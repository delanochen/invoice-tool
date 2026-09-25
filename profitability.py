from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import abort, render_template, request

from rate_engine import CONTRACT_RATE_LABELS, contract_rate, employee_rate


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_float(value):
    return float(_money(value))


def init_profitability_schema(connection):
    """Create the snapshot ledger now; Phase 1 UI uses live estimated lines.

    The table is intentionally additive and does not rewrite historical source data.
    A later lock/finalize action can persist the exact live lines into this ledger.
    """
    connection.executescript(
        """
        create table if not exists profit_ledger (
            id integer primary key autoincrement,
            work_date text not null,
            service_order_id integer not null,
            employee_id integer,
            category text not null,
            item_type text not null,
            quantity real not null default 0,
            unit text not null default '',
            client_rate_snapshot real not null default 0,
            employee_rate_snapshot real not null default 0,
            revenue real not null default 0,
            cost real not null default 0,
            profit real not null default 0,
            contract_rate_version_id integer,
            employee_rate_version_id integer,
            source_type text not null,
            source_id integer,
            source_line_id integer,
            allocation_method text not null default 'direct',
            calculation_status text not null default 'estimated',
            calculation_version integer not null default 1,
            created_at text not null,
            foreign key(service_order_id) references service_orders(id) on delete cascade,
            foreign key(employee_id) references users(id),
            foreign key(contract_rate_version_id) references contract_rate_versions(id),
            foreign key(employee_rate_version_id) references employee_rate_versions(id)
        );
        create index if not exists idx_profit_ledger_date on profit_ledger(work_date);
        create index if not exists idx_profit_ledger_order on profit_ledger(service_order_id, work_date);
        """
    )


def _parse_day(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _default_date_range():
    today = date.today()
    start = today.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = next_month - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _requested_range():
    default_start, default_end = _default_date_range()
    start = request.args.get("start_date", default_start).strip() or default_start
    end = request.args.get("end_date", default_end).strip() or default_end
    if not _parse_day(start):
        start = default_start
    if not _parse_day(end):
        end = default_end
    if end < start:
        start, end = end, start
    return start, end


def _labor_components(api, report, worker, worker_count):
    work_date = api["report_actual_date"](report)
    work_hours = float(api["report_duration_hours"](report, worker_count) or 0)
    parsed = _parse_day(work_date)
    if parsed and api["is_us_weekend_or_holiday"](parsed):
        regular = 0.0
        overtime = 0.0
        holiday = work_hours
    else:
        regular = min(work_hours, 8.0)
        overtime = max(work_hours - 8.0, 0.0)
        holiday = 0.0

    mode = worker["travel_mode"] or "legacy"
    travel_hours = 0.0 if mode in {"self_drive", "legacy"} else float(worker["travel_hours"] or 0)
    public_hours = float(worker["public_transport_hours"] or 0)
    if mode == "rental_drive":
        billing_miles = 0.0
    elif report["mileage_billing_method"] == "per_vehicle":
        billing_miles = float(worker["driving_miles"] or 0) if mode in {"self_drive", "legacy"} else 0.0
    else:
        billing_miles = float(worker["driving_miles"] or 0) if mode in {"self_drive", "following", "legacy"} else 0.0

    return [
        ("regular_hours", regular, "hour"),
        ("overtime_hours", overtime, "hour"),
        ("holiday_hours", holiday, "hour"),
        ("travel_hours", travel_hours, "hour"),
        ("public_transport_hours", public_hours, "hour"),
        ("mileage", billing_miles, "mile"),
    ]


def _employee_cost_rate_type(item_type, worker):
    if item_type == "travel_hours" and (worker["travel_mode"] or "legacy") == "rental_drive":
        return "rental_drive_hours"
    return item_type


def _line_status(order_status, has_settlement, has_invoice):
    """按工单维度判定利润行状态（业务规则，2026-09-17 用户确认）：
    - 工单已完成（closed）→ 客户已确认（甲方确认金额后我们才把工单改为已完成）
    - 工单进行中但已生成工单结算单或发票 → 结算中（金额尚未获甲方确认，可能删除重开）
    - 工单未生成工单结算单/发票 → 预计
    """
    if order_status == "closed":
        return "client_confirmed"
    if has_settlement or has_invoice:
        return "settlement_in_progress"
    return "estimated"


def _labor_lines(api, start_date, end_date, order_id=None, employee_id=None):
    clauses = ["date(coalesce(service_reports.actual_work_date, service_reports.report_date)) between ? and ?"]
    params = [start_date, end_date]
    if order_id:
        clauses.append("service_reports.service_order_id = ?")
        params.append(order_id)
    reports = api["db"]().execute(
        f"""
        select service_reports.*, service_orders.order_number, service_orders.client_name,
               service_orders.contract_id,
               service_orders.status as order_status,
               exists(select 1 from customer_reimbursements cr
                      where cr.service_order_id = service_orders.id) as has_settlement,
               exists(select 1 from invoices inv
                      where inv.service_order_id = service_orders.id and inv.status != 'void') as has_invoice
        from service_reports
        join service_orders on service_orders.id = service_reports.service_order_id
        where {' and '.join(clauses)}
        order by coalesce(service_reports.actual_work_date, service_reports.report_date), service_reports.id
        """,
        params,
    ).fetchall()
    lines = []
    for report in reports:
        workers = api["db"]().execute(
            """
            select service_report_workers.*, users.name as worker_name
            from service_report_workers
            join users on users.id = service_report_workers.user_id
            where service_report_workers.report_id = ?
            order by users.name, users.id
            """,
            (report["id"],),
        ).fetchall()
        worker_count = max(len(workers), 1)
        work_date = api["report_actual_date"](report)
        for worker in workers:
            # 按人员筛选时只跳过行生成；worker_count 仍取全组人数，
            # 保证选中员工的工时分摊与不筛选时完全一致。
            if employee_id and worker["user_id"] != employee_id:
                continue
            for item_type, quantity, unit in _labor_components(api, report, worker, worker_count):
                if quantity <= 0:
                    continue
                client = contract_rate(
                    api["db"](), report["service_order_id"], work_date, item_type,
                    lodging_fallback=api["lodging_reimbursement_limit"](),
                )
                employee_type = _employee_cost_rate_type(item_type, worker)
                employee = employee_rate(api["db"](), worker["user_id"], work_date, employee_type)
                revenue = _money(quantity) * _money(client["rate"])
                cost = _money(quantity) * _money(employee["rate"])
                missing = (
                    client["source"] == "missing"
                    or employee["source"].startswith("missing")
                    or float(client["rate"] or 0) <= 0
                    or float(employee["rate"] or 0) <= 0
                )
                lines.append({
                    "work_date": work_date,
                    "service_order_id": report["service_order_id"],
                    "order_number": report["order_number"],
                    "client_name": report["client_name"],
                    "employee_id": worker["user_id"],
                    "employee_name": worker["worker_name"],
                    "category": "labor" if item_type in {"regular_hours", "overtime_hours", "holiday_hours"} else "allowance",
                    "item_type": item_type,
                    "item_label": CONTRACT_RATE_LABELS.get(item_type, item_type),
                    "quantity": float(quantity),
                    "unit": unit,
                    "client_rate": float(client["rate"]),
                    "employee_rate": float(employee["rate"]),
                    "revenue": float(revenue),
                    "cost": float(cost),
                    "profit": float(revenue - cost),
                    "client_rate_source": client["source"],
                    "employee_rate_source": employee["source"],
                    "contract_rate_version_id": client["version_id"],
                    "employee_rate_version_id": employee["version_id"],
                    "source_type": "service_report",
                    "source_id": report["id"],
                    "source_line_id": worker["id"],
                    "allocation_method": "direct",
                    "status": _line_status(report["order_status"], report["has_settlement"], report["has_invoice"]),
                    "incomplete": missing,
                })
    return lines


def _work_hour_weights_for_orders(api, order_ids):
    """Return full-work-order labor-hour weights for expense allocation.

    The report date filter must not change how a whole-work-order expense is
    allocated.  For example, a personal-fuel claim covering an August/September
    job is first distributed across *all* actual work dates on that order; only
    after that do we apply the requested profitability date range.
    """
    order_ids = sorted({int(value) for value in order_ids if value})
    weights = defaultdict(lambda: defaultdict(Decimal))
    if not order_ids:
        return weights
    placeholders = ",".join("?" for _ in order_ids)
    reports = api["db"]().execute(
        f"""
        select service_reports.*
        from service_reports
        where service_reports.service_order_id in ({placeholders})
        order by service_reports.service_order_id,
                 coalesce(service_reports.actual_work_date, service_reports.report_date),
                 service_reports.id
        """,
        order_ids,
    ).fetchall()
    for report in reports:
        workers = api["db"]().execute(
            """
            select service_report_workers.user_id
            from service_report_workers
            where service_report_workers.report_id = ?
            order by service_report_workers.user_id
            """,
            (report["id"],),
        ).fetchall()
        if not workers:
            continue
        work_date = api["report_actual_date"](report)
        work_hours = Decimal(str(api["report_duration_hours"](report, len(workers)) or 0))
        if work_hours <= 0:
            continue
        for worker in workers:
            weights[(report["service_order_id"], worker["user_id"])][work_date] += work_hours
    return weights


def _lodging_ratios(api):
    ratios = {}
    reimbursements = api["db"]().execute(
        """
        select id, lodging_person_nights, lodging_cap_rate_snapshot
        from customer_reimbursements
        where id in (select distinct customer_reimbursement_id from customer_reimbursement_expense_links)
        """
    ).fetchall()
    for reimbursement in reimbursements:
        rows = api["db"]().execute(
            """
            select customer_reimbursement_expense_links.amount_snapshot,
                   coalesce(projects.name, expense_items.project) as project_name,
                   expense_items.fuel_vehicle_type
            from customer_reimbursement_expense_links
            join expense_items on expense_items.id = customer_reimbursement_expense_links.expense_item_id
            left join projects on projects.id = expense_items.project_id
            where customer_reimbursement_expense_links.customer_reimbursement_id = ?
            """,
            (reimbursement["id"],),
        ).fetchall()
        lodging_total = Decimal("0")
        for row in rows:
            if api["customer_reimbursement_expense_field"](row["project_name"], row["fuel_vehicle_type"]) == "lodging":
                lodging_total += _money(row["amount_snapshot"])
        cap = _money(reimbursement["lodging_person_nights"]) * _money(reimbursement["lodging_cap_rate_snapshot"])
        ratios[reimbursement["id"]] = Decimal("1") if lodging_total <= 0 or cap <= 0 else min(Decimal("1"), cap / lodging_total)
    return ratios


def _selected_expense_map(api):
    ratios = _lodging_ratios(api)
    result = {}
    rows = api["db"]().execute(
        """
        select links.expense_item_id, links.amount_snapshot, links.customer_reimbursement_id,
               coalesce(projects.name, expense_items.project) as project_name,
               expense_items.fuel_vehicle_type
        from customer_reimbursement_expense_links links
        join customer_reimbursements cr on cr.id = links.customer_reimbursement_id
        join expense_items on expense_items.id = links.expense_item_id
        left join projects on projects.id = expense_items.project_id
        order by cr.created_at desc, cr.id desc
        """
    ).fetchall()
    for row in rows:
        if row["expense_item_id"] in result:
            continue
        amount = _money(row["amount_snapshot"])
        field = api["customer_reimbursement_expense_field"](row["project_name"], row["fuel_vehicle_type"])
        if field == "lodging":
            amount *= ratios.get(row["customer_reimbursement_id"], Decimal("1"))
        result[row["expense_item_id"]] = {
            "amount": amount,
            "field": field,
        }
    return result


def _is_personal_fuel(api, row):
    if row["fuel_vehicle_type"] == "personal":
        return True
    name = str(row["project_name"] or "").strip()
    return api["normalized_project_name"](name) == "个人自驾油费"


def _expense_lines(api, start_date, end_date, order_id=None, employee_id=None):
    # Pull all approved expenses for the selected orders, then date-filter after
    # allocation so order-level personal fuel can be distributed across work days.
    clauses = ["expenses.status = 'approved'"]
    params = []
    if order_id:
        clauses.append("expenses.service_order_id = ?")
        params.append(order_id)
    if employee_id:
        clauses.append("coalesce(expenses.beneficiary_id, expenses.created_by) = ?")
        params.append(employee_id)
    rows = api["db"]().execute(
        f"""
        select expense_items.id as expense_item_id, expense_items.amount,
               expense_items.description, expense_items.fuel_vehicle_type,
               coalesce(projects.name, expense_items.project) as project_name,
               expenses.id as expense_id, expenses.expense_date,
               expenses.service_order_id,
               coalesce(expenses.beneficiary_id, expenses.created_by) as employee_id,
               users.name as employee_name,
               service_orders.order_number, service_orders.client_name,
               service_orders.status as order_status,
               exists(select 1 from customer_reimbursements cr
                      where cr.service_order_id = service_orders.id) as has_settlement,
               exists(select 1 from invoices inv
                      where inv.service_order_id = service_orders.id and inv.status != 'void') as has_invoice
        from expenses
        join expense_items on expense_items.expense_id = expenses.id
        join service_orders on service_orders.id = expenses.service_order_id
        left join projects on projects.id = expense_items.project_id
        left join users on users.id = coalesce(expenses.beneficiary_id, expenses.created_by)
        where {' and '.join(clauses)}
        order by expenses.expense_date, expenses.id, expense_items.sort_order, expense_items.id
        """,
        params,
    ).fetchall()
    selected = _selected_expense_map(api)
    weights = _work_hour_weights_for_orders(api, {row["service_order_id"] for row in rows})
    lines = []

    def append_line(row, work_date, cost, revenue, allocation_method, selection):
        if work_date < start_date or work_date > end_date:
            return
        revenue = _money(revenue)
        cost = _money(cost)
        lines.append({
            "work_date": work_date,
            "service_order_id": row["service_order_id"],
            "order_number": row["order_number"],
            "client_name": row["client_name"],
            "employee_id": row["employee_id"],
            "employee_name": row["employee_name"] or "-",
            "category": "expense",
            "item_type": api["customer_reimbursement_expense_field"](row["project_name"], row["fuel_vehicle_type"]) or "company_only",
            "item_label": row["project_name"],
            "quantity": 1.0,
            "unit": "expense",
            "client_rate": float(revenue),
            "employee_rate": float(cost),
            "revenue": float(revenue),
            "cost": float(cost),
            "profit": float(revenue - cost),
            "client_rate_source": "selected_settlement_expense" if selection else "not_client_billed",
            "employee_rate_source": "actual_approved_expense",
            "contract_rate_version_id": None,
            "employee_rate_version_id": None,
            "source_type": "expense_item",
            "source_id": row["expense_id"],
            "source_line_id": row["expense_item_id"],
            "allocation_method": allocation_method,
            "status": _line_status(row["order_status"], row["has_settlement"], row["has_invoice"]),
            "incomplete": False,
        })

    for row in rows:
        cost = _money(row["amount"])
        selection = selected.get(row["expense_item_id"])
        revenue = selection["amount"] if selection else Decimal("0")
        if _is_personal_fuel(api, row):
            # Customer never receives personal/self-drive fuel as a pass-through
            # under the current business rule. Allocate an order-level fuel claim
            # by the employee's work hours so daily/monthly profit is not distorted.
            revenue = Decimal("0")
            day_weights = weights.get((row["service_order_id"], row["employee_id"]), {})
            total_weight = sum(day_weights.values(), Decimal("0"))
            if day_weights and total_weight > 0:
                allocated = Decimal("0")
                weighted_days = sorted(day_weights.items())
                for index, (work_date, weight) in enumerate(weighted_days):
                    if index == len(weighted_days) - 1:
                        share = cost - allocated
                    else:
                        share = (cost * weight / total_weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        allocated += share
                    append_line(row, work_date, share, 0, "by_work_hours", None)
                continue
        append_line(row, str(row["expense_date"] or "")[:10], cost, revenue, "direct", selection)
    return lines


def build_profit_lines(api, start_date, end_date, order_id=None, employee_id=None):
    labor = _labor_lines(api, start_date, end_date, order_id, employee_id)
    expenses = _expense_lines(api, start_date, end_date, order_id, employee_id)
    return labor + expenses


def _group_lines(lines, mode):
    grouped = {}
    for line in lines:
        if mode == "month":
            key = line["work_date"][:7]
            label = key
        elif mode == "order":
            key = str(line["service_order_id"])
            label = f"{line['order_number']} · {line['client_name']}"
        else:
            key = line["work_date"]
            label = key
        bucket = grouped.setdefault(key, {
            "key": key,
            "label": label,
            "revenue": Decimal("0"),
            "cost": Decimal("0"),
            "profit": Decimal("0"),
            "incomplete": 0,
            "line_count": 0,
        })
        bucket["revenue"] += _money(line["revenue"])
        bucket["cost"] += _money(line["cost"])
        bucket["profit"] += _money(line["profit"])
        bucket["incomplete"] += 1 if line["incomplete"] else 0
        bucket["line_count"] += 1
    output = []
    for bucket in grouped.values():
        revenue = bucket["revenue"]
        bucket["margin"] = float((bucket["profit"] / revenue * 100) if revenue else 0)
        for field in ("revenue", "cost", "profit"):
            bucket[field] = float(bucket[field])
        output.append(bucket)
    return sorted(output, key=lambda row: row["key"], reverse=True)


def _summary(lines):
    revenue = sum((_money(line["revenue"]) for line in lines), Decimal("0"))
    cost = sum((_money(line["cost"]) for line in lines), Decimal("0"))
    profit = revenue - cost
    by_category = {}
    for category in ("labor", "allowance", "expense"):
        category_lines = [line for line in lines if line["category"] == category]
        cat_revenue = sum((_money(line["revenue"]) for line in category_lines), Decimal("0"))
        cat_cost = sum((_money(line["cost"]) for line in category_lines), Decimal("0"))
        by_category[category] = {
            "revenue": float(cat_revenue),
            "cost": float(cat_cost),
            "profit": float(cat_revenue - cat_cost),
        }
    return {
        "revenue": float(revenue),
        "cost": float(cost),
        "profit": float(profit),
        "margin": float((profit / revenue * 100) if revenue else 0),
        "incomplete": sum(1 for line in lines if line["incomplete"]),
        "by_category": by_category,
    }


def register_profitability_routes(app, api):
    @app.get("/reports/profitability")
    @api["login_required"]
    def profitability_report():
        if not api["has_menu_permission"]("profitability") or not api["has_action_permission"]("profitability", "view"):
            abort(403)
        start_date, end_date = _requested_range()
        order_id_text = request.args.get("service_order_id", "").strip()
        order_id = int(order_id_text) if order_id_text.isdigit() else None
        employee_id_text = request.args.get("employee_id", "").strip()
        employee_id = int(employee_id_text) if employee_id_text.isdigit() else None
        mode = request.args.get("group_by", "day")
        if mode not in {"day", "month", "order"}:
            mode = "day"
        lines = build_profit_lines(api, start_date, end_date, order_id, employee_id)
        rows = _group_lines(lines, mode)
        orders = api["db"]().execute(
            "select id, order_number, client_name from service_orders order by order_number desc"
        ).fetchall()
        employees = api["db"]().execute(
            "select id, name from users order by name, id"
        ).fetchall()
        return render_template(
            "profitability.html",
            lines=lines,
            rows=rows,
            summary=_summary(lines),
            start_date=start_date,
            end_date=end_date,
            selected_order_id=order_id,
            selected_employee_id=employee_id,
            group_by=mode,
            orders=orders,
            employees=employees,
        )
