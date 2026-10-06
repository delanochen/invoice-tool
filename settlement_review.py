from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from flask import abort, flash, g, redirect, render_template, request, url_for

from rate_engine import contract_rate


ELIGIBLE_STATUS = "approved"


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def selected_expense_item_ids(connection, reimbursement_id):
    return {
        row["expense_item_id"]
        for row in connection.execute(
            "select expense_item_id from customer_reimbursement_expense_links where customer_reimbursement_id = ?",
            (reimbursement_id,),
        ).fetchall()
    }


def selected_expense_count(connection, reimbursement_id):
    return connection.execute(
        "select count(*) as count from customer_reimbursement_expense_links where customer_reimbursement_id = ?",
        (reimbursement_id,),
    ).fetchone()["count"]


def settlement_expense_candidates(api, order_id, reimbursement_id=None):
    selected = selected_expense_item_ids(api["db"](), reimbursement_id) if reimbursement_id else set()
    snapshots = {}
    if reimbursement_id:
        snapshots = {
            row["expense_item_id"]: dict(row)
            for row in api["db"]().execute(
                """
                select expense_item_id, amount_snapshot, project_snapshot, expense_status_snapshot
                from customer_reimbursement_expense_links
                where customer_reimbursement_id = ?
                """,
                (reimbursement_id,),
            ).fetchall()
        }
    rows = api["db"]().execute(
        """
        select expense_items.id as expense_item_id,
               expense_items.expense_id,
               expense_items.line_key,
               expense_items.amount,
               expense_items.description,
               expense_items.fuel_vehicle_type,
               coalesce(projects.name, expense_items.project) as project_name,
               expenses.expense_number,
               expenses.expense_date,
               expenses.status,
               expenses.payout_status,
               expenses.reviewed_at,
               expenses.created_at as expense_created_at,
               expenses.updated_at as expense_updated_at,
               coalesce(beneficiaries.name, creators.name) as worker_name,
               creators.name as creator_name
        from expenses
        join expense_items on expense_items.expense_id = expenses.id
        left join projects on projects.id = expense_items.project_id
        left join users creators on creators.id = expenses.created_by
        left join users beneficiaries on beneficiaries.id = coalesce(expenses.beneficiary_id, expenses.created_by)
        where expenses.service_order_id = ?
        order by expenses.expense_date, expenses.id, expense_items.sort_order, expense_items.id
        """,
        (order_id,),
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        field_name = api["customer_reimbursement_expense_field"](
            data["project_name"], data["fuel_vehicle_type"]
        )
        data["settlement_field"] = field_name
        data["eligible"] = bool(field_name)
        data["selectable"] = bool(field_name) and data["status"] == ELIGIBLE_STATUS
        data["selected"] = data["expense_item_id"] in selected
        snapshot = snapshots.get(data["expense_item_id"])
        data["amount_snapshot"] = snapshot.get("amount_snapshot") if snapshot else None
        data["project_snapshot"] = snapshot.get("project_snapshot") if snapshot else None
        data["status_snapshot"] = snapshot.get("expense_status_snapshot") if snapshot else None
        data["source_changed"] = bool(snapshot) and (
            _money(data["amount"]) != _money(snapshot.get("amount_snapshot"))
            or (data["project_name"] or "") != (snapshot.get("project_snapshot") or "")
            or (data["status"] or "") != (snapshot.get("expense_status_snapshot") or "")
        )
        data["group_key"] = field_name or "excluded"
        data["group_label"] = {
            "lodging": "住宿费",
            "airfare": "机票",
            "baggage": "行李费",
            "rental_car": "租车费",
            "fuel": "租车油费",
            "parking": "停车费",
            "taxi": "打车费",
            "other": "配件及耗材 / 其他",
            "excluded": "不进入客户实报实销",
        }.get(data["group_key"], data["project_name"])
        result.append(data)
    return result


def _worker_work_dates(connection, order_id):
    rows = connection.execute(
        """
        select service_report_workers.user_id,
               date(coalesce(service_reports.actual_work_date, service_reports.report_date)) as work_date
        from service_reports
        join service_report_workers on service_report_workers.report_id = service_reports.id
        where service_reports.service_order_id = ?
          and coalesce(service_reports.actual_work_date, service_reports.report_date) is not null
        group by service_report_workers.user_id,
                 date(coalesce(service_reports.actual_work_date, service_reports.report_date))
        order by service_report_workers.user_id, work_date
        """,
        (order_id,),
    ).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        try:
            grouped[row["user_id"]].append(date.fromisoformat(row["work_date"]))
        except (TypeError, ValueError):
            continue
    return grouped


def default_person_nights(connection, order_id):
    """Estimate nights from each worker's span of actual work dates.

    Two consecutive work days -> one person-night. Three consecutive days -> two.
    A gap contributes the calendar nights in the gap; the review screen allows a
    manual override because a worker may go home between non-consecutive dates.
    """
    total = 0
    for dates in _worker_work_dates(connection, order_id).values():
        unique_dates = sorted(set(dates))
        for previous, current in zip(unique_dates, unique_dates[1:]):
            gap = (current - previous).days
            if gap > 0:
                total += gap
    return total


def _contract_lodging_cap(api, order, work_date=None):
    fallback = float(api["lodging_reimbursement_limit"]() or 0)
    rate = contract_rate(
        api["db"](),
        order["id"],
        work_date or order["start_date"] or date.today().isoformat(),
        "lodging_cap",
        lodging_fallback=fallback,
    )
    return rate


def _candidate_summary(candidates, person_nights, lodging_cap_rate):
    groups = {}
    for candidate in candidates:
        key = candidate["group_key"]
        bucket = groups.setdefault(
            key,
            {
                "label": candidate["group_label"],
                "approved": Decimal("0"),
                "pending": Decimal("0"),
                "selected": Decimal("0"),
                "count": 0,
            },
        )
        bucket["count"] += 1
        amount = _money(candidate["amount"])
        if candidate["status"] == ELIGIBLE_STATUS:
            bucket["approved"] += amount
        else:
            bucket["pending"] += amount
        if candidate["selected"]:
            bucket["selected"] += amount
    cap = _money(lodging_cap_rate) * Decimal(str(person_nights or 0))
    selected_lodging = groups.get("lodging", {}).get("selected", Decimal("0"))
    return {
        "groups": groups,
        "lodging_cap": cap,
        "selected_lodging": selected_lodging,
        "unused_lodging_cap": max(cap - selected_lodging, Decimal("0")),
    }


def replace_expense_links(api, reimbursement_id, candidate_map, selected_ids):
    api["db"]().execute(
        "delete from customer_reimbursement_expense_links where customer_reimbursement_id = ?",
        (reimbursement_id,),
    )
    for item_id in sorted(selected_ids):
        row = candidate_map.get(item_id)
        if not row:
            raise ValueError("选择的报销明细不属于当前工单。")
        if not row["selectable"]:
            raise ValueError(f"{row['expense_number']} 的该明细尚未审核通过，不能进入客户结算。")
        duplicate = api["db"]().execute(
            """
            select customer_reimbursements.id, service_orders.order_number
            from customer_reimbursement_expense_links
            join customer_reimbursements on customer_reimbursements.id = customer_reimbursement_expense_links.customer_reimbursement_id
            join service_orders on service_orders.id = customer_reimbursements.service_order_id
            where customer_reimbursement_expense_links.expense_item_id = ?
              and customer_reimbursement_expense_links.customer_reimbursement_id != ?
            limit 1
            """,
            (item_id, reimbursement_id),
        ).fetchone()
        if duplicate:
            raise ValueError(f"{row['expense_number']} 的该明细已经进入其他工单结算，不能重复结算。")
        api["db"]().execute(
            """
            insert into customer_reimbursement_expense_links
                (customer_reimbursement_id, expense_item_id, amount_snapshot, project_snapshot,
                 expense_status_snapshot, selected_by, selected_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reimbursement_id,
                item_id,
                float(row["amount"] or 0),
                row["project_name"] or "",
                row["status"] or "",
                g.user["id"],
                api["now"](),
            ),
        )


def linked_approved_expense_rows(api, reimbursement_id, order_id, cutoff_at=None):
    return api["db"]().execute(
        """
        select expense_items.id as expense_item_id,
               coalesce(customer_reimbursement_expense_links.amount_snapshot, expense_items.amount) as amount,
               expense_items.fuel_vehicle_type,
               expenses.id as expense_id, expenses.expense_number, expense_items.line_key,
               expense_items.description as item_description,
               (select count(*) from expense_items ordinal where ordinal.expense_id = expenses.id
                and (ordinal.sort_order < expense_items.sort_order
                     or (ordinal.sort_order = expense_items.sort_order and ordinal.id <= expense_items.id))) as line_number,
               coalesce(projects.name, expense_items.project) as project_name,
               expenses.expense_date, users.name as worker_name
        from customer_reimbursement_expense_links
        join expense_items on expense_items.id = customer_reimbursement_expense_links.expense_item_id
        join expenses on expenses.id = expense_items.expense_id
        join users on users.id = coalesce(expenses.beneficiary_id, expenses.created_by)
        left join projects on projects.id = expense_items.project_id
        where customer_reimbursement_expense_links.customer_reimbursement_id = ?
          and expenses.service_order_id = ?
          and expenses.status = 'approved'
          and (? is null or coalesce(expenses.reviewed_at, expenses.updated_at, expenses.created_at) <= ?)
        order by expenses.expense_date, expenses.id, expense_items.sort_order, expense_items.id
        """,
        (reimbursement_id, order_id, cutoff_at, cutoff_at),
    ).fetchall()


def selected_expense_total(api, reimbursement_id, field_name):
    total = Decimal("0")
    reimbursement = api["db"]().execute(
        "select service_order_id, expense_transfer_cutoff_at from customer_reimbursements where id = ?",
        (reimbursement_id,),
    ).fetchone()
    if not reimbursement:
        return 0.0
    for row in linked_approved_expense_rows(
        api,
        reimbursement_id,
        reimbursement["service_order_id"],
        reimbursement["expense_transfer_cutoff_at"],
    ):
        mapped = api["customer_reimbursement_expense_field"](
            row["project_name"], row["fuel_vehicle_type"]
        )
        if mapped == field_name:
            total += _money(row["amount"])
    return float(total)


def register_settlement_review_routes(app, api):
    @app.route("/service-orders/<int:order_id>/customer-reimbursement/review", methods=["GET", "POST"])
    @api["login_required"]
    def customer_reimbursement_expense_review(order_id):
        if not api["can_view_customer_reimbursement"]():
            abort(403)
        order = api["require_service_order"](order_id)
        reimbursement = api["latest_customer_reimbursement"](order_id)
        if reimbursement and reimbursement["status"] not in {"draft", "returned"}:
            flash("这份工单结算已进入审核/完成状态，不能重新选择报销来源。", "error")
            return redirect(url_for("customer_reimbursement_form", order_id=order_id))
        if request.method == "POST" and not api["can_manage_customer_reimbursement"]():
            abort(403)

        candidates = settlement_expense_candidates(
            api,
            order_id,
            reimbursement["id"] if reimbursement else None,
        )
        candidate_map = {row["expense_item_id"]: row for row in candidates}
        default_nights = default_person_nights(api["db"](), order_id)
        stored_nights = int(reimbursement["lodging_person_nights"] or 0) if reimbursement else 0
        person_nights = stored_nights if stored_nights > 0 else default_nights
        lodging_rate = _contract_lodging_cap(api, order)

        if request.method == "POST":
            action = request.form.get("action", "generate")
            if action == "cancel":
                return redirect(url_for("service_order_detail", order_id=order_id))
            try:
                selected_ids = {
                    int(value)
                    for value in request.form.getlist("expense_item_id")
                    if str(value).isdigit()
                }
                raw_nights = request.form.get("lodging_person_nights", str(person_nights)).strip()
                try:
                    person_nights = max(int(raw_nights or 0), 0)
                except ValueError as exc:
                    raise ValueError("住宿人晚必须是整数。") from exc

                if not reimbursement:
                    reimbursement = api["create_customer_reimbursement"](order, "manual_review")
                replace_expense_links(api, reimbursement["id"], candidate_map, selected_ids)
                api["db"]().execute(
                    """
                    update customer_reimbursements
                    set lodging_person_nights = ?, lodging_cap_rate_snapshot = ?, contract_rate_version_id = ?,
                        expense_selection_mode = 'manual_review', expense_transfer_cutoff_at = ?
                    where id = ?
                    """,
                    (
                        person_nights,
                        lodging_rate["rate"],
                        lodging_rate["version_id"],
                        api["now"](),
                        reimbursement["id"],
                    ),
                )
                api["update_customer_reimbursement_totals"](reimbursement["id"])
                api["log_action"](
                    "update",
                    "customer_reimbursement",
                    reimbursement["id"],
                    reimbursement["file_name"],
                    f"结算前审核：选择 {len(selected_ids)} 条员工报销；住宿 {person_nights} 人晚",
                )
                api["db"]().commit()
                flash("已按选择的报销明细生成工单结算草稿。", "success")
                return redirect(url_for("customer_reimbursement_form", order_id=order_id))
            except ValueError as error:
                api["db"]().rollback()
                flash(str(error), "error")

        # Re-read selection after a failed POST only when an existing settlement exists.
        if reimbursement:
            selected = selected_expense_item_ids(api["db"](), reimbursement["id"])
            for candidate in candidates:
                candidate["selected"] = candidate["expense_item_id"] in selected
        summary = _candidate_summary(candidates, person_nights, lodging_rate["rate"])
        # 客户费率一律来自合同费率版本：审核页同样醒目提示缺失项
        missing_rate_summary = api["contract_rate_missing_summary"](
            order_id, [candidate.get("expense_date") for candidate in candidates]
        )
        return render_template(
            "settlement_expense_review.html",
            order=order,
            reimbursement=reimbursement,
            candidates=candidates,
            summary=summary,
            lodging_rate=lodging_rate,
            person_nights=person_nights,
            default_person_nights=default_nights,
            missing_rate_summary=missing_rate_summary,
        )
