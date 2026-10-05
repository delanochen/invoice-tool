"""Explicit registration for employee and user-management endpoints."""

import os
import re

from flask import (
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import generate_password_hash

from database import IntegrityError


def register_employee_user_routes(
    app,
    *,
    db,
    now,
    login_required,
    can_assign_external_employees,
    country_from_form,
    language_from_form,
    current_language,
    communication_languages_from_form,
    can_manage_users,
    is_external_manager,
    role_options,
    requires_user_address,
    save_user_service_order_assignments,
    uploaded_attachments_from_request,
    save_named_attachment,
    user_attachment_dir,
    get_user_attachments,
    assigned_service_order_ids,
    can_approve_users,
    country_rows,
    employee_grade_options,
    normalized_role,
    default_language_code,
    country_by_code,
    normalize_phone,
    can_manage_user_record,
    safe_attachment_response,
    create_message,
    worker_tax_status_current,
):
    @login_required
    def users():
        if request.method == "POST" and not can_assign_external_employees():
            abort(403)
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            name = request.form.get("name", "").strip() or email
            english_name = request.form.get("english_name", "").strip()
            address = request.form.get("address", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "employee")
            country = country_from_form()
            default_language = language_from_form(current_language())
            preferred_language, communication_languages = communication_languages_from_form(current_language())
            employee_grade_id = (
                int(request.form["employee_grade_id"])
                if can_manage_users() and request.form.get("employee_grade_id", "").isdigit()
                else None
            )
            if is_external_manager():
                role = "external_employee"
            if role not in role_options:
                role = "employee"
            client_id = (
                g.user["client_id"]
                if is_external_manager()
                else int(request.form["client_id"]) if role in {"external_manager", "external_employee"} and request.form.get("client_id", "").isdigit()
                else None
            )
            if "�" in name:
                flash("姓名包含损坏字符，请重新输入正确姓名。", "error")
                return redirect(url_for("users"))
            if english_name and not re.fullmatch(r"[A-Za-z ]+", english_name):
                flash("英文名只能包含英文字母和空格，不能包含中文、数字或其他符号。", "error")
                return redirect(url_for("users"))
            if requires_user_address(role) and not address:
                flash("内部员工必须填写地址。", "error")
                return redirect(url_for("users"))
            if len(password) < 8:
                flash("密码至少需要 8 位。", "error")
                return redirect(url_for("users"))
            try:
                cursor = db().execute(
                    """
                    insert into users (
                        name, english_name, email, password_hash, role, address, default_language, employee_grade_id, client_id, region_code, country_code, created_at,
                        preferred_communication_language, communication_languages
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        english_name,
                        email,
                        generate_password_hash(password),
                        role,
                        address,
                        default_language,
                        employee_grade_id,
                        client_id,
                        country["region_code"],
                        country["code"],
                        now(),
                        preferred_language,
                        communication_languages,
                    ),
                )
                user_id = cursor.lastrowid
                save_user_service_order_assignments(user_id, role)
                for uploaded in uploaded_attachments_from_request():
                    save_named_attachment(
                        uploaded,
                        os.path.join(user_attachment_dir, str(user_id)),
                        "user_attachments",
                        "user_id",
                        user_id,
                    )
                db().commit()
                flash("用户已创建。", "success")
            except IntegrityError:
                db().rollback()
                flash("这个邮箱已经存在。", "error")
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
            return redirect(url_for("users"))
        if can_manage_users():
            rows = db().execute(
                """
                select users.*, clients.name as client_name,
                       employee_grades.grade_name as employee_grade_name,
                       coalesce(country_local.name, country_zh.name, users.country_code) as country_name,
                       coalesce(country_local.region_name, country_zh.region_name, users.region_code) as region_name
                from users left join clients on clients.id = users.client_id
                left join employee_grades on employee_grades.id = users.employee_grade_id
                left join country_translations country_local
                  on country_local.country_code = users.country_code and country_local.language_code = ?
                left join country_translations country_zh
                  on country_zh.country_code = users.country_code and country_zh.language_code = 'zh-CN'
                order by users.created_at desc
                """,
                (current_language(),),
            ).fetchall()
        elif normalized_role() == "manager":
            rows = db().execute(
                """
                select users.*, clients.name as client_name,
                       employee_grades.grade_name as employee_grade_name,
                       coalesce(country_local.name, country_zh.name, users.country_code) as country_name,
                       coalesce(country_local.region_name, country_zh.region_name, users.region_code) as region_name
                from users left join clients on clients.id = users.client_id
                left join employee_grades on employee_grades.id = users.employee_grade_id
                left join country_translations country_local
                  on country_local.country_code = users.country_code and country_local.language_code = ?
                left join country_translations country_zh
                  on country_zh.country_code = users.country_code and country_zh.language_code = 'zh-CN'
                where users.id = ? or users.role in ('employee', 'external_employee')
                order by users.is_active asc, users.created_at desc
                """,
                (current_language(), g.user["id"]),
            ).fetchall()
        elif is_external_manager():
            rows = db().execute(
                """
                select users.*, clients.name as client_name,
                       employee_grades.grade_name as employee_grade_name,
                       coalesce(country_local.name, country_zh.name, users.country_code) as country_name,
                       coalesce(country_local.region_name, country_zh.region_name, users.region_code) as region_name
                from users left join clients on clients.id = users.client_id
                left join employee_grades on employee_grades.id = users.employee_grade_id
                left join country_translations country_local
                  on country_local.country_code = users.country_code and country_local.language_code = ?
                left join country_translations country_zh
                  on country_zh.country_code = users.country_code and country_zh.language_code = 'zh-CN'
                where users.id = ?
                   or (users.role = 'external_employee' and users.client_id = ?)
                order by users.created_at desc
                """,
                (current_language(), g.user["id"], g.user["client_id"]),
            ).fetchall()
        else:
            rows = db().execute(
                """
                select users.*, null as client_name,
                       employee_grades.grade_name as employee_grade_name,
                       coalesce(country_local.name, country_zh.name, users.country_code) as country_name,
                       coalesce(country_local.region_name, country_zh.region_name, users.region_code) as region_name
                from users
                left join employee_grades on employee_grades.id = users.employee_grade_id
                left join country_translations country_local
                  on country_local.country_code = users.country_code and country_local.language_code = ?
                left join country_translations country_zh
                  on country_zh.country_code = users.country_code and country_zh.language_code = 'zh-CN'
                where users.id = ?
                """,
                (current_language(), g.user["id"]),
            ).fetchall()
        clients_rows = (
            db().execute("select id, client_number, name from clients where id = ?", (g.user["client_id"],)).fetchall()
            if is_external_manager()
            else db().execute("select id, client_number, name from clients order by client_number").fetchall()
        )
        service_orders_rows = (
            db().execute(
                "select id, order_number, client_name, status from service_orders where client_id = ? order by status != 'closed' desc, created_at desc, id desc",
                (g.user["client_id"],),
            ).fetchall()
            if is_external_manager()
            else db().execute("select id, order_number, client_name, status from service_orders order by status != 'closed' desc, created_at desc, id desc").fetchall()
        )
        return render_template(
            "users.html",
            users=rows,
            clients=clients_rows,
            service_orders=service_orders_rows,
            user_attachments={user["id"]: get_user_attachments(user["id"]) for user in rows},
            user_order_ids={user["id"]: assigned_service_order_ids(user["id"]) for user in rows},
            tax_status_by_user={user["id"]: worker_tax_status_current(user["id"]) for user in rows},
            role_options=role_options,
            can_manage=can_manage_users(),
            can_assign=can_assign_external_employees(),
            can_approve=can_approve_users(),
            countries=country_rows(),
            employee_grades=employee_grade_options(),
        )

    @login_required
    def edit_user(user_id):
        if not can_manage_users() and user_id != g.user["id"] and not is_external_manager():
            abort(403)
        user = db().execute(
            """
            select users.*, employee_grades.grade_name as employee_grade_name
            from users
            left join employee_grades on employee_grades.id = users.employee_grade_id
            where users.id = ?
            """,
            (user_id,),
        ).fetchone()
        if not user:
            abort(404)
        if is_external_manager() and user_id != g.user["id"]:
            if normalized_role(user["role"]) != "external_employee" or user["client_id"] != g.user["client_id"]:
                abort(403)
        if request.method == "POST":
            email = request.form.get("email", user["email"]).strip().lower() if can_manage_users() else user["email"]
            name = request.form.get("name", "").strip() or email
            english_name = request.form.get("english_name", "").strip()
            role = request.form.get("role", normalized_role(user["role"])) if can_manage_users() else user["role"]
            address = request.form.get("address", "").strip()
            default_language = language_from_form(user["default_language"] or default_language_code)
            preferred_language, communication_languages = communication_languages_from_form(
                user["preferred_communication_language"] or user["default_language"] or default_language_code
            )
            if role not in role_options and can_manage_users():
                role = "employee"
            client_id = (
                user["client_id"]
                if not can_manage_users()
                else int(request.form["client_id"]) if role in {"external_manager", "external_employee"} and request.form.get("client_id", "").isdigit()
                else None
            )
            employee_grade_id = (
                int(request.form["employee_grade_id"])
                if can_manage_users() and request.form.get("employee_grade_id", "").isdigit()
                else user["employee_grade_id"]
            )
            password = request.form.get("password", "")
            country = country_from_form(user["country_code"]) if can_assign_external_employees() else country_by_code(
                user["country_code"], include_inactive=True
            )
            admin_count = db().execute("select count(*) as count from users where role = 'admin'").fetchone()["count"]
            if "�" in name:
                flash("姓名包含损坏字符，请重新输入正确姓名。", "error")
                return redirect(url_for("edit_user", user_id=user_id))
            if english_name and not re.fullmatch(r"[A-Za-z ]+", english_name):
                flash("英文名只能包含英文字母和空格，不能包含中文、数字或其他符号。", "error")
                return redirect(url_for("edit_user", user_id=user_id))
            if can_manage_users() and user["role"] == "admin" and role != "admin" and admin_count <= 1:
                flash("至少需要保留一个管理员。", "error")
                return redirect(url_for("edit_user", user_id=user_id))
            if requires_user_address(role) and not address:
                flash("内部员工必须填写地址。", "error")
                return redirect(url_for("edit_user", user_id=user_id))
            try:
                phone = user["phone"] or ""
                if "phone" in request.form:
                    raw_phone = request.form.get("phone", "").strip()
                    phone = normalize_phone(raw_phone, country["code"]) if raw_phone else ""
                db().execute(
                    """
                    update users
                    set name = ?, english_name = ?, email = ?, role = ?, address = ?, default_language = ?, employee_grade_id = ?, client_id = ?, region_code = ?, country_code = ?, phone = ?,
                        preferred_communication_language = ?, communication_languages = ?
                    where id = ?
                    """,
                    (
                        name,
                        english_name,
                        email,
                        role,
                        address,
                        default_language,
                        employee_grade_id,
                        client_id,
                        country["region_code"],
                        country["code"],
                        phone,
                        preferred_language,
                        communication_languages,
                        user_id,
                    ),
                )
                if phone != (user["phone"] or ""):
                    db().execute("update users set phone_verified = 0 where id = ?", (user_id,))
                if user_id == g.user["id"]:
                    session["language"] = default_language
                if can_assign_external_employees():
                    save_user_service_order_assignments(user_id, role)
                if password:
                    if len(password) < 8:
                        flash("新密码至少需要 8 位。", "error")
                        return redirect(url_for("edit_user", user_id=user_id))
                    db().execute("update users set password_hash = ? where id = ?", (generate_password_hash(password), user_id))
                for uploaded in uploaded_attachments_from_request():
                    save_named_attachment(
                        uploaded,
                        os.path.join(user_attachment_dir, str(user_id)),
                        "user_attachments",
                        "user_id",
                        user_id,
                    )
                db().commit()
                flash("用户资料已更新。", "success")
            except IntegrityError:
                db().rollback()
                flash("这个邮箱已经存在。", "error")
                return redirect(url_for("edit_user", user_id=user_id))
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
                return redirect(url_for("edit_user", user_id=user_id))
            return redirect(url_for("users"))
        clients_rows = (
            db().execute("select id, client_number, name from clients where id = ?", (g.user["client_id"],)).fetchall()
            if is_external_manager()
            else db().execute("select id, client_number, name from clients order by client_number").fetchall()
        )
        service_orders_rows = (
            db().execute(
                "select id, order_number, client_name, status from service_orders where client_id = ? order by status != 'closed' desc, created_at desc, id desc",
                (g.user["client_id"],),
            ).fetchall()
            if is_external_manager()
            else db().execute("select id, order_number, client_name, status from service_orders order by status != 'closed' desc, created_at desc, id desc").fetchall()
        )
        return render_template(
            "user_form.html",
            user=user,
            clients=clients_rows,
            service_orders=service_orders_rows,
            selected_order_ids=assigned_service_order_ids(user_id),
            attachments=get_user_attachments(user_id),
            current_tax_status=worker_tax_status_current(user_id),
            role_options=role_options,
            can_manage=can_manage_users(),
            can_assign=can_assign_external_employees(),
            can_approve=can_approve_users(),
            countries=country_rows(),
            employee_grades=employee_grade_options(),
        )

    @login_required
    def update_user_status(user_id):
        if not can_approve_users():
            abort(403)
        user = db().execute("select * from users where id = ?", (user_id,)).fetchone()
        if not user:
            abort(404)
        if normalized_role(user["role"]) not in {"employee", "external_employee"}:
            abort(403)
        is_active = 1 if request.form.get("is_active") == "1" else 0
        db().execute("update users set is_active = ? where id = ?", (is_active, user_id))
        if is_active:
            create_message(
                user_id,
                "账号已启用",
                f"{g.user['name']} 已批准并启用你的账号。",
            )
        db().commit()
        flash("用户状态已更新。", "success")
        return redirect(url_for("users"))

    @login_required
    def download_user_attachment(attachment_id):
        attachment = db().execute("select * from user_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        owner = db().execute("select * from users where id = ?", (attachment["user_id"],)).fetchone()
        if not owner or not can_manage_user_record(owner):
            abort(403)
        return send_file(
            os.path.join(user_attachment_dir, str(attachment["user_id"]), attachment["stored_filename"]),
            as_attachment=True,
            download_name=attachment["original_filename"],
        )

    @login_required
    def preview_user_attachment(attachment_id):
        attachment = db().execute("select * from user_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        owner = db().execute("select * from users where id = ?", (attachment["user_id"],)).fetchone()
        if not owner or not can_manage_user_record(owner):
            abort(403)
        return safe_attachment_response(
            os.path.join(user_attachment_dir, str(attachment["user_id"]), attachment["stored_filename"]),
            attachment["original_filename"],
        )

    @login_required
    def delete_user_attachment(attachment_id):
        attachment = db().execute("select * from user_attachments where id = ?", (attachment_id,)).fetchone()
        if not attachment:
            abort(404)
        owner = db().execute("select * from users where id = ?", (attachment["user_id"],)).fetchone()
        if not owner or not can_manage_user_record(owner):
            abort(403)
        try:
            os.remove(os.path.join(user_attachment_dir, str(attachment["user_id"]), attachment["stored_filename"]))
        except FileNotFoundError:
            pass
        db().execute("delete from user_attachments where id = ?", (attachment_id,))
        db().commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "attachment_id": attachment_id})
        flash("用户附件已删除。", "success")
        return redirect(url_for("users"))

    @login_required
    def delete_user(user_id):
        if user_id == g.user["id"]:
            flash("不能删除当前登录用户。", "error")
            return redirect(url_for("users"))
        user = db().execute("select * from users where id = ?", (user_id,)).fetchone()
        if not user:
            abort(404)
        admin_count = db().execute("select count(*) as count from users where role = 'admin'").fetchone()["count"]
        if user["role"] == "admin" and admin_count <= 1:
            flash("至少需要保留一个管理员。", "error")
            return redirect(url_for("users"))
        invoice_count = db().execute("select count(*) as count from invoices where created_by = ?", (user_id,)).fetchone()["count"]
        if invoice_count:
            flash("这个用户已经创建过发票，不能删除。", "error")
            return redirect(url_for("users"))
        db().execute("delete from messages where user_id = ?", (user_id,))
        db().execute("delete from users where id = ?", (user_id,))
        db().commit()
        flash("用户已删除。", "success")
        return redirect(url_for("users"))

    app.add_url_rule('/users', endpoint='users', view_func=users, methods=['GET', 'POST'])
    app.add_url_rule('/users/<int:user_id>/edit', endpoint='edit_user', view_func=edit_user, methods=['GET', 'POST'])
    app.add_url_rule('/users/<int:user_id>/status', endpoint='update_user_status', view_func=update_user_status, methods=['POST'])
    app.add_url_rule('/user-attachments/<int:attachment_id>/download', endpoint='download_user_attachment', view_func=download_user_attachment, methods=['GET'])
    app.add_url_rule('/user-attachments/<int:attachment_id>/preview', endpoint='preview_user_attachment', view_func=preview_user_attachment, methods=['GET'])
    app.add_url_rule('/user-attachments/<int:attachment_id>/delete', endpoint='delete_user_attachment', view_func=delete_user_attachment, methods=['POST'])
    app.add_url_rule('/users/<int:user_id>/delete', endpoint='delete_user', view_func=delete_user, methods=['POST'])
    return {
        "users": users,
        "edit_user": edit_user,
        "update_user_status": update_user_status,
        "download_user_attachment": download_user_attachment,
        "preview_user_attachment": preview_user_attachment,
        "delete_user_attachment": delete_user_attachment,
        "delete_user": delete_user,
    }
