"""Project-level employee settlement agreements.

Daily reports remain the factual source for hours and mileage.  This module only
decides how those facts become employee compensation: normal rate calculation,
or one all-inclusive fixed amount for an employee on one service order.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")
MODES = {"actual", "fixed"}


def _money(value):
    try:
        return Decimal(str(value or "0").replace(",", "")).quantize(CENT, ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("请输入有效的一口价金额。")


def fixed_employee_settlement(connection, service_order_id, employee_id):
    return connection.execute(
        """
        select * from service_order_employee_settlements
        where service_order_id = ? and employee_id = ?
          and settlement_mode = 'fixed' and status in ('confirmed', 'paid')
        """,
        (service_order_id, employee_id),
    ).fetchone()


def employee_expense_block_reason(connection, service_order_id, employee_id):
    agreement = fixed_employee_settlement(connection, service_order_id, employee_id)
    if not agreement:
        return ""
    return "该员工在此工单采用项目一口价全包结算，不能提交员工报销。"


def _existing_expenses(connection, service_order_id, employee_id):
    return connection.execute(
        """
        select expense_number, status
        from expenses
        where service_order_id = ?
          and coalesce(beneficiary_id, created_by) = ?
          and status not in ('cancelled', 'deleted')
        order by id
        """,
        (service_order_id, employee_id),
    ).fetchall()


def register_order_employee_settlement_routes(app, api):
    @app.post("/service-orders/<int:order_id>/employee-settlements")
    @api["login_required"]
    def save_order_employee_settlement(order_id):
        if api["normalized_role"]() not in {"admin", "manager", "finance"}:
            api["abort"](403)
        order = api["require_service_order"](order_id)
        employee_id = str(api["request"].form.get("employee_id") or "").strip()
        if not employee_id.isdigit():
            api["flash"]("请选择员工。", "error")
            return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))
        employee_id = int(employee_id)
        employee = api["db"]().execute(
            "select id, name from users where id = ? and is_active = 1", (employee_id,)
        ).fetchone()
        if not employee:
            api["abort"](404)

        mode = str(api["request"].form.get("settlement_mode") or "actual").strip()
        if mode not in MODES:
            api["flash"]("请选择有效的员工结算方式。", "error")
            return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))
        try:
            amount = _money(api["request"].form.get("fixed_amount")) if mode == "fixed" else Decimal("0.00")
        except ValueError as error:
            api["flash"](str(error), "error")
            return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))
        settlement_date = str(api["request"].form.get("settlement_date") or "").strip() or None
        if mode == "fixed" and amount <= 0:
            api["flash"]("项目一口价必须大于 0。", "error")
            return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))
        if mode == "fixed" and not settlement_date:
            api["flash"]("项目一口价必须填写结算日期。", "error")
            return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))
        if settlement_date:
            try:
                date.fromisoformat(settlement_date)
            except ValueError:
                api["flash"]("请输入有效的结算日期。", "error")
                return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))
        if mode == "fixed":
            expenses = _existing_expenses(api["db"](), order_id, employee_id)
            if expenses:
                numbers = "、".join(row["expense_number"] for row in expenses[:5])
                api["flash"](f"该员工已有工单报销（{numbers}），处理后才能改为一口价。", "error")
                return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))

        current = api["db"]().execute(
            "select * from service_order_employee_settlements where service_order_id=? and employee_id=?",
            (order_id, employee_id),
        ).fetchone()
        if current and current["status"] == "paid":
            api["flash"]("该员工的一口价已经付款，不能直接修改；请先办理冲销。", "error")
            return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))
        timestamp = api["now"]()
        api["db"]().execute(
            """
            insert into service_order_employee_settlements (
                service_order_id, employee_id, settlement_mode, fixed_amount, currency,
                settlement_date, status, notes, confirmed_by, confirmed_at, created_at, updated_at
            ) values (?, ?, ?, ?, 'USD', ?, 'confirmed', ?, ?, ?, ?, ?)
            on conflict (service_order_id, employee_id) do update set
                settlement_mode=excluded.settlement_mode,
                fixed_amount=excluded.fixed_amount,
                settlement_date=excluded.settlement_date,
                status='confirmed', notes=excluded.notes,
                confirmed_by=excluded.confirmed_by, confirmed_at=excluded.confirmed_at,
                updated_at=excluded.updated_at
            """,
            (
                order_id, employee_id, mode, amount, settlement_date,
                str(api["request"].form.get("notes") or "").strip(),
                api["g"].user["id"], timestamp, timestamp, timestamp,
            ),
        )
        api["log_action"](
            "update", "service_order_employee_settlement", current["id"] if current else None,
            order["order_number"],
            f"{employee['name']}：{'项目一口价 ' + str(amount) if mode == 'fixed' else '按实际数量'}",
        )
        api["db"]().commit()
        api["flash"]("人员结算约定已保存。", "success")
        return api["redirect"](api["url_for"]("service_order_detail", order_id=order_id))

    return {"save_order_employee_settlement": save_order_employee_settlement}


def order_employee_settlement_rows(connection, service_order_id):
    """Employees seen on reports/assignments plus their current agreement and actuals."""
    return connection.execute(
        """
        with participants as (
            select srw.user_id
            from service_report_workers srw
            join service_reports sr on sr.id = srw.report_id
            where sr.service_order_id = ?
            union
            select user_id from user_service_orders where service_order_id = ?
        ), actuals as (
            select srw.user_id,
                   coalesce(sum(sr.total_service_hours / nullif(wc.worker_count, 0)), 0) as actual_hours,
                   coalesce(sum(srw.travel_hours), 0) as travel_hours,
                   coalesce(sum(srw.driving_miles), 0) as actual_miles
            from service_report_workers srw
            join service_reports sr on sr.id = srw.report_id
            join (
                select report_id, count(*) as worker_count
                from service_report_workers group by report_id
            ) wc on wc.report_id = sr.id
            where sr.service_order_id = ?
            group by srw.user_id
        )
        select u.id as employee_id, u.name as employee_name,
               coalesce(s.settlement_mode, 'actual') as settlement_mode,
               coalesce(s.fixed_amount, 0) as fixed_amount,
               s.currency, s.settlement_date, s.status, s.notes,
               coalesce(a.actual_hours, 0) as actual_hours,
               coalesce(a.travel_hours, 0) as travel_hours,
               coalesce(a.actual_miles, 0) as actual_miles
        from participants p
        join users u on u.id = p.user_id
        left join service_order_employee_settlements s
          on s.service_order_id = ? and s.employee_id = u.id and s.status != 'cancelled'
        left join actuals a on a.user_id = u.id
        order by u.name, u.id
        """,
        (service_order_id, service_order_id, service_order_id, service_order_id),
    ).fetchall()
