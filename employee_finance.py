"""PostgreSQL-backed employee payments, treasury, advances and assets.

The module deliberately sits after the existing payroll/expense calculations:
those systems remain the source of truth for amounts, while payment orders are
the accounts-payable document and bank transactions are the cash evidence.
Schema changes live in the numbered PostgreSQL migrations (0285 and later).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from flask import Response, abort, flash, g, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename


PAYMENT_STATUS_LABELS = {
    "draft": "草稿", "pending_review": "待审核", "approved": "已批准",
    "pending_payment": "待付款", "paid": "已付款", "reconciled": "已对账",
    "rejected": "已驳回", "cancelled": "已取消", "payment_failed": "付款失败",
}
PAYMENT_TYPE_LABELS = {
    "salary": "工资", "expense": "员工报销",
}
PAYMENT_METHOD_LABELS = {
    "ach": "ACH", "wire": "电汇", "check": "支票", "cash": "现金", "other": "其他",
}
# v0.1.350：合并发放。审核通过后不再一张一张发，而是把同一员工的多张付款单
# 合成一张支票（付款批次 PB-YYMM-NNNN）一次性发放。
PAYMENT_BATCH_STATUS_LABELS = {
    "issued": "已发放", "reconciled": "已对账", "void": "已作废",
}
BATCH_NUMBER_PREFIX = "PB"
# 能进批次的付款单状态：已批准（approved）直接合并发放时，会先补做借款抵扣
# 再置为已付款，省掉「先进入待付款」这一步。
BATCHABLE_PAYMENT_STATUSES = ("approved", "pending_payment")
ASSET_STATUS_LABELS = {
    "available": "可领用", "assigned": "使用中", "damaged": "损坏",
    "lost": "丢失", "retired": "已报废",
}
ASSET_EVENT_LABELS = {
    "created": "建档", "assign": "领用", "return": "归还", "damaged": "损坏",
    "lost": "丢失", "retired": "报废", "updated": "更新",
}


def _money(value, field="金额") -> Decimal:
    try:
        amount = Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field}格式不正确。")
    return amount


def _csv_value(row, *names, default=""):
    """Read a CSV value using either the English template or Chinese headings."""
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _csv_rows(upload):
    if not upload or not upload.filename:
        raise ValueError("请选择 CSV 文件。")
    text = io.TextIOWrapper(upload.stream, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)
    if not reader.fieldnames:
        raise ValueError("CSV 文件缺少表头。")
    rows = list(reader)
    if not rows:
        raise ValueError("CSV 文件没有可导入的数据。")
    return rows


def payment_amounts(gross_amount, advance_offset=0, other_adjustment=0):
    """Return normalized (gross, offset, adjustment, net) with domain checks."""
    gross = _money(gross_amount, "应付金额")
    offset = _money(advance_offset, "借款抵扣")
    adjustment = _money(other_adjustment, "其他调整")
    if gross < 0 or offset < 0:
        raise ValueError("应付金额和借款抵扣不能为负数。")
    net = gross - offset + adjustment
    if net < 0:
        raise ValueError("实付金额不能为负数。")
    return gross, offset, adjustment, net


def payment_transition_target(status, action):
    transitions = {
        ("draft", "submit"): "pending_review",
        ("pending_review", "approve"): "approved",
        ("pending_review", "reject"): "rejected",
        ("approved", "ready"): "pending_payment",
        ("pending_payment", "mark_paid"): "paid",
        ("pending_payment", "fail"): "payment_failed",
        ("payment_failed", "retry"): "pending_payment",
        ("paid", "reconcile"): "reconciled",
    }
    return transitions.get((status, action))


def _require(api, resource, action="view"):
    if not api["has_action_permission"](resource, action):
        abort(403)


def _can_view_all_payments(api):
    return api["normalized_role"]() in {"admin", "manager", "finance"}


def _require_payment_access(api, payment):
    if not _can_view_all_payments(api) and payment["employee_id"] != g.user["id"]:
        abort(403)


# v0.1.343：员工付款单按业务类型分前缀 —— 工资走 SL-，员工报销走 ER-。
# 以前两类共用 EP- 前缀 + 同一个流水池（工资 EP-2609-0001~0007、报销接着 0008
# 往下排），光看单号分不清是工资还是报销；历史迁移还留下过第三种
# EP-MIG-EXP-<id> 格式。现在与全站其它单据（SO/EX/CT/PP）一致：一类一前缀。
PAYMENT_NUMBER_PREFIXES = {
    "salary": "SL",    # 工资
    "expense": "ER",   # 员工报销
}


def _next_number(api, prefix, table, column):
    api["lock_number_allocation"](api["db"]())
    stamp = date.today().strftime("%y%m")
    root = f"{prefix}-{stamp}-"
    # 取同前缀同月份里**流水号最大**的那条，不能 `order by id desc` ——
    # 存量改号后 id 顺序与流水顺序不再一致，按 id 取会让新号与旧号撞车。
    rows = api["db"]().execute(
        f"select {column} from {table} where {column} like ?", (root + "%",)
    ).fetchall()
    sequence = 1
    for row in rows:
        try:
            value = int(str(row[column]).rsplit("-", 1)[1])
        except (ValueError, IndexError):
            continue
        if value >= sequence:
            sequence = value + 1
    return f"{root}{sequence:04d}"


def _payment_order(api, payment_id):
    row = api["db"]().execute(
        """
        select p.*, u.name employee_name, a.account_name, a.bank_name,
               creator.name creator_name, reviewer.name reviewer_name, payer.name payer_name
        from employee_payment_orders p
        join users u on u.id=p.employee_id
        left join bank_accounts a on a.id=p.bank_account_id
        left join users creator on creator.id=p.created_by
        left join users reviewer on reviewer.id=p.reviewed_by
        left join users payer on payer.id=p.paid_by
        where p.id=?
        """, (payment_id,)
    ).fetchone()
    if not row:
        abort(404)
    return row


def _advance_balances(api, employee_id=None):
    clauses, params = ["1=1"], []
    if employee_id:
        clauses.append("a.employee_id=?")
        params.append(employee_id)
    return api["db"]().execute(
        f"""
        select a.*, u.name employee_name, b.account_name,
               coalesce(sum(l.amount),0) applied_amount,
               a.principal_amount-coalesce(sum(l.amount),0) remaining_amount
        from employee_advances a join users u on u.id=a.employee_id
        left join bank_accounts b on b.id=a.bank_account_id
        left join employee_advance_applications l on l.advance_id=a.id
        where {' and '.join(clauses)}
        group by a.id,u.name,b.account_name
        order by a.advance_date desc,a.id desc
        """, params
    ).fetchall()


def employee_finance_summary(api, employee_id):
    """Small, read-only employee profile summary used by the existing user page."""
    pending = api["db"]().execute(
        "select coalesce(sum(net_amount),0) total,count(*) count from employee_payment_orders "
        "where employee_id=? and status in ('pending_review','approved','pending_payment','payment_failed')",
        (employee_id,),
    ).fetchone()
    paid = api["db"]().execute(
        "select coalesce(sum(net_amount),0) total,count(*) count from employee_payment_orders "
        "where employee_id=? and status in ('paid','reconciled')", (employee_id,),
    ).fetchone()
    advances = _advance_balances(api, employee_id)
    assets = api["db"]().execute(
        "select id,asset_number,name,status from assets where current_holder_id=? and status='assigned' order by asset_number",
        (employee_id,),
    ).fetchall()
    return {
        "pending_total": pending["total"], "pending_count": pending["count"],
        "paid_total": paid["total"], "paid_count": paid["count"],
        "advance_balance": sum(Decimal(str(row["remaining_amount"] or 0)) for row in advances),
        "assets": assets,
    }


def _insert_payment(api, *, employee_id, payment_type, gross_amount, source_type,
                    source_id=None, source_number="", source_key="", description="",
                    other_adjustment=Decimal("0"), advance_id=None, advance_offset=Decimal("0")):
    if payment_type not in PAYMENT_TYPE_LABELS:
        raise ValueError("付款单类型只能是工资或员工报销。")
    gross, offset, adjustment, net = payment_amounts(gross_amount, advance_offset, other_adjustment)
    if offset and not advance_id:
        raise ValueError("填写借款抵扣时必须选择员工借款。")
    if advance_id:
        advance = api["db"]().execute(
            "select employee_id from employee_advances where id=?", (advance_id,)
        ).fetchone()
        if not advance or advance["employee_id"] != employee_id:
            raise ValueError("所选借款不属于该员工。")
    if source_key:
        existing = api["db"]().execute(
            "select id from employee_payment_orders where source_key=?", (source_key,)
        ).fetchone()
        if existing:
            return existing["id"], False
    # v0.1.343：按业务类型选前缀（工资 SL-、员工报销 ER-），各自独立流水池。
    number = _next_number(api, PAYMENT_NUMBER_PREFIXES[payment_type],
                          "employee_payment_orders", "payment_number")
    created_at = api["now"]()
    cursor = api["db"]().execute(
        """
        insert into employee_payment_orders
        (payment_number,employee_id,payment_type,status,currency,gross_amount,advance_offset,
         other_adjustment,net_amount,primary_advance_id,description,source_type,source_id,
         source_number,source_key,sync_source,sync_status,created_by,created_at,updated_at)
        values (?,?,?,'draft','USD',?,?,?,?,?,?,?,?,?,?,'manual','local',?,?,?)
        """,
        (number, employee_id, payment_type, gross, offset, adjustment, net, advance_id,
         description, source_type, source_id, source_number, source_key or None,
         g.user["id"], created_at, created_at),
    )
    payment_id = cursor.lastrowid
    api["db"]().execute(
        "insert into payment_order_sources(payment_order_id,source_type,source_id,source_number,amount,created_at) values(?,?,?,?,?,?)",
        (payment_id, source_type, source_id, source_number, gross, created_at),
    )
    api["db"]().execute(
        "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,?,'','draft',?,?,?)",
        (payment_id, "created", description, g.user["id"], created_at),
    )
    api["log_action"]("create", "employee_payment", payment_id, number, f"{PAYMENT_TYPE_LABELS[payment_type]} {net}")
    return payment_id, True


def ensure_expense_payment_order(api, expense_id):
    """Idempotently bridge an already-approved expense into accounts payable."""
    expense = api["db"]().execute(
        "select *,coalesce(beneficiary_id,created_by) employee_id from expenses where id=?", (expense_id,)
    ).fetchone()
    if not expense or expense["status"] != "approved":
        return None
    existing = api["db"]().execute(
        "select id,status,payment_number from employee_payment_orders where source_key=?",
        (f"expense:{expense_id}",),
    ).fetchone()
    if existing:
        if existing["status"] in {"cancelled", "rejected"}:
            reopened_at = api["now"]()
            api["db"]().execute(
                """update employee_payment_orders
                   set employee_id=?,status='draft',gross_amount=?,advance_offset=0,
                       other_adjustment=0,net_amount=?,primary_advance_id=null,
                       bank_account_id=null,payment_method=null,external_transaction_id=null,
                       reviewed_by=null,reviewed_at=null,paid_by=null,paid_at=null,
                       description=?,source_number=?,updated_at=?
                   where id=?""",
                (expense["employee_id"], expense["amount"], expense["amount"],
                 f"报销单 {expense['expense_number']}", expense["expense_number"],
                 reopened_at, existing["id"]),
            )
            api["db"]().execute(
                "update payment_order_sources set source_number=?,amount=? where payment_order_id=? and source_type='expense' and source_id=?",
                (expense["expense_number"], expense["amount"], existing["id"], expense_id),
            )
            api["db"]().execute(
                "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,'reopen',?,'draft','报销重新审核通过',?,?)",
                (existing["id"], existing["status"], g.user["id"], reopened_at),
            )
            api["log_action"](
                "reopen", "employee_payment", existing["id"], existing["payment_number"],
                "报销重新审核通过，付款单恢复为草稿",
            )
        return existing["id"]
    payment_id, _ = _insert_payment(
        api, employee_id=expense["employee_id"], payment_type="expense",
        gross_amount=expense["amount"], source_type="expense", source_id=expense_id,
        source_number=expense["expense_number"], source_key=f"expense:{expense_id}",
        description=f"报销单 {expense['expense_number']}",
    )
    return payment_id


def cancel_expense_payment_order(api, expense_id, reason="报销审核流程已重置"):
    """Cancel an unpaid expense payment and reverse any posted advance offset."""
    payment = api["db"]().execute(
        "select * from employee_payment_orders where source_key=?",
        (f"expense:{expense_id}",),
    ).fetchone()
    if not payment:
        return None
    if payment["status"] in {"paid", "reconciled"}:
        raise ValueError("该报销付款单已经发放，不能重置审核流程。")
    if payment["status"] == "cancelled":
        return payment["id"]
    _reverse_advance_application(api, payment)
    changed_at = api["now"]()
    api["db"]().execute(
        "update employee_payment_orders set status='cancelled',updated_at=? where id=?",
        (changed_at, payment["id"]),
    )
    api["db"]().execute(
        "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,'cancel',?,'cancelled',?,?,?)",
        (payment["id"], payment["status"], reason, g.user["id"], changed_at),
    )
    api["log_action"](
        "cancel", "employee_payment", payment["id"], payment["payment_number"], reason,
    )
    return payment["id"]


def _create_advance_application(api, payment):
    if not payment["primary_advance_id"] or not Decimal(str(payment["advance_offset"] or 0)):
        return
    api["db"]().execute("begin immediate")
    exists = api["db"]().execute(
        "select id from employee_advance_applications where payment_order_id=? and entry_type='application'",
        (payment["id"],),
    ).fetchone()
    if exists:
        return
    balances = {row["id"]: Decimal(str(row["remaining_amount"])) for row in _advance_balances(api, payment["employee_id"])}
    amount = Decimal(str(payment["advance_offset"]))
    if balances.get(payment["primary_advance_id"], Decimal("0")) < amount:
        raise ValueError("借款未结余额不足以完成本次抵扣。")
    api["db"]().execute(
        "insert into employee_advance_applications(advance_id,payment_order_id,entry_type,amount,notes,created_by,created_at) values(?,?,'application',?,?,?,?)",
        (payment["primary_advance_id"], payment["id"], amount, f"付款单 {payment['payment_number']} 抵扣", g.user["id"], api["now"]()),
    )


def _reverse_advance_application(api, payment):
    applied = api["db"]().execute(
        "select coalesce(sum(amount),0) amount from employee_advance_applications where payment_order_id=?",
        (payment["id"],),
    ).fetchone()["amount"]
    applied = Decimal(str(applied or 0))
    if applied:
        api["db"]().execute(
            "insert into employee_advance_applications(advance_id,payment_order_id,entry_type,amount,notes,created_by,created_at) values(?,?,'reversal',?,?,?,?)",
            (payment["primary_advance_id"], payment["id"], -applied, f"付款单 {payment['payment_number']} 取消冲销", g.user["id"], api["now"]()),
        )


def _settle_expense_payout(api, payment, extra=""):
    """付款单发放后回写来源报销单（单张发放与批次发放共用同一条回写口径）。

    extra 会追加到通知正文末尾（批次发放时用来说明是哪张支票），不传则与
    原来的单张发放文案完全一致。
    """
    if payment["source_type"] != "expense" or not payment["source_id"]:
        return None
    expense = api["db"]().execute("select * from expenses where id=?", (payment["source_id"],)).fetchone()
    api["db"]().execute(
        "update expenses set payout_status='paid',reimbursed_by=?,reimbursed_at=?,updated_at=? where id=?",
        (g.user["id"], api["now"](), api["now"](), payment["source_id"]),
    )
    if expense and "notify_expense_participants" in api:
        note = f"报销 {expense['expense_number']} 已通过付款单 {payment['payment_number']} 发放。"
        api["notify_expense_participants"](
            expense, "报销付款已发放", f"{note}{extra}",
            url_for("employee_payment_detail", payment_id=payment["id"]),
        )
    if expense:
        api["log_action"]("pay", "expense", expense["id"], expense["expense_number"],
                          f"通过付款单 {payment['payment_number']} 发放")
    return expense


def _release_expense_payout(api, payment):
    """批次作废：来源报销单退回未付款（与报销审核重置同一口径）。"""
    if payment["source_type"] != "expense" or not payment["source_id"]:
        return
    api["db"]().execute(
        "update expenses set payout_status='pending',reimbursed_by=null,reimbursed_at=null,updated_at=? where id=?",
        (api["now"](), payment["source_id"]),
    )


def _load_batch(api, batch_id):
    """读取批次并按员工校验可见性（员工本人只能看自己的批次）。"""
    batch = api["db"]().execute(
        """select b.*, u.name employee_name, a.account_name
           from employee_payment_batches b
           join users u on u.id=b.employee_id
           left join bank_accounts a on a.id=b.bank_account_id
           where b.id=?""", (batch_id,)
    ).fetchone()
    if not batch:
        abort(404)
    _require_payment_access(api, batch)
    return batch


def _batch_orders(api, batch_id):
    return api["db"]().execute(
        """select p.*,u.name employee_name from employee_payment_orders p
           join users u on u.id=p.employee_id where p.batch_id=? order by p.id""", (batch_id,)
    ).fetchall()


def register_employee_finance_routes(app, api):
    app.jinja_env.globals["employee_finance_summary"] = lambda employee_id: employee_finance_summary(api, employee_id)

    @app.get("/employee-payments")
    @api["login_required"]
    def employee_payments():
        _require(api, "employee_payments")
        status = request.args.get("status", "")
        employee_id = request.args.get("employee_id", "")
        clauses, params = ["1=1"], []
        can_view_all = _can_view_all_payments(api)
        if not can_view_all:
            clauses.append("p.employee_id=?")
            params.append(g.user["id"])
        if status in PAYMENT_STATUS_LABELS:
            clauses.append("p.status=?"); params.append(status)
        if can_view_all and employee_id.isdigit():
            clauses.append("p.employee_id=?"); params.append(int(employee_id))
        elif not can_view_all:
            employee_id = str(g.user["id"])
        rows = api["db"]().execute(
            f"""select p.*,u.name employee_name,b.account_name from employee_payment_orders p
            join users u on u.id=p.employee_id left join bank_accounts b on b.id=p.bank_account_id
            where {' and '.join(clauses)} order by p.created_at desc,p.id desc""", params
        ).fetchall()
        employees = (
            api["db"]().execute("select id,name from users where role in ('employee','manager','finance','admin') order by name").fetchall()
            if can_view_all else api["db"]().execute("select id,name from users where id=?", (g.user["id"],)).fetchall()
        )
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall() if can_view_all else []
        advances = _advance_balances(api, int(employee_id) if employee_id.isdigit() else None) if can_view_all else []
        agreements = api["db"]().execute(
            "select s.*,u.name employee_name from employee_salary_agreements s join users u on u.id=s.employee_id order by s.is_active desc,s.effective_from desc"
        ).fetchall() if can_view_all else []
        return render_template("employee_payments.html", rows=rows, employees=employees, accounts=accounts,
                               advances=advances, agreements=agreements, status=status, employee_id=employee_id,
                               status_labels=PAYMENT_STATUS_LABELS, type_labels=PAYMENT_TYPE_LABELS,
                               method_labels=PAYMENT_METHOD_LABELS, can_view_all=can_view_all)

    @app.post("/employee-payments/create")
    @api["login_required"]
    def create_employee_payment():
        _require(api, "employee_payments", "create")
        try:
            employee_id = int(request.form.get("employee_id", ""))
            payment_type = request.form.get("payment_type", "salary")
            if payment_type != "salary":
                raise ValueError("员工报销付款单只能由报销审核通过后自动生成。")
            payment_id, _ = _insert_payment(
                api, employee_id=employee_id, payment_type=payment_type,
                gross_amount=request.form.get("gross_amount"), source_type="manual",
                description=request.form.get("description", "").strip(),
                other_adjustment=request.form.get("other_adjustment", "0"),
                advance_id=int(request.form["advance_id"]) if request.form.get("advance_id", "").isdigit() else None,
                advance_offset=request.form.get("advance_offset", "0"),
            )
            api["db"]().commit()
            flash("员工付款单已创建。", "success")
            return redirect(url_for("employee_payment_detail", payment_id=payment_id))
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("employee_payments"))

    @app.post("/employee-payments/salary-agreements")
    @api["login_required"]
    def create_salary_agreement():
        _require(api, "employee_payments", "create")
        try:
            employee_id = int(request.form.get("employee_id", ""))
            amount = _money(request.form.get("amount"), "约定工资")
            effective_from = date.fromisoformat(request.form.get("effective_from", "")).isoformat()
            effective_to = request.form.get("effective_to", "").strip() or None
            if amount < 0: raise ValueError("约定工资不能为负数。")
            if effective_to and effective_to < effective_from: raise ValueError("结束日期不能早于生效日期。")
            overlap = api["db"]().execute(
                "select id from employee_salary_agreements where employee_id=? and is_active=1 and effective_from<=? and (effective_to is null or effective_to>=?) limit 1",
                (employee_id, effective_to or "9999-12-31", effective_from),
            ).fetchone()
            if overlap: raise ValueError("该员工已有生效期重叠的线下工资约定。")
            api["db"]().execute(
                "insert into employee_salary_agreements(employee_id,amount,currency,effective_from,effective_to,replaces_system_payroll,notes,is_active,created_by,created_at,updated_at) values(?,?,'USD',?,?,?, ?,1,?,?,?)",
                (employee_id, amount, effective_from, effective_to,
                 1 if request.form.get("replaces_system_payroll", "1") == "1" else 0,
                 request.form.get("notes", "").strip(), g.user["id"], api["now"](), api["now"]()),
            )
            api["log_action"]("create", "employee_salary_agreement", employee_id, str(employee_id), f"约定工资 {amount}")
            api["db"]().commit(); flash("线下约定工资已保存。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("employee_payments"))

    @app.post("/employee-payments/generate-payroll")
    @api["login_required"]
    def generate_payroll_payments():
        _require(api, "employee_payments", "create")
        try:
            period_start = date.fromisoformat(request.form.get("period_start", ""))
            batch = api["payroll_rows_for_period"](period_start)
            agreements = api["db"]().execute(
                "select * from employee_salary_agreements where is_active=1 and effective_from<=? and (effective_to is null or effective_to>=?)",
                (batch["period_end"].isoformat(), period_start.isoformat()),
            ).fetchall()
            replacements = {row["employee_id"]: row for row in agreements if row["replaces_system_payroll"]}
            created = 0

            def _write_components(payment_id, employee_id, components, totals, gross_amount):
                """组件快照与三项税务合计与付款单同事务落库（失败整体回滚）。

                写入前做最后一道 sum(components) == gross 硬校验（ROUND_HALF_UP），
                任何不一致都拒绝写快照并回滚整个生成操作。
                """
                gross = _money(gross_amount)
                line_sum = sum((line["amount"] for line in components), Decimal("0"))
                if line_sum != gross or sum(totals.values()) != gross:
                    raise ValueError(
                        "工资组件合计 %s / 税务合计 %s 与付款单金额 %s 不一致，已中止生成。"
                        % (line_sum, sum(totals.values()), gross)
                    )
                db = api["db"]()
                for line in components:
                    db.execute(
                        """
                        insert into employee_payment_components
                        (payment_order_id,employee_id,component_code,component_name,amount,
                         quantity,unit,unit_rate,service_date,work_order_id,source_type,source_id,
                         daily_report_id,tax_category,tax_status_snapshot,substantiated,review_status,created_at)
                        values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (payment_id, employee_id, line["component_code"], line["component_name"],
                         line["amount"], line["quantity"], line["unit"], line["unit_rate"],
                         line["service_date"], line["work_order_id"], line["source_type"],
                         line["source_id"], line["daily_report_id"], line["tax_category"],
                         line["tax_status_snapshot"], line["substantiated"], line["review_status"],
                         api["now"]()),
                    )
                db.execute(
                    "update employee_payment_orders set taxable_compensation_total=?,"
                    "accountable_reimbursement_total=?,tax_review_required_total=? where id=?",
                    (totals["taxable_compensation"], totals["accountable_reimbursement"],
                     totals["tax_review_required"], payment_id),
                )

            for row in batch["rows"]:
                agreement = replacements.get(row["worker_id"])
                amount = agreement["amount"] if agreement else row["total_pay"]
                source_kind = "offline_salary" if agreement else "system_payroll"
                # Phase 2A：生成前先算组件并做 sum==gross 硬校验（失败抛 ValueError
                # → 下方 except 回滚整个生成操作），绝不写不完整快照、不静默补差。
                if source_kind == "system_payroll":
                    components, totals = api["payroll_payment_components"](
                        period_start, row["worker_id"], amount)
                else:
                    components, totals = api["offline_salary_payment_components"](
                        row["worker_id"], amount, batch["period_end"])
                payment_id, inserted = _insert_payment(
                    api, employee_id=row["worker_id"], payment_type="salary", gross_amount=amount,
                    source_type=source_kind, source_id=agreement["id"] if agreement else None,
                    source_number=f"{period_start.isoformat()}~{batch['period_end'].isoformat()}",
                    source_key=f"payroll:{source_kind}:{row['worker_id']}:{period_start.isoformat()}",
                    description=f"{period_start.isoformat()} 至 {batch['period_end'].isoformat()} {'线下约定工资' if agreement else '系统工资'}",
                )
                if inserted:
                    _write_components(payment_id, row["worker_id"], components, totals, amount)
                created += int(inserted)
            # Offline-only employees may have no service-report row in this period.
            worker_ids = {row["worker_id"] for row in batch["rows"]}
            for agreement in agreements:
                if agreement["employee_id"] in worker_ids and agreement["replaces_system_payroll"]:
                    continue
                payment_id, inserted = _insert_payment(
                    api, employee_id=agreement["employee_id"], payment_type="salary",
                    gross_amount=agreement["amount"], source_type="offline_salary", source_id=agreement["id"],
                    source_number=f"{period_start.isoformat()}~{batch['period_end'].isoformat()}",
                    source_key=f"payroll:offline_salary:{agreement['employee_id']}:{period_start.isoformat()}",
                    description=f"{period_start.isoformat()} 至 {batch['period_end'].isoformat()} 线下约定工资",
                )
                if inserted:
                    components, totals = api["offline_salary_payment_components"](
                        agreement["employee_id"], agreement["amount"], batch["period_end"])
                    _write_components(payment_id, agreement["employee_id"], components, totals, agreement["amount"])
                created += int(inserted)
            api["db"]().commit(); flash(f"已生成 {created} 张付款单；重复来源已自动跳过。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("employee_payments"))

    @app.get("/employee-payments/<int:payment_id>")
    @api["login_required"]
    def employee_payment_detail(payment_id):
        _require(api, "employee_payments")
        payment = _payment_order(api, payment_id)
        _require_payment_access(api, payment)
        sources = api["db"]().execute("select * from payment_order_sources where payment_order_id=? order by id", (payment_id,)).fetchall()
        events = api["db"]().execute(
            "select e.*,u.name creator_name from payment_order_events e left join users u on u.id=e.created_by where e.payment_order_id=? order by e.id",
            (payment_id,),
        ).fetchall()
        applications = api["db"]().execute(
            "select l.*,a.advance_number from employee_advance_applications l join employee_advances a on a.id=l.advance_id where l.payment_order_id=? order by l.id",
            (payment_id,),
        ).fetchall()
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
        # 合并发放出来的付款单要能一眼看到是哪张支票（批次）。
        batch = (
            api["db"]().execute("select * from employee_payment_batches where id=?", (payment["batch_id"],)).fetchone()
            if payment["batch_id"] else None
        )
        return render_template("employee_payment_detail.html", payment=payment, sources=sources, events=events,
                               applications=applications, accounts=accounts, batch=batch,
                               status_labels=PAYMENT_STATUS_LABELS,
                               type_labels=PAYMENT_TYPE_LABELS, method_labels=PAYMENT_METHOD_LABELS)

    @app.post("/employee-payments/<int:payment_id>/transition")
    @api["login_required"]
    def transition_employee_payment(payment_id):
        payment = _payment_order(api, payment_id)
        _require_payment_access(api, payment)
        action = request.form.get("action", "")
        transitions = {
            "submit": ("draft", "pending_review", "edit"),
            "approve": ("pending_review", "approved", "approve"),
            "ready": ("approved", "pending_payment", "pay"),
            "retry": ("payment_failed", "pending_payment", "pay"),
            "mark_paid": ("pending_payment", "paid", "pay"),
            "fail": ("pending_payment", "payment_failed", "pay"),
            "reject": ("pending_review", "rejected", "approve"),
        }
        try:
            if action == "cancel":
                if payment["status"] in {"paid", "reconciled", "cancelled"}:
                    raise ValueError("已付款、已对账或已取消的付款单不能取消。")
                _require(api, "employee_payments", "edit")
                target = "cancelled"
                _reverse_advance_application(api, payment)
            else:
                source, target, permission = transitions.get(action, (None, None, None))
                if not source or payment["status"] != source:
                    raise ValueError("付款单当前状态不允许执行该操作。")
                _require(api, "employee_payments", permission)
                if target == "pending_payment":
                    _create_advance_application(api, payment)
                if target == "paid":
                    account_id = request.form.get("bank_account_id", "")
                    method = request.form.get("payment_method", "")
                    if not account_id.isdigit() or method not in PAYMENT_METHOD_LABELS:
                        raise ValueError("付款时必须选择付款账户和付款方式。")
                    api["db"]().execute(
                        "update employee_payment_orders set bank_account_id=?,payment_method=?,external_transaction_id=?,paid_by=?,paid_at=? where id=?",
                        (int(account_id), method, request.form.get("external_transaction_id", "").strip() or None,
                         g.user["id"], api["now"](), payment_id),
                    )
                    _settle_expense_payout(api, payment)
                if target in {"approved", "rejected"}:
                    api["db"]().execute("update employee_payment_orders set reviewed_by=?,reviewed_at=? where id=?", (g.user["id"], api["now"](), payment_id))
            reason = request.form.get("reason", "").strip()
            api["db"]().execute("update employee_payment_orders set status=?,updated_at=? where id=?", (target, api["now"](), payment_id))
            api["db"]().execute(
                "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,?,?,?,?,?,?)",
                (payment_id, action, payment["status"], target, reason, g.user["id"], api["now"]()),
            )
            api["log_action"](action, "employee_payment", payment_id, payment["payment_number"], f"{payment['status']} → {target} {reason}")
            api["db"]().commit(); flash("付款单状态已更新。", "success")
        except ValueError as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("employee_payment_detail", payment_id=payment_id))

    @app.get("/finance/payment-batches")
    @api["login_required"]
    def payment_batches():
        _require(api, "employee_payments")
        employee_id = request.args.get("employee_id", "")
        status = request.args.get("status", "")
        can_view_all = _can_view_all_payments(api)
        if not can_view_all:
            # 与付款单同一口径：没有「查看全部」权限时只给本人，改 URL 也不放行。
            employee_id = str(g.user["id"])
        employees = (
            api["db"]().execute("select id,name from users where role in ('employee','manager','finance','admin') order by name").fetchall()
            if can_view_all else api["db"]().execute("select id,name from users where id=?", (g.user["id"],)).fetchall()
        )
        clauses, params = ["1=1"], []
        if employee_id.isdigit():
            clauses.append("b.employee_id=?"); params.append(int(employee_id))
        if status in PAYMENT_BATCH_STATUS_LABELS:
            clauses.append("b.status=?"); params.append(status)
        else:
            status = ""
        rows = api["db"]().execute(
            f"""select b.*,u.name employee_name,a.account_name from employee_payment_batches b
            join users u on u.id=b.employee_id left join bank_accounts a on a.id=b.bank_account_id
            where {' and '.join(clauses)} order by b.issued_at desc,b.id desc""", params
        ).fetchall()
        return render_template("payment_batches.html", rows=rows, employees=employees, employee_id=employee_id,
                               status=status, status_labels=PAYMENT_BATCH_STATUS_LABELS,
                               method_labels=PAYMENT_METHOD_LABELS, can_view_all=can_view_all)

    @app.get("/finance/payment-batches/<int:batch_id>")
    @api["login_required"]
    def payment_batch_detail(batch_id):
        _require(api, "employee_payments")
        batch = _load_batch(api, batch_id)
        orders = _batch_orders(api, batch_id)
        return render_template("payment_batch_detail.html", batch=batch, orders=orders,
                               status_labels=PAYMENT_STATUS_LABELS, type_labels=PAYMENT_TYPE_LABELS,
                               method_labels=PAYMENT_METHOD_LABELS,
                               batch_status_labels=PAYMENT_BATCH_STATUS_LABELS)

    @app.post("/finance/payment-batches/create")
    @api["login_required"]
    def create_payment_batch():
        """合并发放：勾选同一员工的多张付款单，合成一张支票。"""
        _require(api, "employee_payments", "pay")
        back = request.referrer or url_for("employee_ledger")
        try:
            raw_ids = [value for value in request.form.getlist("payment_id") if str(value).strip().isdigit()]
            ids = sorted({int(value) for value in raw_ids})
            if not ids:
                raise ValueError("请先勾选要合并发放的付款单。")
            placeholders = ",".join(["?"] * len(ids))
            orders = api["db"]().execute(
                f"""select p.*,u.name employee_name from employee_payment_orders p
                join users u on u.id=p.employee_id where p.id in ({placeholders}) order by p.id""", ids
            ).fetchall()
            if len(orders) != len(ids):
                raise ValueError("部分付款单不存在，请刷新后重试。")
            for order in orders:
                _require_payment_access(api, order)
            blocked = [order["payment_number"] for order in orders
                       if order["status"] not in BATCHABLE_PAYMENT_STATUSES]
            if blocked:
                raise ValueError("只有「已批准」或「待付款」的付款单可以合并发放：" + "、".join(blocked))
            # 一张支票只有一名收款人 —— 跨员工必须先按人分批，否则支票抬头没法写。
            if len({order["employee_id"] for order in orders}) > 1:
                raise ValueError("一张支票只能开给一名员工，请不要跨员工勾选。")
            if len({order["currency"] for order in orders}) > 1:
                raise ValueError("不同币种的付款单不能合并到同一张支票。")
            # 只拦「还在生效」的批次：支票作废后付款单退回待付款，必须能重新合并发放，
            # 否则那几张单就被作废批次永久占住了。
            live_batches = {row["id"] for row in api["db"]().execute(
                "select id from employee_payment_batches where status<>'void'").fetchall()}
            taken = [order["payment_number"] for order in orders
                     if order["batch_id"] and order["batch_id"] in live_batches]
            if taken:
                raise ValueError("这些付款单已属于其它发放批次：" + "、".join(taken))
            account_id = request.form.get("bank_account_id", "")
            method = request.form.get("payment_method", "")
            if not account_id.isdigit() or method not in PAYMENT_METHOD_LABELS:
                raise ValueError("请选择付款账户和付款方式。")
            account = api["db"]().execute(
                "select * from bank_accounts where id=? and is_active=1", (int(account_id),)
            ).fetchone()
            if not account:
                raise ValueError("请选择有效的付款账户。")
            currency = orders[0]["currency"]
            if account["currency"] != currency:
                raise ValueError(f"付款账户币种（{account['currency']}）与付款单币种（{currency}）不一致。")
            check_number = request.form.get("check_number", "").strip()
            if not check_number:
                raise ValueError("请填写支票号。")
            if api["db"]().execute(
                "select id from employee_payment_batches where bank_account_id=? and check_number=? and status<>'void'",
                (int(account_id), check_number),
            ).fetchone():
                raise ValueError("该账户下的支票号已经用过了。")
            total = sum((Decimal(str(order["net_amount"])) for order in orders), Decimal("0"))
            batch_number = _next_number(api, BATCH_NUMBER_PREFIX, "employee_payment_batches", "batch_number")
            now = api["now"]()
            api["db"]().execute(
                "insert into employee_payment_batches(batch_number,employee_id,currency,bank_account_id,payment_method,"
                " check_number,total_amount,payment_count,status,notes,issued_by,issued_at,created_at,updated_at)"
                " values(?,?,?,?,?,?,?,?,'issued',?,?,?,?,?)",
                (batch_number, orders[0]["employee_id"], currency, int(account_id), method, check_number,
                 total, len(orders), request.form.get("notes", "").strip(), g.user["id"], now, now, now),
            )
            batch_id = api["db"]().execute(
                "select id from employee_payment_batches where batch_number=?", (batch_number,)
            ).fetchone()["id"]
            detail = f"批次 {batch_number} · 支票 {check_number}"
            for order in orders:
                # 从「已批准」直接合并发放时，借款抵扣还没落账（单张流程是点
                # 「进入待付款」时落的），这里补上，否则借款余额不会减少。
                if order["status"] == "approved":
                    _create_advance_application(api, order)
                api["db"]().execute(
                    "update employee_payment_orders set status='paid',batch_id=?,bank_account_id=?,payment_method=?,"
                    "external_transaction_id=?,paid_by=?,paid_at=?,updated_at=? where id=?",
                    (batch_id, int(account_id), method, check_number, g.user["id"], now, now, order["id"]),
                )
                api["db"]().execute(
                    "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at)"
                    " values(?,?,?,?,?,?,?)",
                    (order["id"], "batch_pay", order["status"], "paid", detail, g.user["id"], now),
                )
                _settle_expense_payout(api, order, extra=f"（{detail}）")
                api["log_action"]("batch_pay", "employee_payment", order["id"], order["payment_number"],
                                  f"{order['status']} → paid {detail}")
            api["db"]().commit()
            flash(f"已合并发放 {len(orders)} 张付款单：{batch_number}（支票 {check_number}，合计 {total}）。", "success")
            return redirect(url_for("payment_batch_detail", batch_id=batch_id))
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
            return redirect(back)

    @app.post("/finance/payment-batches/<int:batch_id>/void")
    @api["login_required"]
    def void_payment_batch(batch_id):
        """支票作废：批次内付款单退回「待付款」，可重新合并发放。"""
        _require(api, "employee_payments", "pay")
        try:
            batch = _load_batch(api, batch_id)
            if batch["status"] != "issued":
                raise ValueError("只有「已发放」的批次可以作废。")
            reason = request.form.get("reason", "").strip()
            if not reason:
                raise ValueError("请填写作废原因。")
            orders = _batch_orders(api, batch_id)
            now = api["now"]()
            for order in orders:
                # 借款抵扣要冲销（发放时落过一笔 application），否则借款余额虚低。
                _reverse_advance_application(api, order)
                _release_expense_payout(api, order)
                api["db"]().execute(
                    "update employee_payment_orders set status='pending_payment',bank_account_id=null,payment_method=null,"
                    "external_transaction_id=null,paid_by=null,paid_at=null,updated_at=? where id=?",
                    (now, order["id"]),
                )
                api["db"]().execute(
                    "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at)"
                    " values(?,?,?,?,?,?,?)",
                    (order["id"], "void", order["status"], "pending_payment",
                     f"批次 {batch['batch_number']} 作废：{reason}", g.user["id"], now),
                )
                api["log_action"]("void", "employee_payment", order["id"], order["payment_number"],
                                  f"批次 {batch['batch_number']} 作废：{reason}")
            api["db"]().execute(
                "update employee_payment_batches set status='void',voided_by=?,voided_at=?,updated_at=? where id=?",
                (g.user["id"], now, now, batch_id),
            )
            api["db"]().commit()
            flash(f"批次 {batch['batch_number']} 已作废，{len(orders)} 张付款单退回待付款。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("payment_batch_detail", batch_id=batch_id))

    @app.route("/finance/advances", methods=["GET", "POST"])
    @api["login_required"]
    def employee_advances():
        _require(api, "employee_advances")
        if request.method == "POST":
            _require(api, "employee_advances", "create")
            try:
                employee_id = int(request.form.get("employee_id", ""))
                employee = api["db"]().execute(
                    "select id,name from users where id=? and is_active=1", (employee_id,)
                ).fetchone()
                if not employee: raise ValueError("请选择有效员工。")
                amount = _money(request.form.get("principal_amount"), "借款金额")
                if amount <= 0: raise ValueError("借款金额必须大于零。")
                purpose = request.form.get("purpose", "").strip()
                if not purpose: raise ValueError("借款用途不能为空。")
                account_id = int(request.form["bank_account_id"]) if request.form.get("bank_account_id", "").isdigit() else None
                if account_id and not api["db"]().execute(
                    "select id from bank_accounts where id=? and is_active=1", (account_id,)
                ).fetchone():
                    raise ValueError("请选择有效付款账户。")
                number = _next_number(api, "EA", "employee_advances", "advance_number")
                upload = request.files.get("attachment")
                original, stored = "", ""
                if upload and upload.filename:
                    original = secure_filename(upload.filename)
                    stored = os.urandom(16).hex() + Path(original).suffix.lower()
                    folder = Path(api["DATA_DIR"]) / "employee-advance-attachments"; folder.mkdir(parents=True, exist_ok=True)
                    upload.save(folder / stored)
                cursor = api["db"]().execute(
                    "insert into employee_advances(advance_number,employee_id,principal_amount,currency,advance_date,bank_account_id,purpose,attachment_name,attachment_stored_filename,status,external_transaction_id,sync_source,sync_status,created_by,created_at,updated_at) values(?,?,?,'USD',?,?,?,?,?,'open',?,'manual','local',?,?,?)",
                    (number, employee_id, amount, request.form.get("advance_date") or date.today().isoformat(),
                     account_id, purpose, original, stored,
                     request.form.get("external_transaction_id", "").strip() or None,
                     g.user["id"], api["now"](), api["now"]()),
                )
                advance_id = api["db"]().execute("select id from employee_advances where advance_number=?", (number,)).fetchone()["id"]
                api["log_action"]("create", "employee_advance", advance_id, number, f"借款 {amount}")
                api["db"]().commit(); flash("员工借款已登记。", "success")
            except (ValueError, TypeError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("employee_advances"))
        rows = _advance_balances(api)
        employees = api["db"]().execute("select id,name from users where is_active=1 order by name").fetchall()
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
        return render_template("employee_advances.html", rows=rows, employees=employees, accounts=accounts)

    @app.get("/finance/advances/<int:advance_id>/attachment")
    @api["login_required"]
    def employee_advance_attachment(advance_id):
        _require(api, "employee_advances")
        advance = api["db"]().execute("select attachment_name,attachment_stored_filename from employee_advances where id=?", (advance_id,)).fetchone()
        if not advance or not advance["attachment_stored_filename"]: abort(404)
        return send_file(Path(api["DATA_DIR"]) / "employee-advance-attachments" / advance["attachment_stored_filename"], download_name=advance["attachment_name"])

    @app.get("/finance")
    @api["login_required"]
    def finance_overview():
        _require(api, "finance_overview")
        payment_summary = api["db"]().execute(
            "select status,count(*) count,coalesce(sum(net_amount),0) total from employee_payment_orders group by status"
        ).fetchall()
        account_summary = api["db"]().execute(
            """select a.*,coalesce(sum(case when t.direction='credit' then t.amount else -t.amount end),0) movement
            from bank_accounts a left join bank_transactions t on t.bank_account_id=a.id group by a.id order by a.account_name"""
        ).fetchall()
        unreconciled = api["db"]().execute("select count(*) count,coalesce(sum(amount),0) total from bank_transactions where matched_payment_order_id is null").fetchone()
        advances = _advance_balances(api)
        return render_template("finance_overview.html", payment_summary=payment_summary, account_summary=account_summary,
                               unreconciled=unreconciled, advance_total=sum(Decimal(str(r["remaining_amount"])) for r in advances),
                               status_labels=PAYMENT_STATUS_LABELS)

    @app.route("/finance/bank-accounts", methods=["GET", "POST"])
    @api["login_required"]
    def bank_accounts():
        _require(api, "bank_accounts")
        if request.method == "POST":
            _require(api, "bank_accounts", "create")
            try:
                action = request.form.get("action", "create")
                if action == "import":
                    created = skipped = 0
                    for row in _csv_rows(request.files.get("csv_file")):
                        name = _csv_value(row, "account_name", "账户名称")
                        if not name: raise ValueError("账户 CSV 中存在空的账户名称。")
                        if api["db"]().execute("select id from bank_accounts where account_name=?", (name,)).fetchone():
                            skipped += 1; continue
                        opening = _money(_csv_value(row, "opening_balance", "期初余额", default="0"), "期初余额")
                        opening_date = _csv_value(row, "opening_balance_date", "期初日期") or None
                        cursor = api["db"]().execute(
                            "insert into bank_accounts(account_name,bank_name,account_type,currency,last_four,opening_balance,opening_balance_date,initialized_at,initialized_by,is_active,external_account_id,sync_source,sync_status,created_by,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,1,?,'csv','imported',?,?,?)",
                            (name, _csv_value(row, "bank_name", "银行"), _csv_value(row, "account_type", "账户类型", default="checking"),
                             _csv_value(row, "currency", "币种", default="USD"), _csv_value(row, "last_four", "尾号"), opening,
                             opening_date, api["now"]() if opening_date else None, g.user["id"] if opening_date else None,
                             _csv_value(row, "external_account_id", "外部账户ID") or None,
                             g.user["id"], api["now"](), api["now"]()),
                        )
                        api["log_action"]("import", "bank_account", cursor.lastrowid, name, "CSV 初始化银行账户")
                        created += 1
                    message = f"银行账户 CSV 导入完成：新增 {created} 个，跳过重复 {skipped} 个。"
                else:
                    name = request.form.get("account_name", "").strip()
                    if not name: raise ValueError("账户名称不能为空。")
                    opening = _money(request.form.get("opening_balance"), "期初余额")
                    opening_date = request.form.get("opening_balance_date", "").strip() or None
                    cursor = api["db"]().execute(
                        "insert into bank_accounts(account_name,bank_name,account_type,currency,last_four,opening_balance,opening_balance_date,initialized_at,initialized_by,is_active,external_account_id,sync_source,sync_status,created_by,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,1,?,'manual','local',?,?,?)",
                        (name, request.form.get("bank_name", "").strip(), request.form.get("account_type", "checking"),
                         request.form.get("currency", "USD"), request.form.get("last_four", "").strip(), opening,
                         opening_date, api["now"]() if opening_date else None, g.user["id"] if opening_date else None,
                         request.form.get("external_account_id", "").strip() or None, g.user["id"], api["now"](), api["now"]()),
                    )
                    api["log_action"]("create", "bank_account", cursor.lastrowid, name, request.form.get("bank_name", "").strip())
                    message = "银行账户已创建。"
                api["db"]().commit(); flash(message, "success")
            except (ValueError, TypeError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("bank_accounts"))
        rows = api["db"]().execute("select * from bank_accounts order by is_active desc,account_name").fetchall()
        return render_template("bank_accounts.html", rows=rows)

    @app.post("/finance/bank-accounts/<int:account_id>/initialize")
    @api["login_required"]
    def initialize_bank_account(account_id):
        _require(api, "bank_accounts", "create")
        try:
            account = api["db"]().execute("select * from bank_accounts where id=?", (account_id,)).fetchone()
            if not account: abort(404)
            count = api["db"]().execute("select count(*) count from bank_transactions where bank_account_id=?", (account_id,)).fetchone()["count"]
            if count: raise ValueError("该账户已有银行流水，不能重设期初余额。")
            opening = _money(request.form.get("opening_balance"), "期初余额")
            opening_date = request.form.get("opening_balance_date", "").strip()
            if not opening_date: raise ValueError("请选择期初日期。")
            api["db"]().execute(
                "update bank_accounts set opening_balance=?,opening_balance_date=?,initialized_at=?,initialized_by=?,updated_at=? where id=?",
                (opening, opening_date, api["now"](), g.user["id"], api["now"](), account_id),
            )
            api["log_action"]("initialize", "bank_account", account_id, account["account_name"], f"期初日期 {opening_date}，期初余额 {opening}")
            api["db"]().commit(); flash("银行账户期初数据已初始化。", "success")
        except (ValueError, TypeError) as error:
            api["db"]().rollback(); flash(str(error), "error")
        return redirect(url_for("bank_accounts"))

    @app.get("/finance/bank-accounts/import-template")
    @api["login_required"]
    def bank_account_import_template():
        _require(api, "bank_accounts", "create")
        content = "account_name,bank_name,account_type,currency,last_four,opening_balance,opening_balance_date,external_account_id\r\n"
        return Response("\ufeff" + content, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=bank-account-import-template.csv"})

    def insert_bank_transaction(account_id, values, source="manual"):
        amount = _money(values.get("amount"), "流水金额")
        if amount <= 0: raise ValueError("流水金额必须大于零。")
        direction = values.get("direction", "debit").strip().lower()
        if direction not in {"debit", "credit"}: raise ValueError("收支方向必须为 debit 或 credit。")
        external_id = (values.get("external_transaction_id") or "").strip() or None
        fingerprint = hashlib.sha256("|".join([
            str(account_id), values.get("transaction_date") or "", direction, str(amount),
            external_id or "", values.get("description") or ""
        ]).encode("utf-8")).hexdigest()
        api["db"]().execute(
            "insert into bank_transactions(bank_account_id,transaction_date,posted_date,direction,amount,currency,description,reference_number,external_transaction_id,sync_source,sync_status,import_fingerprint,created_by,created_at) values(?,?,?,?,?,'USD',?,?,?,?,'imported',?,?,?) on conflict(import_fingerprint) do nothing",
            (account_id, values.get("transaction_date") or date.today().isoformat(), values.get("posted_date") or None,
             direction, amount, values.get("description", "").strip(), values.get("reference_number", "").strip(),
             external_id, source, fingerprint, g.user["id"], api["now"]()),
        )

    @app.route("/finance/bank-transactions", methods=["GET", "POST"])
    @api["login_required"]
    def bank_transactions():
        _require(api, "bank_transactions")
        if request.method == "POST":
            _require(api, "bank_transactions", "create")
            try:
                account_id = int(request.form.get("bank_account_id", ""))
                upload = request.files.get("csv_file")
                if upload and upload.filename:
                    count = 0
                    for row in _csv_rows(upload):
                        insert_bank_transaction(account_id, row, "csv"); count += 1
                    message = f"CSV 已处理 {count} 行；重复流水按指纹跳过。"
                else:
                    insert_bank_transaction(account_id, request.form, "manual"); message = "银行流水已登记。"
                api["log_action"]("import", "bank_transaction", 0, f"账户 {account_id}", message)
                api["db"]().commit(); flash(message, "success")
            except (ValueError, TypeError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("bank_transactions"))
        rows = api["db"]().execute(
            "select t.*,a.account_name,p.payment_number from bank_transactions t join bank_accounts a on a.id=t.bank_account_id left join employee_payment_orders p on p.id=t.matched_payment_order_id order by t.transaction_date desc,t.id desc"
        ).fetchall()
        accounts = api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
        return render_template("bank_transactions.html", rows=rows, accounts=accounts)

    @app.get("/finance/bank-transactions/import-template")
    @api["login_required"]
    def bank_transaction_import_template():
        _require(api, "bank_transactions", "create")
        content = "transaction_date,posted_date,direction,amount,description,reference_number,external_transaction_id\r\n"
        return Response("\ufeff" + content, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=bank-transaction-import-template.csv"})

    @app.route("/finance/reconciliation", methods=["GET", "POST"])
    @api["login_required"]
    def bank_reconciliation():
        _require(api, "bank_reconciliation")
        if request.method == "POST":
            _require(api, "bank_reconciliation", "reconcile")
            _require(api, "employee_payments", "reconcile")
            try:
                transaction_id = int(request.form.get("transaction_id", ""))
                tx = api["db"]().execute("select * from bank_transactions where id=?", (transaction_id,)).fetchone()
                if not tx or tx["matched_payment_order_id"] or tx["matched_batch_id"]:
                    raise ValueError("银行流水不存在或已匹配。")
                raw_batch = request.form.get("batch_id", "")
                # 批次级对账：一张支票在流水里是一笔支出，整批核销，
                # 不再要求「一笔流水 = 一张付款单」。
                if str(raw_batch).strip().isdigit():
                    batch = _load_batch(api, int(raw_batch))
                    if batch["status"] != "issued": raise ValueError("只有已发放的付款批次可以对账。")
                    if tx["direction"] != "debit" or tx["bank_account_id"] != batch["bank_account_id"]:
                        raise ValueError("流水必须是付款账户下的支出记录。")
                    if tx["currency"] != batch["currency"]:
                        raise ValueError("流水币种与付款批次不一致。")
                    if Decimal(str(tx["amount"])) != Decimal(str(batch["total_amount"])):
                        raise ValueError("流水金额必须与付款批次合计金额一致。")
                    orders = _batch_orders(api, batch["id"])
                    api["db"]().execute(
                        "update bank_transactions set matched_batch_id=?,matched_by=?,matched_at=?,sync_status='matched' where id=?",
                        (batch["id"], g.user["id"], api["now"](), transaction_id),
                    )
                    for order in orders:
                        api["db"]().execute(
                            "update employee_payment_orders set status='reconciled',reconciled_by=?,reconciled_at=?,updated_at=? where id=?",
                            (g.user["id"], api["now"](), api["now"](), order["id"]),
                        )
                        api["db"]().execute(
                            "insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,?,?,?,?,?,?)",
                            (order["id"], "reconcile", order["status"], "reconciled",
                             f"批次 {batch['batch_number']} 匹配银行流水 #{transaction_id}", g.user["id"], api["now"]()),
                        )
                    api["db"]().execute(
                        "update employee_payment_batches set status='reconciled',reconciled_by=?,reconciled_at=?,updated_at=? where id=?",
                        (g.user["id"], api["now"](), api["now"](), batch["id"]),
                    )
                    api["log_action"]("reconcile", "payment_batch", batch["id"], batch["batch_number"], f"银行流水 #{transaction_id}")
                    api["db"]().commit()
                    flash(f"付款批次与银行流水已完成对账，{len(orders)} 张付款单转为已对账。", "success")
                    return redirect(url_for("bank_reconciliation"))
                payment_id = int(request.form.get("payment_id", ""))
                payment = _payment_order(api, payment_id)
                if payment["status"] != "paid": raise ValueError("只有已付款的付款单可以对账。")
                if tx["direction"] != "debit" or tx["bank_account_id"] != payment["bank_account_id"]:
                    raise ValueError("流水必须是付款账户下的支出记录。")
                if tx["currency"] != payment["currency"]:
                    raise ValueError("流水币种与付款单不一致。")
                if Decimal(str(tx["amount"])) != Decimal(str(payment["net_amount"])):
                    raise ValueError("流水金额必须与付款单实付金额一致。")
                api["db"]().execute("update bank_transactions set matched_payment_order_id=?,matched_by=?,matched_at=?,sync_status='matched' where id=?", (payment_id,g.user["id"],api["now"](),transaction_id))
                api["db"]().execute("update employee_payment_orders set status='reconciled',reconciled_by=?,reconciled_at=?,updated_at=? where id=?", (g.user["id"],api["now"](),api["now"](),payment_id))
                api["db"]().execute("insert into payment_order_events(payment_order_id,event_type,from_status,to_status,details,created_by,created_at) values(?,'reconcile','paid','reconciled',?,?,?)", (payment_id,f"匹配银行流水 #{transaction_id}",g.user["id"],api["now"]()))
                api["log_action"]("reconcile", "employee_payment", payment_id, payment["payment_number"], f"银行流水 #{transaction_id}")
                api["db"]().commit(); flash("付款单与银行流水已完成对账。", "success")
            except (ValueError, TypeError) as error:
                api["db"]().rollback(); flash(str(error), "error")
            return redirect(url_for("bank_reconciliation"))
        transactions = api["db"]().execute("select t.*,a.account_name from bank_transactions t join bank_accounts a on a.id=t.bank_account_id where t.matched_payment_order_id is null and t.matched_batch_id is null and t.direction='debit' order by t.transaction_date desc").fetchall()
        payments = api["db"]().execute("select p.*,u.name employee_name from employee_payment_orders p join users u on u.id=p.employee_id where p.status='paid' order by p.paid_at desc").fetchall()
        batches = api["db"]().execute(
            """select b.*,u.name employee_name,a.account_name from employee_payment_batches b
            join users u on u.id=b.employee_id left join bank_accounts a on a.id=b.bank_account_id
            where b.status='issued' order by b.issued_at desc,b.id desc"""
        ).fetchall()
        return render_template("bank_reconciliation.html", transactions=transactions, payments=payments,
                               batches=batches, method_labels=PAYMENT_METHOD_LABELS)

    @app.get("/finance/employee-ledger")
    @api["login_required"]
    def employee_ledger():
        _require(api, "employee_ledger")
        # v0.1.344：从「先选员工才出数据」改成 ERP 明细账范式 ——
        # 默认列出全部员工（受 can_view_all_payments 约束），员工 / 类型 / 状态三个筛选
        # 可任意组合；只有员工筛选必须能单独生效，所以员工筛选走服务端（精确匹配），
        # 类型与状态交给表格自带筛选（前端，含排序+列设置）以减少一次整页往返。
        # 员工下拉必须保留「全部」选项：旧版 select 带 required，不给「全部」就没法退回去。
        employee_id = request.args.get("employee_id", "")
        payment_type = request.args.get("payment_type", "")
        status = request.args.get("status", "")
        can_view_all = _can_view_all_payments(api)
        if not can_view_all:
            # 无「查看全部」权限时强制锁定为本人，忽略 URL 里的 employee_id，
            # 否则改一下查询串就能读到别人的付款记录。
            employee_id = str(g.user["id"])
        employees = (
            api["db"]().execute("select id,name from users where role in ('employee','manager','finance','admin') order by name").fetchall()
            if can_view_all else api["db"]().execute("select id,name from users where id=?", (g.user["id"],)).fetchall()
        )
        clauses, params = ["1=1"], []
        if employee_id.isdigit():
            clauses.append("p.employee_id=?"); params.append(int(employee_id))
        # 状态筛选：白名单判定，非法值当作「全部」。
        if status in PAYMENT_STATUS_LABELS:
            clauses.append("p.status=?"); params.append(status)
        else:
            status = ""
        # 汇总栏「工资 / 员工报销」的计数必须是「当前员工+状态」口径，不套类型筛选 ——
        # 否则选中「工资」后报销数会变成 0，看着像数据没了（v0.1.345）。
        # 所以计数排在类型条件加入之前。
        type_counts = {key: 0 for key in PAYMENT_TYPE_LABELS}
        total_count = 0
        for row in api["db"]().execute(
            f"""select p.payment_type, count(*) total from employee_payment_orders p
            join users u on u.id=p.employee_id where {' and '.join(clauses)} group by p.payment_type""", params
        ).fetchall():
            count = int(row["total"])
            total_count += count
            if row["payment_type"] in type_counts:
                type_counts[row["payment_type"]] = count
        # 类型筛选：白名单判定，只接受 PAYMENT_TYPE_LABELS 里的键，
        # 非法值当作「全部」而不是拼进 SQL（值虽已参数化，但未知类型只会筛出空表，徒增困惑）。
        if payment_type in PAYMENT_TYPE_LABELS:
            clauses.append("p.payment_type=?"); params.append(payment_type)
        else:
            payment_type = ""
        payments = api["db"]().execute(
            f"""select p.*,u.name employee_name from employee_payment_orders p
            join users u on u.id=p.employee_id
            where {' and '.join(clauses)} order by p.created_at desc,p.id desc""", params
        ).fetchall()
        # 借款按员工聚合展示（原表格只有单号/本金/余额，看不出是谁的，选「全部」后完全没法读）
        advances = (
            _advance_balances(api, int(employee_id) if employee_id.isdigit() else None)
            if can_view_all else []
        )
        # 合并发放需要付款账户下拉与「pay」权限；没有权限就不画按钮也不查账户。
        can_pay = can_view_all and api["has_action_permission"]("employee_payments", "pay")
        accounts = (
            api["db"]().execute("select * from bank_accounts where is_active=1 order by account_name").fetchall()
            if can_pay else []
        )
        return render_template("employee_ledger.html", employees=employees, employee_id=employee_id,
                               payments=payments, advances=advances, status_labels=PAYMENT_STATUS_LABELS,
                               type_labels=PAYMENT_TYPE_LABELS, payment_type=payment_type, status=status,
                               total_count=total_count, salary_count=type_counts["salary"],
                               expense_count=type_counts["expense"], can_view_all=can_view_all,
                               can_pay=can_pay, accounts=accounts, method_labels=PAYMENT_METHOD_LABELS)

    @app.route("/assets", methods=["GET", "POST"])
    @api["login_required"]
    def assets():
        _require(api, "assets")
        if request.method == "POST":
            _require(api, "assets", "create")
            name = request.form.get("name", "").strip()
            if not name:
                flash("资产名称不能为空。", "error"); return redirect(url_for("assets"))
            number = _next_number(api, "AS", "assets", "asset_number")
            cursor = api["db"]().execute(
                "insert into assets(asset_number,stable_id,name,category,acquisition_value,currency,serial_number,status,current_holder_id,service_order_id,notes,created_by,created_at,updated_at) values(?,?,?, ?,?,'USD',?,'available',null,?,?,?, ?,?)",
                (number, os.urandom(16).hex(), name, request.form.get("category", "").strip(),
                 _money(request.form.get("acquisition_value"), "资产价值"), request.form.get("serial_number", "").strip(),
                 int(request.form["service_order_id"]) if request.form.get("service_order_id", "").isdigit() else None,
                 request.form.get("notes", "").strip(), g.user["id"], api["now"](), api["now"]()),
            )
            asset_id = cursor.lastrowid
            api["db"]().execute("insert into asset_events(asset_id,event_type,to_status,notes,created_by,created_at) values(?,'created','available',?,?,?)", (asset_id,"资产建档",g.user["id"],api["now"]()))
            api["log_action"]("create", "asset", asset_id, number, name)
            api["db"]().commit(); flash("资产已建档。", "success")
            return redirect(url_for("asset_detail", asset_id=asset_id))
        rows = api["db"]().execute("select a.*,u.name holder_name,s.order_number from assets a left join users u on u.id=a.current_holder_id left join service_orders s on s.id=a.service_order_id order by a.asset_number desc").fetchall()
        orders = api["db"]().execute("select id,order_number from service_orders order by id desc limit 200").fetchall()
        return render_template("assets.html", rows=rows, orders=orders, status_labels=ASSET_STATUS_LABELS)

    @app.get("/assets/<int:asset_id>")
    @api["login_required"]
    def asset_detail(asset_id):
        _require(api, "assets")
        asset = api["db"]().execute("select a.*,u.name holder_name,s.order_number from assets a left join users u on u.id=a.current_holder_id left join service_orders s on s.id=a.service_order_id where a.id=?", (asset_id,)).fetchone()
        if not asset: abort(404)
        events = api["db"]().execute("select e.*,u.name creator_name,h.name holder_name from asset_events e left join users u on u.id=e.created_by left join users h on h.id=e.holder_id where e.asset_id=? order by e.id desc", (asset_id,)).fetchall()
        photos = api["db"]().execute("select * from asset_photos where asset_id=? order by id", (asset_id,)).fetchall()
        employees = api["db"]().execute("select id,name from users where is_active=1 order by name").fetchall()
        return render_template("asset_detail.html", asset=asset, events=events, photos=photos, employees=employees,
                               status_labels=ASSET_STATUS_LABELS, event_labels=ASSET_EVENT_LABELS)

    @app.post("/assets/<int:asset_id>/event")
    @api["login_required"]
    def asset_event(asset_id):
        _require(api, "assets", "edit")
        asset = api["db"]().execute("select * from assets where id=?", (asset_id,)).fetchone()
        if not asset: abort(404)
        event = request.form.get("event_type", "")
        mapping = {"assign":"assigned","return":"available","damaged":"damaged","lost":"lost","retire":"retired"}
        if event not in mapping: abort(400)
        target = mapping[event]
        holder_id = int(request.form["holder_id"]) if event == "assign" and request.form.get("holder_id", "").isdigit() else None
        if event == "assign" and not holder_id:
            flash("领用时必须选择员工。", "error"); return redirect(url_for("asset_detail", asset_id=asset_id))
        api["db"]().execute("update assets set status=?,current_holder_id=?,updated_at=? where id=?", (target,holder_id,api["now"](),asset_id))
        api["db"]().execute("insert into asset_events(asset_id,event_type,from_status,to_status,holder_id,notes,created_by,created_at) values(?,?,?,?,?,?,?,?)", (asset_id,event,asset["status"],target,holder_id,request.form.get("notes", "").strip(),g.user["id"],api["now"]()))
        api["log_action"](event, "asset", asset_id, asset["asset_number"], f"{asset['status']} → {target}")
        api["db"]().commit(); flash("资产状态已更新。", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.get("/assets/<int:asset_id>/qr")
    @api["login_required"]
    def asset_qr(asset_id):
        _require(api, "assets")
        asset = api["db"]().execute("select stable_id from assets where id=?", (asset_id,)).fetchone()
        if not asset: abort(404)
        import qrcode
        target = request.url_root.rstrip("/") + url_for("asset_by_stable_id", stable_id=asset["stable_id"])
        output = io.BytesIO(); qrcode.make(target).save(output, format="PNG"); output.seek(0)
        return send_file(output, mimetype="image/png", download_name=f"asset-{asset_id}-qr.png")

    @app.get("/a/<stable_id>")
    @api["login_required"]
    def asset_by_stable_id(stable_id):
        asset = api["db"]().execute("select id from assets where stable_id=?", (stable_id,)).fetchone()
        if not asset: abort(404)
        return redirect(url_for("asset_detail", asset_id=asset["id"]))

    @app.post("/assets/<int:asset_id>/photos")
    @api["login_required"]
    def upload_asset_photo(asset_id):
        _require(api, "assets", "edit")
        upload = request.files.get("photo")
        if not upload or not upload.filename:
            flash("请选择照片。", "error"); return redirect(url_for("asset_detail", asset_id=asset_id))
        original = secure_filename(upload.filename)
        suffix = Path(original).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif"}:
            flash("不支持的图片格式。", "error"); return redirect(url_for("asset_detail", asset_id=asset_id))
        folder = Path(api["DATA_DIR"]) / "asset-photos" / str(asset_id); folder.mkdir(parents=True, exist_ok=True)
        stored = os.urandom(16).hex() + suffix; upload.save(folder / stored)
        api["db"]().execute("insert into asset_photos(asset_id,stored_filename,original_filename,created_by,created_at) values(?,?,?,?,?)", (asset_id,stored,original,g.user["id"],api["now"]()))
        api["log_action"]("upload", "asset", asset_id, str(asset_id), f"上传照片 {original}")
        api["db"]().commit(); flash("资产照片已上传。", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))

    @app.get("/asset-photos/<int:photo_id>")
    @api["login_required"]
    def asset_photo(photo_id):
        _require(api, "assets")
        photo = api["db"]().execute("select * from asset_photos where id=?", (photo_id,)).fetchone()
        if not photo: abort(404)
        return send_file(Path(api["DATA_DIR"]) / "asset-photos" / str(photo["asset_id"]) / photo["stored_filename"], download_name=photo["original_filename"])
