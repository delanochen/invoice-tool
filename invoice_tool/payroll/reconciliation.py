"""Live payroll / frozen SL reconciliation. Historical components are immutable."""
from __future__ import annotations
from collections import defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
import re
from flask import abort, flash, g, redirect, render_template, request, url_for
from .tax import money, TAX_CATEGORIES

SYSTEM_SOURCES = {"system_payroll", "system_payroll_adjustment"}
IGNORED_STATUSES = {"cancelled", "rejected"}
HELD_CORRECTIONS = {"correction_pending", "partially_superseded", "superseded"}
ROUNDING_TOLERANCE = Decimal("0.02")
PERIOD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$")


def is_rounding_difference(amount):
    return abs(money(amount)) <= ROUNDING_TOLERANCE


def component_identity(row):
    return (str(row.get("service_date") or ""), row["component_code"],
            int(row.get("work_order_id") or 0))


def component_difference(current, recorded):
    expected, booked, samples = defaultdict(Decimal), defaultdict(Decimal), {}
    for row in current:
        key = component_identity(row)
        expected[key] += money(row["amount"])
        samples[key] = dict(row)
    for row in recorded:
        key = component_identity(row)
        booked[key] += money(row["amount"])
        samples.setdefault(key, dict(row))
    changes = []
    for key in sorted(expected.keys() | booked.keys()):
        amount = money(expected[key] - booked[key])
        if amount:
            line = dict(samples[key])
            line.update(amount=amount, quantity=Decimal("1"), unit="adjustment",
                        unit_rate=amount, source_type="payroll_adjustment", source_id=None)
            changes.append(line)
    return changes


def revision_token(current, orders, recorded):
    def stable(row):
        return {key: str(row.get(key)) for key in (
            "id", "status", "correction_status", "gross_amount", "amount",
            "component_code", "service_date", "work_order_id", "daily_report_id",
            "tax_category", "tax_status_snapshot", "substantiated", "review_status")}
    payload = [[stable(dict(row)) for row in group] for group in (current, orders, recorded)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_reconciliation_services(api):
    def rows(period_start=None, employee_id=None, include_balanced=False):
        db = api["db"]()
        payments = [dict(row) for row in db.execute(
            """select p.*,u.name as employee_name from employee_payment_orders p
               join users u on u.id=p.employee_id
               where p.payment_type='salary' order by p.id""").fetchall()]
        unlinked = {p["employee_id"] for p in payments
                    if p["source_type"] == "manual" and not PERIOD_RE.fullmatch(p["source_number"] or "")
                    and p["status"] not in IGNORED_STATUSES}
        periods = defaultdict(list)
        for payment in payments:
            match = PERIOD_RE.fullmatch(payment["source_number"] or "")
            if match and payment["source_type"] in SYSTEM_SOURCES:
                if period_start and match[1] != str(period_start):
                    continue
                periods[(match[1], match[2])].append(payment)
        output = []
        for (start_text, end_text), original_orders in sorted(periods.items(), reverse=True):
            start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
            actual_end = api["payroll_period_dates"](start)[0]
            cycle = api["payroll_cycle_start_date"]()
            if not (start >= cycle and (start-cycle).days % 14 == 0 and end == actual_end):
                continue
            batch = api["payroll_rows_for_period"](start)
            live = {int(row["worker_id"]): row for row in batch["rows"]}
            workers = set(live) | {int(p["employee_id"]) for p in original_orders}
            replacements = {int(row["employee_id"]) for row in db.execute(
                """select employee_id from employee_salary_agreements
                   where is_active=1 and replaces_system_payroll=1 and effective_from<=?
                   and (effective_to is null or effective_to>=?)""",
                (end_text, start_text)).fetchall()}
            for worker in sorted(workers):
                if employee_id and int(employee_id) != worker or worker in replacements:
                    continue
                all_orders = [p for p in original_orders if int(p["employee_id"]) == worker]
                valid_orders = [p for p in all_orders if p["status"] not in IGNORED_STATUSES
                                and p.get("correction_status", "none") not in HELD_CORRECTIONS]
                current_row = live.get(worker)
                expected = money(current_row["total_pay"] if current_row else 0)
                recorded_total = money(sum((money(p["gross_amount"]) for p in valid_orders), Decimal(0)))
                paid = money(sum((money(p["gross_amount"]) for p in valid_orders
                                  if p["status"] in {"paid", "reconciled"}), Decimal(0)))
                delta = money(expected-recorded_total)
                rounding_only = is_rounding_difference(delta)
                if rounding_only and not include_balanced:
                    continue
                current, _ = api["payroll_payment_components"](start, worker, expected)
                current = [dict(row) for row in current]
                marks = ",".join("?" for _ in valid_orders)
                recorded = []
                if marks:
                    recorded = [dict(row) for row in db.execute(
                        f"""with {api['correction_component_cte']()}
                            select c.* from effective_components c
                            where c.effective_payment_order_id in ({marks}) order by c.id""",
                        [p["id"] for p in valid_orders]).fetchall()]
                changes = component_difference(current, recorded)
                snapshot_complete = money(sum((money(c["amount"]) for c in recorded), Decimal(0))) == recorded_total
                reason = ""
                status = "supplement"
                status_label = "可生成补发"
                if not valid_orders and abs(expected) > ROUNDING_TOLERANCE:
                    status = "unbilled"
                    status_label = "尚未入单"
                    reason = "当前工资尚未形成有效SL，请先确认原工资单状态，不直接生成补发草稿。"
                elif any(p.get("correction_status", "none") in HELD_CORRECTIONS for p in all_orders):
                    status = "historical_change"
                    status_label = "历史纠偏待处理"
                    reason = "原工资存在待处理的历史纠偏，请先完成纠偏。"
                elif not snapshot_complete:
                    status = "source_mismatch"
                    status_label = "历史来源异常"
                    reason = "原SL金额与有效组件快照不一致，需要人工核对来源。"
                elif worker in unlinked:
                    status = "manual_conflict"
                    status_label = "手工SL冲突"
                    reason = "存在未关联工资周期的手工SL，请先核对，避免重复补发。"
                elif delta < -ROUNDING_TOLERANCE or any(c["amount"] < -ROUNDING_TOLERANCE for c in changes):
                    status = "negative_review"
                    status_label = "负差额待审核"
                    reason = "当前工资低于原SL或存在组件抵减，需要人工审核；系统不会自动扣款。"
                if status == "supplement" and any(
                        p.get("correction_status", "none") == "replacement" for p in valid_orders):
                    status_label = "历史工资已变更 · 正差额可补发"
                carry = api.get("flag_carry_forward_components")
                if carry and carry(worker, current, start_text, end_text):
                    status = "historical_change"
                    status_label = "历史结转待核对"
                    reason = "涉及历史纠偏结转组件，请先核对原SL归属。"
                if any(int(p["employee_id"]) == worker and p["source_type"] not in SYSTEM_SOURCES
                       and p["source_number"] == start_text+"~"+end_text
                       and p["status"] not in IGNORED_STATUSES for p in payments):
                    status = "manual_conflict"
                    status_label = "本周期另有手工SL"
                    reason = "本周期另有手工或约定工资SL，需要人工核对。"
                if money(sum((c["amount"] for c in changes), Decimal(0))) != delta:
                    status = "source_mismatch"
                    status_label = "组件未闭合"
                    reason = "差额与工资组件未闭合，需要人工核对来源。"
                if rounding_only:
                    status = "rounding"
                    status_label = "舍入误差"
                    reason = "差额在 ±$0.02 容差内，按舍入误差处理。"
                name = current_row["worker_name"] if current_row else all_orders[0]["employee_name"]
                output.append(dict(employee_id=worker, employee_name=name,
                    period_start=start_text, period_end=end_text,
                    expected=expected, recorded=recorded_total, paid=paid,
                    difference=delta, raw_difference=delta, orders=valid_orders, changes=changes,
                    revision=revision_token(current, all_orders, recorded),
                    snapshot_complete=snapshot_complete, status=status, status_label=status_label,
                    rounding_only=rounding_only, blocked_reason=reason,
                    can_create=status == "supplement" and delta > ROUNDING_TOLERANCE))
        return output

    def notify(actor="", event="", previous=None):
        db = api["db"]()
        db.begin_write()
        recipients = db.execute(
            "select id from users where is_active=1 and role in ('admin','finance')").fetchall()
        for row in rows():
            if previous is not None and previous.get((row["employee_id"], row["period_start"])) == row["revision"]:
                continue
            affected_days = ", ".join(sorted({line["service_date"] for line in row["changes"] if line.get("service_date")}))
            link = ("/employee-payments/payroll-reconciliation?period_start="+row["period_start"]
                    +"&employee_id="+str(row["employee_id"])+"&revision="+row["revision"])
            db.execute("select pg_advisory_xact_lock(hashtextextended(?,0))", ("payroll-alert:"+link,))
            for recipient in recipients:
                if db.execute("select id from messages where user_id=? and link=? limit 1",
                              (recipient["id"], link)).fetchone():
                    continue
                body = (f"{actor or '系统核对'} {event}；受影响工作日期：{affected_days or '周期项目'}；{row['employee_name']} "
                        f"{row['period_start']} 至 {row['period_end']}："
                        f"当前应付 USD {row['expected']}，已入单 USD {row['recorded']}，"
                        f"已支付工资金额 USD {row['paid']}，待处理差额 USD {row['difference']}。"
                        "请打开差额核对页确认；系统未自动更改或发放工资。")
                api["create_message"](recipient["id"], "工资变动待核对", body, link)
    return rows, notify


def install_payroll_reconciliation(app, api):
    rows, notify = build_reconciliation_services(api)
    api["payroll_reconciliation_rows"] = rows

    def require(action="view"):
        if api["normalized_role"]() not in {"admin", "manager", "finance"}:
            abort(403)
        if not api["has_action_permission"]("employee_payments", action):
            abort(403)

    @app.get("/employee-payments/payroll-reconciliation")
    @api["login_required"]
    def payroll_reconciliation():
        require()
        period = request.args.get("period_start", "").strip()
        worker = request.args.get("employee_id", "").strip()
        if period:
            try:
                date.fromisoformat(period)
            except ValueError:
                abort(400)
        if worker and not worker.isdigit():
            abort(400)
        pending = rows(period or None, worker or None)
        manual = api["db"]().execute(
            """select p.id,p.payment_number,u.name as employee_name,p.gross_amount
               from employee_payment_orders p join users u on u.id=p.employee_id
               where p.payment_type='salary' and p.source_type='manual'
                 and p.status not in ('cancelled','rejected')
                 and p.source_number='' order by p.id""").fetchall()
        return render_template("payroll_reconciliation.html", reconciliation_rows=pending,
                               unlinked_manual=manual)

    @app.post("/employee-payments/payroll-reconciliation/confirm")
    @api["login_required"]
    def confirm_payroll_reconciliation():
        require("create")
        db = api["db"]()
        try:
            db.begin_write()
            worker = int(request.form["employee_id"])
            start = date.fromisoformat(request.form["period_start"])
            db.execute("select id from users where id=? for update", (worker,)).fetchone()
            matches = rows(start.isoformat(), worker)
            if len(matches) != 1:
                raise ValueError("差额已处理或来源已变化，请刷新核对页。")
            row = matches[0]
            if row["revision"] != request.form.get("revision"):
                raise ValueError("日报或付款单已变化，请刷新核对后重试。")
            if not row["can_create"]:
                raise ValueError(row["blocked_reason"] or "该差额不能自动生成补发。")
            from employee_finance import _insert_payment
            payment_id, inserted = _insert_payment(api, employee_id=worker,
                payment_type="salary", gross_amount=row["difference"],
                source_type="system_payroll_adjustment",
                source_number=row["period_start"]+"~"+row["period_end"],
                source_key=f"payroll:adjustment:{worker}:{row['period_start']}:{row['revision']}",
                description=(f"{row['period_start']} 至 {row['period_end']} 工资差额补发；"
                             "原SL："+", ".join(p["payment_number"] for p in row["orders"])))
            if not inserted:
                raise ValueError("此差额已经生成补发草稿，请刷新核对页。")
            totals = {key: Decimal(0) for key in TAX_CATEGORIES}
            for line in row["changes"]:
                totals[line["tax_category"]] += line["amount"]
                db.execute(
                    """insert into employee_payment_components
                    (payment_order_id,employee_id,component_code,component_name,amount,
                     quantity,unit,unit_rate,service_date,work_order_id,source_type,source_id,
                     daily_report_id,tax_category,tax_status_snapshot,substantiated,review_status,created_at)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (payment_id, worker, line["component_code"], line["component_name"], line["amount"],
                     line["quantity"], line["unit"], line["unit_rate"], line["service_date"],
                     line["work_order_id"], "payroll_adjustment", None, line.get("daily_report_id"),
                     line["tax_category"], line["tax_status_snapshot"], line["substantiated"],
                     line["review_status"], api["now"]()))
            db.execute(
                """update employee_payment_orders set taxable_compensation_total=?,
                   accountable_reimbursement_total=?,tax_review_required_total=? where id=?""",
                (totals["taxable_compensation"], totals["accountable_reimbursement"],
                 totals["tax_review_required"], payment_id))
            for original in row["orders"]:
                db.execute(
                    """insert into payment_order_sources
                       (payment_order_id,source_type,source_id,source_number,amount,created_at)
                       values (?,'payroll_original',?,?,0,?)""",
                    (payment_id, original["id"], original["payment_number"], api["now"]()))
            api["log_action"]("reconcile", "employee_payment", payment_id, str(payment_id),
                              f"工资差额确认 {row['revision']}；补发 {row['difference']}")
            db.commit()
            flash("工资差额已生成SL补发草稿，仍需正常审核付款。", "success")
            return redirect(url_for("employee_payment_detail", payment_id=payment_id))
        except (ValueError, TypeError, KeyError) as error:
            db.rollback()
            flash(str(error), "error")
            return redirect(url_for("payroll_reconciliation"))

    # Existing correction gates stay authoritative. Reconciliation adds a hold
    # until late-report differences are acknowledged, including after a draft
    # supplement itself becomes stale.
    correction_gate = api.get("payment_batch_block_reason")
    def payroll_payment_change_reason(payment):
        payment = dict(payment)
        match = PERIOD_RE.fullmatch(payment.get("source_number") or "")
        if payment.get("payment_type") != "salary" or payment.get("source_type") not in SYSTEM_SOURCES or not match:
            return ""
        if payment.get("correction_status", "none") != "none":
            return ""
        pending = rows(match[1], payment["employee_id"])
        return "工资存在未核对差额，请先到工资差额核对页处理。" if any(row["snapshot_complete"] for row in pending) else ""
    def combined_gate(payment):
        return (correction_gate(payment) if correction_gate else None) or payroll_payment_change_reason(payment) or None
    api["payment_batch_block_reason"] = combined_gate
    api["payroll_payment_change_reason"] = payroll_payment_change_reason

    original_payload = api["payroll_batch_payload"]
    def payload_with_reconciliation(start, batch_type="regular"):
        payload = original_payload(start, batch_type)
        if (batch_type == "regular" and api["normalized_role"]() in {"admin","manager","finance"}
                and api["has_action_permission"]("employee_payments","view")):
            payload["reconciliation"] = [
                {key: (float(row[key]) if isinstance(row[key], Decimal) else row[key])
                 for key in ("employee_id","employee_name","expected","recorded","paid","difference")}
                for row in rows(str(start), include_balanced=True)]
        return payload
    api["payroll_batch_payload"] = payload_with_reconciliation

    def report_write_request():
        return request.method == "POST" and request.endpoint in {
            "new_service_report", "edit_service_report", "delete_service_report",
            "ai_daily_report_confirm"}
    @app.before_request
    def payroll_before_report_write():
        if report_write_request() and getattr(g, "user", None):
            try:
                g.payroll_before_report = {
                    (row["employee_id"], row["period_start"]): row["revision"] for row in rows()}
            except Exception:
                app.logger.exception("Could not capture payroll revision before report save")

    @app.after_request
    def payroll_change_notice(response):
        if (report_write_request() and response.status_code < 400
                and getattr(g, "user", None) and hasattr(g, "payroll_before_report")):
            try:
                report_id = (request.view_args or {}).get("report_id")
                if not report_id:
                    match = re.search(r"/service-reports/(\d+)", response.headers.get("Location", ""))
                    report_id = int(match[1]) if match else None
                events = {"new_service_report":"新增日报","edit_service_report":"修改日报",
                          "delete_service_report":"删除日报","ai_daily_report_confirm":"确认AI日报"}
                event = events[request.endpoint]+(f" 日报#{report_id}" if report_id else "")
                notify(g.user["name"], event, previous=g.payroll_before_report)
                api["db"]().commit()
            except Exception:
                api["db"]().rollback()
                app.logger.exception("Payroll notice failed; live reconciliation queue remains available")
        return response
    return rows
