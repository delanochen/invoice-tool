"""Expense query and processing queue endpoints."""
import secrets
from flask import abort, g, flash, redirect, render_template, request, url_for, jsonify, send_file


def register_expense_query_routes(app, *,
    resolve_EXPENSE_PAYOUT_LABELS,
    resolve_EXPENSE_STATUS_LABELS,
    current_language,
    db,
    expense_access_filter,
    is_internal_user,
    login_required,
):
    @login_required
    def expense_query():
        q = request.args.get("q", "").strip()
        person_id = request.args.get("person_id", "").strip()
        status = request.args.get("status", "")
        payout_status = request.args.get("payout_status", "")
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", "")
        order_number = request.args.get("order_number", "").strip()
        project_name = request.args.get("project_name", "").strip()
        clauses = ["1 = 1"]
        params = []
        access_clause, access_params = expense_access_filter()
        clauses.append(access_clause)
        params.extend(access_params)
        if person_id.isdigit():
            clauses.append("coalesce(expenses.beneficiary_id, expenses.created_by) = ?")
            params.append(int(person_id))
        else:
            person_id = ""
        if q:
            clauses.append(
                """(expenses.expense_number like ?
                 or coalesce(projects.name, expense_items.project) like ?
                 or coalesce(expense_items.description, '') like ?
                 or service_orders.order_number like ?
                 or service_orders.client_name like ?)"""
            )
            params.extend([f"%{q}%"] * 5)
        if order_number:
            clauses.append("service_orders.order_number like ?")
            params.append(f"%{order_number}%")
        if project_name:
            clauses.append("coalesce(projects.name, expense_items.project) = ?")
            params.append(project_name)
        if status:
            clauses.append("expenses.status = ?")
            params.append(status)
        if payout_status in resolve_EXPENSE_PAYOUT_LABELS():
            clauses.append("expenses.payout_status = ?")
            params.append(payout_status)
        else:
            payout_status = ""
        if date_from:
            clauses.append("expenses.expense_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("expenses.expense_date <= ?")
            params.append(date_to)
        rows = db().execute(
            f"""
            select expenses.*, expense_items.id as item_id,
                   coalesce(projects.name, expense_items.project) as item_project,
                   expense_items.description as item_description, expense_items.amount as item_amount,
                   service_orders.order_number, service_orders.client_name, users.name as creator_name, beneficiaries.name as beneficiary_name,
                   coalesce(country_local.name, country_zh.name, service_orders.country_code) as country_name,
                   coalesce(country_local.region_name, country_zh.region_name, service_orders.region_code) as region_name
            from expenses
            join expense_items on expense_items.expense_id = expenses.id
            join service_orders on service_orders.id = expenses.service_order_id
            left join users on users.id = expenses.created_by
            left join users as beneficiaries on beneficiaries.id = coalesce(expenses.beneficiary_id, expenses.created_by)
            left join projects on projects.id = expense_items.project_id
            left join country_translations country_local
              on country_local.country_code = service_orders.country_code and country_local.language_code = ?
            left join country_translations country_zh
              on country_zh.country_code = service_orders.country_code and country_zh.language_code = 'zh-CN'
            where {" and ".join(clauses)}
            order by expenses.expense_date desc, expenses.id desc, expense_items.sort_order, expense_items.id
            """,
            [current_language(), *params],
        ).fetchall()
        total = sum(
            float(row["item_amount"] or 0)
            for row in rows
            if row["status"] == "approved" and row["payout_status"] != "paid"
        )
        people = db().execute(
            f"""
            select distinct users.id, users.name
            from users
            join expenses on coalesce(expenses.beneficiary_id, expenses.created_by) = users.id
            where {access_clause}
            order by users.name
            """,
            access_params,
        ).fetchall()
        project_options = db().execute(
            f"""select distinct coalesce(projects.name, expense_items.project) as name
            from expenses join expense_items on expense_items.expense_id = expenses.id
            left join projects on projects.id = expense_items.project_id
            where {access_clause} order by name""", access_params).fetchall()
        return render_template(
            "expense_query.html",
            rows=rows,
            q=q,
            person_id=person_id,
            people=people,
            status=status,
            payout_status=payout_status,
            date_from=date_from,
            date_to=date_to,
            total=total,
            labels=resolve_EXPENSE_STATUS_LABELS(),
            payout_labels=resolve_EXPENSE_PAYOUT_LABELS(),
            order_number=order_number,
            project_name=project_name,
            project_options=project_options,
        )


    @login_required
    def expense_processing():
        if not is_internal_user():
            abort(403)
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        payout_status = request.args.get("payout_status", "").strip()
        clauses = ["1 = 1"]
        params = []
        access_clause, access_params = expense_access_filter()
        clauses.append(access_clause)
        params.extend(access_params)
        if q:
            clauses.append(
                """
                (expenses.expense_number like ? or service_orders.order_number like ?
                 or service_orders.client_name like ? or users.name like ? or beneficiaries.name like ?)
                """
            )
            params.extend([f"%{q}%"] * 5)
        if status in resolve_EXPENSE_STATUS_LABELS():
            clauses.append("expenses.status = ?")
            params.append(status)
        else:
            status = ""
        if payout_status in resolve_EXPENSE_PAYOUT_LABELS():
            clauses.append("expenses.payout_status = ?")
            params.append(payout_status)
        else:
            payout_status = ""
        rows = db().execute(
            f"""
            select expenses.*, service_orders.order_number, service_orders.client_name,
                   users.name as creator_name, beneficiaries.name as beneficiary_name, reimbursers.name as reimbursed_by_name
            from expenses
            join service_orders on service_orders.id = expenses.service_order_id
            left join users on users.id = expenses.created_by
            left join users as beneficiaries on beneficiaries.id = coalesce(expenses.beneficiary_id, expenses.created_by)
            left join users as reimbursers on reimbursers.id = expenses.reimbursed_by
            where {" and ".join(clauses)}
            order by
                case
                    when expenses.status = 'approved' and expenses.payout_status = 'pending' then 0
                    when expenses.status = 'submitted' then 1
                    when expenses.status = 'returned' then 2
                    when expenses.status = 'draft' then 3
                    else 4
                end,
                expenses.expense_date desc,
                expenses.id desc
            """,
            params,
        ).fetchall()
        totals = db().execute(
            f"""
            select
                coalesce(sum(case when payout_status != 'paid' then amount else 0 end), 0) as pending_total,
                coalesce(sum(case when payout_status = 'paid' then amount else 0 end), 0) as paid_total
            from expenses
            where status = 'approved' and {access_clause}
            """, access_params,
        ).fetchone()
        return render_template(
            "expense_processing.html",
            rows=rows,
            q=q,
            status=status,
            payout_status=payout_status,
            expense_labels=resolve_EXPENSE_STATUS_LABELS(),
            payout_labels=resolve_EXPENSE_PAYOUT_LABELS(),
            pending_total=totals["pending_total"],
            paid_total=totals["paid_total"],
        )


    app.add_url_rule('/reports/expenses', endpoint='expense_query', view_func=expense_query, methods=['GET'])
    app.add_url_rule('/expense-processing', endpoint='expense_processing', view_func=expense_processing, methods=['GET'])
    return {'expense_query': expense_query, 'expense_processing': expense_processing}
