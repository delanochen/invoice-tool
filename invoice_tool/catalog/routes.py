"""Explicit registration for base catalog endpoints."""

import re

from flask import abort, flash, redirect, render_template, request, url_for

from database import IntegrityError
from .services import normalized_project_name, project_name_key


def register_catalog_routes(
    app,
    *,
    db,
    now,
    login_required,
    has_action_permission,
    to_float,
    project_type_labels,
    settlement_expense_field_labels,
    customer_reimbursement_invoice_projects,
    merge_duplicate_projects,
    project_name_exists,
    country_rows,
    country_translations,
):
    @login_required
    def work_order_types():
        if request.method == "POST":
            code = request.form.get("code", "").strip().upper()
            name = request.form.get("name", "").strip()
            if not code or not name:
                flash("请填写工单类型编码和名称。", "error")
                return redirect(url_for("work_order_types"))
            try:
                db().execute(
                    """
                    insert into work_order_types (code, name, description, is_active, created_at)
                    values (?, ?, ?, 1, ?)
                    """,
                    (code, name, request.form.get("description", "").strip(), now()),
                )
                db().commit()
                flash("工单类型已创建。", "success")
            except IntegrityError:
                db().rollback()
                flash("工单类型编码重复。", "error")
            return redirect(url_for("work_order_types"))
        rows = db().execute("select * from work_order_types order by is_active desc, code").fetchall()
        return render_template("work_order_types.html", work_order_types=rows)

    @login_required
    def edit_work_order_type(type_id):
        work_order_type = db().execute("select * from work_order_types where id = ?", (type_id,)).fetchone()
        if not work_order_type:
            abort(404)
        if request.method == "POST":
            try:
                db().execute(
                    """
                    update work_order_types
                    set code = ?, name = ?, description = ?, is_active = ?
                    where id = ?
                    """,
                    (
                        request.form.get("code", "").strip().upper(),
                        request.form.get("name", "").strip(),
                        request.form.get("description", "").strip(),
                        1 if request.form.get("is_active", "1") == "1" else 0,
                        type_id,
                    ),
                )
                db().commit()
                flash("工单类型已更新。", "success")
                return redirect(url_for("work_order_types"))
            except IntegrityError:
                db().rollback()
                flash("工单类型编码重复。", "error")
        return render_template("work_order_type_form.html", work_order_type=work_order_type)

    @login_required
    def delete_work_order_type(type_id):
        used = db().execute(
            "select count(*) as count from service_orders where work_order_type_id = ?",
            (type_id,),
        ).fetchone()["count"]
        if used:
            flash("这个工单类型已有工单使用，不能删除，可以停用。", "error")
            return redirect(url_for("work_order_types"))
        db().execute("delete from work_order_types where id = ?", (type_id,))
        db().commit()
        flash("工单类型已删除。", "success")
        return redirect(url_for("work_order_types"))

    @login_required
    def projects():
        map_action = request.form.get("map_action", "").strip()
        if request.method == "POST" and map_action in ("add", "delete"):
            # 报销项目映射维护（v0.1.325）：员工报销项目 → 结算字段 → 发票项目。
            # 「其他」桶发票拆分（customer_reimbursement_other_invoice_breakdown）按此表 fail-fast，
            # 此前只有种子数据没有任何维护界面。
            if not has_action_permission("projects", "edit"):
                abort(403)
            if map_action == "add":
                expense_name = normalized_project_name(request.form.get("expense_project_name"))
                settlement_field = request.form.get("settlement_field", "").strip()
                invoice_name = request.form.get("invoice_project_name", "").strip()
                if not expense_name or settlement_field not in settlement_expense_field_labels or not invoice_name:
                    flash("新增映射：报销项目、结算字段、发票项目都不能为空。", "error")
                elif db().execute(
                    "select 1 from expense_settlement_invoice_map where expense_project_name = ?",
                    (expense_name,),
                ).fetchone():
                    flash(f"报销项目「{expense_name}」已有映射，请先删除旧行再新增。", "error")
                else:
                    # id 显式生成：PG 端该表 id 序列与种子行（migration 0282）未同步，
                    # nextval 会撞已有种子 id；使用 max(id)+1 保持现有分配语义。
                    db().execute(
                        "insert into expense_settlement_invoice_map (id, expense_project_name, settlement_field, invoice_project_name, created_at) "
                        "values ((select coalesce(max(id), 0) + 1 from expense_settlement_invoice_map), ?, ?, ?, ?)",
                        (expense_name, settlement_field, invoice_name, now()),
                    )
                    db().commit()
            else:
                map_id = request.form.get("map_id", "").strip()
                if map_id.isdigit():
                    db().execute("delete from expense_settlement_invoice_map where id = ?", (int(map_id),))
                    db().commit()
            return redirect(url_for("projects"))
        if request.method == "POST":
            name = normalized_project_name(request.form.get("name"))
            project_type = request.form.get("project_type", "invoice")
            if project_type not in project_type_labels:
                project_type = "invoice"
            if not name:
                flash("项目名称不能为空。", "error")
                return redirect(url_for("projects"))
            if project_name_exists(project_type, name):
                flash("同一项目类型下已存在同名项目，不能重复创建。", "error")
                return redirect(url_for("projects"))
            try:
                db().execute(
                    """
                    insert into projects (name, name_key, project_type, default_amount, unit_price, tax_rate, is_active, created_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        project_name_key(name),
                        project_type,
                        to_float(request.form.get("default_amount")) if project_type == "invoice" else 0,
                        to_float(request.form.get("unit_price")),
                        to_float(request.form.get("tax_rate")) if project_type == "invoice" else 0,
                        1 if request.form.get("is_active", "1") == "1" else 0,
                        now(),
                    ),
                )
                db().commit()
                flash("项目已创建。", "success")
            except IntegrityError:
                db().rollback()
                flash("同一项目类型下已存在同名项目，不能重复创建。", "error")
            return redirect(url_for("projects"))
        merge_duplicate_projects(db())
        db().commit()
        q = request.args.get("q", "").strip()
        project_type = request.args.get("project_type", "").strip()
        if project_type not in project_type_labels:
            project_type = ""
        clauses = ["1 = 1"]
        params = []
        if q:
            clauses.append("name like ?")
            params.append(f"%{q}%")
        if project_type:
            clauses.append("project_type = ?")
            params.append(project_type)
        rows = db().execute(
            f"select * from projects where {' and '.join(clauses)} order by is_active desc, name",
            params,
        ).fetchall()
        invoice_project_names = sorted(
            {row["name"] for row in db().execute(
                "select name from projects where project_type = 'invoice' and is_active = 1 order by name"
            ).fetchall()}
            | set(customer_reimbursement_invoice_projects)
        )
        return render_template(
            "projects.html",
            projects=rows,
            q=q,
            selected_project_type=project_type,
            expense_map=db().execute(
                "select id, expense_project_name, settlement_field, invoice_project_name from expense_settlement_invoice_map order by expense_project_name"
            ).fetchall(),
            settlement_field_labels=settlement_expense_field_labels,
            invoice_project_names=invoice_project_names,
            can_edit_map=has_action_permission("projects", "edit"),
        )

    @login_required
    def edit_project(project_id):
        project = db().execute("select * from projects where id = ?", (project_id,)).fetchone()
        if not project:
            abort(404)
        if request.method == "POST":
            name = normalized_project_name(request.form.get("name"))
            project_type = request.form.get("project_type", "invoice")
            if project_type not in project_type_labels:
                project_type = "invoice"
            if not name:
                flash("项目名称不能为空。", "error")
                return redirect(url_for("edit_project", project_id=project_id))
            if project_name_exists(project_type, name, excluded_project_id=project_id):
                flash("同一项目类型下已存在同名项目，不能重复保存。", "error")
                return redirect(url_for("edit_project", project_id=project_id))
            invoice_used = db().execute(
                "select count(*) as count from invoice_items where project_id = ?",
                (project_id,),
            ).fetchone()["count"]
            expense_used = db().execute(
                "select count(*) as count from expense_items where project_id = ?",
                (project_id,),
            ).fetchone()["count"]
            if project_type != project["project_type"] and (invoice_used or expense_used):
                flash("已被发票或报销使用的项目不能切换类型，可以新建另一个项目。", "error")
                return redirect(url_for("edit_project", project_id=project_id))
            try:
                db().execute(
                    """
                    update projects set name = ?, name_key = ?, project_type = ?, default_amount = ?, unit_price = ?, tax_rate = ?, is_active = ?
                    where id = ?
                    """,
                    (
                        name,
                        project_name_key(name),
                        project_type,
                        to_float(request.form.get("default_amount")) if project_type == "invoice" else 0,
                        to_float(request.form.get("unit_price")),
                        to_float(request.form.get("tax_rate")) if project_type == "invoice" else 0,
                        1 if request.form.get("is_active", "1") == "1" else 0,
                        project_id,
                    ),
                )
                db().commit()
                flash("项目已更新。", "success")
            except IntegrityError:
                db().rollback()
                flash("同一项目类型下已存在同名项目，不能重复保存。", "error")
            return redirect(url_for("projects"))
        return render_template("project_form.html", project=project)

    @login_required
    def delete_project(project_id):
        invoice_used = db().execute("select count(*) as count from invoice_items where project_id = ?", (project_id,)).fetchone()["count"]
        expense_used = db().execute("select count(*) as count from expense_items where project_id = ?", (project_id,)).fetchone()["count"]
        if invoice_used or expense_used:
            flash("这个项目已有发票或报销记录，不能删除。可以停用该项目。", "error")
            return redirect(url_for("projects"))
        db().execute("delete from projects where id = ?", (project_id,))
        db().commit()
        flash("项目已删除。", "success")
        return redirect(url_for("projects"))

    @login_required
    def countries():
        if request.method == "POST":
            code = request.form.get("code", "").strip().upper()
            region_code = request.form.get("region_code", "").strip().lower()
            if not re.fullmatch(r"[A-Z]{2,3}", code) or not re.fullmatch(r"[a-z][a-z0-9_-]*", region_code):
                flash("国家代码应为 2-3 位大写字母，区域代码应使用小写字母、数字、下划线或连字符。", "error")
                return redirect(url_for("countries"))
            translations = []
            seen_languages = set()
            for language_code, name, region_name in zip(
                request.form.getlist("language_code"),
                request.form.getlist("translation_name"),
                request.form.getlist("translation_region_name"),
            ):
                language_code = language_code.strip()
                name = name.strip()
                region_name = region_name.strip()
                if not language_code or not name or not region_name or language_code in seen_languages:
                    continue
                seen_languages.add(language_code)
                translations.append((language_code, name, region_name))
            if not translations:
                flash("请至少填写一种语言的国家名称和区域名称。", "error")
                return redirect(url_for("countries"))
            db().execute(
                """
                insert into countries (code, region_code, is_active, sort_order, created_at)
                values (?, ?, ?, ?, ?)
                on conflict(code) do update set
                    region_code = excluded.region_code,
                    is_active = excluded.is_active,
                    sort_order = excluded.sort_order
                """,
                (
                    code,
                    region_code,
                    1 if request.form.get("is_active", "1") == "1" else 0,
                    int(request.form.get("sort_order") or 0),
                    now(),
                ),
            )
            db().execute("delete from country_translations where country_code = ?", (code,))
            db().executemany(
                """
                insert into country_translations (country_code, language_code, name, region_name)
                values (?, ?, ?, ?)
                """,
                [(code, language_code, name, region_name) for language_code, name, region_name in translations],
            )
            db().commit()
            flash("国家配置已保存。", "success")
            return redirect(url_for("countries"))
        rows = country_rows(include_inactive=True)
        translations = {country["code"]: country_translations(country["code"]) for country in rows}
        return render_template("countries.html", countries=rows, translations=translations)

    app.add_url_rule('/work-order-types', endpoint='work_order_types', view_func=work_order_types, methods=['GET', 'POST'])
    app.add_url_rule('/work-order-types/<int:type_id>/edit', endpoint='edit_work_order_type', view_func=edit_work_order_type, methods=['GET', 'POST'])
    app.add_url_rule('/work-order-types/<int:type_id>/delete', endpoint='delete_work_order_type', view_func=delete_work_order_type, methods=['POST'])
    app.add_url_rule('/projects', endpoint='projects', view_func=projects, methods=['GET', 'POST'])
    app.add_url_rule('/projects/<int:project_id>/edit', endpoint='edit_project', view_func=edit_project, methods=['GET', 'POST'])
    app.add_url_rule('/projects/<int:project_id>/delete', endpoint='delete_project', view_func=delete_project, methods=['POST'])
    app.add_url_rule('/countries', endpoint='countries', view_func=countries, methods=['GET', 'POST'])
    return {
        "work_order_types": work_order_types,
        "edit_work_order_type": edit_work_order_type,
        "delete_work_order_type": delete_work_order_type,
        "projects": projects,
        "edit_project": edit_project,
        "delete_project": delete_project,
        "countries": countries,
    }
