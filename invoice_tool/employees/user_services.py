"""User attachment, assignment, and lookup helpers."""

from flask import g, request


def build_user_services(*, db, now, is_external_manager):
    def get_user_attachments(user_id):
        return db().execute(
            """
            select user_attachments.*, users.name as uploader_name
            from user_attachments
            left join users on users.id = user_attachments.uploaded_by
            where user_attachments.user_id = ?
            order by uploaded_at desc, user_attachments.id desc
            """,
            (user_id,),
        ).fetchall()

    def assigned_service_order_ids(user_id):
        return {
            row["service_order_id"]
            for row in db().execute(
                "select service_order_id from user_service_orders where user_id = ?",
                (user_id,),
            ).fetchall()
        }

    def save_user_service_order_assignments(user_id, role):
        db().execute("delete from user_service_orders where user_id = ?", (user_id,))
        if role != "external_employee":
            return
        order_ids = {int(value) for value in request.form.getlist("service_order_id") if value.isdigit()}
        for order_id in order_ids:
            order = db().execute("select client_id from service_orders where id = ?", (order_id,)).fetchone()
            if order and (not is_external_manager() or order["client_id"] == g.user["client_id"]):
                db().execute(
                    """
                    insert into user_service_orders (user_id, service_order_id, assigned_by, assigned_at)
                    values (?, ?, ?, ?)
                    """,
                    (user_id, order_id, g.user["id"], now()),
                )

    def employee_grade_options(include_inactive=False):
        active_clause = "" if include_inactive else "where is_active = 1"
        return db().execute(
            f"""
            select *
            from employee_grades
            {active_clause}
            order by is_active desc, grade_name
            """
        ).fetchall()

    return {
        "get_user_attachments": get_user_attachments,
        "assigned_service_order_ids": assigned_service_order_ids,
        "save_user_service_order_assignments": save_user_service_order_assignments,
        "employee_grade_options": employee_grade_options,
    }
