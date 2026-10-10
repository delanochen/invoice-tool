"""Explicit registration for customer and site endpoints."""

from flask import abort, flash, g, redirect, render_template, request, session, url_for

from database import IntegrityError
from invoice_tool.customers.services import (
    localized_state_name,
    state_code_from_address,
    state_names_for_language,
)


def register_customer_routes(
    app,
    *,
    db,
    now,
    login_required,
    normalized_role,
    is_external_manager,
    is_external_user,
    posted_payment_term_id,
    payment_term_rows,
    log_action,
    has_action_permission,
    parse_coordinate_pair,
    current_language,
    can_access_buyer,
    geocoder_version,
    country_rows,
    country_from_form,
    normalized_address,
    next_client_number,
    next_buyer_number,
    next_owner_number,
    next_manufacturer_number,
    unknown_owner,
    owner_options,
    manufacturer_options,
    manufacturer_from_form,
    import_buyers_from_file,
):
    @login_required
    def clients():
        if normalized_role() in {"employee", "external_employee"}:
            abort(403)
        if is_external_manager() and not g.user["client_id"]:
            flash("外部用户尚未绑定客户。", "error")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            if is_external_user():
                abort(403)
            try:
                payment_term_id = posted_payment_term_id()
                db().execute(
                    """
                    insert into clients (
                        client_number, name, short_name, contact_name, email, address,
                        country, payment_term_id, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        next_client_number(),
                        request.form.get("name", "").strip(),
                        request.form.get("short_name", "").strip() or request.form.get("name", "").strip(),
                        request.form.get("contact_name", "").strip(),
                        request.form.get("email", "").strip(),
                        request.form.get("address", "").strip(),
                        request.form.get("country", "China").strip() or "China",
                        payment_term_id,
                        now(),
                    ),
                )
                db().commit()
                flash("客户已创建。", "success")
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
            except IntegrityError:
                flash("客户编号重复，请重试。", "error")
            return redirect(url_for("clients"))
        q = request.args.get("q", "").strip()
        if is_external_manager():
            rows = db().execute(
                """
                select clients.*, payment_terms.name as payment_term_name
                from clients left join payment_terms on payment_terms.id = clients.payment_term_id
                where clients.id = ?
                """,
                (g.user["client_id"],),
            ).fetchall()
        else:
            params = []
            where = ""
            if q:
                where = """
                where clients.client_number like ? or clients.name like ? or clients.short_name like ?
                   or clients.contact_name like ? or clients.email like ?
                """
                params = [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
            rows = db().execute(
                f"""
                select clients.*, payment_terms.name as payment_term_name
                from clients left join payment_terms on payment_terms.id = clients.payment_term_id
                {where}
                order by clients.client_number asc
                """,
                params,
            ).fetchall()
        return render_template(
            "clients.html", clients=rows, q=q, payment_terms=payment_term_rows()
        )

    @login_required
    def edit_client(client_id):
        if normalized_role() == "employee":
            abort(403)
        if is_external_user():
            abort(403)
        client = db().execute("select * from clients where id = ?", (client_id,)).fetchone()
        if not client:
            abort(404)
        if request.method == "POST":
            client_number = request.form.get("client_number", "").strip()
            if len(client_number) != 5 or not client_number.isdigit():
                flash("客户编号必须是 5 位数字。", "error")
                return redirect(url_for("edit_client", client_id=client_id))
            try:
                payment_term_id = posted_payment_term_id(allow_inactive=True)
                db().execute(
                    """
                    update clients
                    set client_number = ?, name = ?, short_name = ?, contact_name = ?, email = ?,
                        address = ?, country = ?, payment_term_id = ?
                    where id = ?
                    """,
                    (
                        client_number,
                        request.form.get("name", "").strip(),
                        request.form.get("short_name", "").strip() or request.form.get("name", "").strip(),
                        request.form.get("contact_name", "").strip(),
                        request.form.get("email", "").strip(),
                        request.form.get("address", "").strip(),
                        request.form.get("country", "China").strip() or "China",
                        payment_term_id,
                        client_id,
                    ),
                )
                log_action(
                    "update", "client", client_id, client_number,
                    f"更新客户资料；账期方案ID：{payment_term_id}",
                )
                db().commit()
                flash("客户资料已更新。", "success")
            except ValueError as error:
                db().rollback()
                flash(str(error), "error")
                return redirect(url_for("edit_client", client_id=client_id))
            except IntegrityError:
                flash("客户编号重复，请换一个编号。", "error")
                return redirect(url_for("edit_client", client_id=client_id))
            return redirect(url_for("clients"))
        return render_template("client_form.html", client=client)

    @login_required
    def delete_client(client_id):
        if normalized_role() == "employee":
            abort(403)
        if is_external_user():
            abort(403)
        invoice_count = db().execute("select count(*) as count from invoices where client_id = ?", (client_id,)).fetchone()["count"]
        contract_count = db().execute("select count(*) as count from contracts where client_id = ?", (client_id,)).fetchone()["count"]
        if invoice_count or contract_count:
            flash("这个客户已有合同或发票记录，不能删除。可以编辑客户资料以保留历史记录。", "error")
            return redirect(url_for("clients"))
        db().execute("delete from clients where id = ?", (client_id,))
        db().commit()
        flash("客户已删除。", "success")
        return redirect(url_for("clients"))

    @login_required
    def owners():
        required_action = "create" if request.method == "POST" else "view"
        if not has_action_permission("owners", required_action):
            abort(403)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("请填写业主名称。", "error")
                return redirect(url_for("owners"))
            if db().execute("select 1 from owners where lower(trim(name)) = lower(trim(?))", (name,)).fetchone():
                flash("业主名称已存在。", "error")
                return redirect(url_for("owners"))
            try:
                db().execute(
                    "insert into owners (owner_number, name, created_at) values (?, ?, ?)",
                    (next_owner_number(), name, now()),
                )
                db().commit()
                flash("业主已创建。", "success")
            except IntegrityError:
                db().rollback()
                flash("业主编号重复，请重试。", "error")
            return redirect(url_for("owners"))
        q = request.args.get("q", "").strip()
        params = []
        where = ""
        if q:
            where = "where owner_number like ? or name like ?"
            params = [f"%{q}%", f"%{q}%"]
        rows = db().execute(
            f"select * from owners {where} order by case when name = '未知' then 0 else 1 end, owner_number asc",
            params,
        ).fetchall()
        return render_template("owners.html", owners=rows, q=q)

    @login_required
    def edit_owner(owner_id):
        if not has_action_permission("owners", "edit"):
            abort(403)
        owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
        if not owner:
            abort(404)
        owner_number = request.form.get("owner_number", "").strip().upper()
        name = request.form.get("name", "").strip()
        if not owner_number or not name:
            flash("请填写业主编号和名称。", "error")
            return redirect(url_for("owners"))
        if db().execute(
            "select 1 from owners where id != ? and lower(trim(name)) = lower(trim(?))",
            (owner_id, name),
        ).fetchone():
            flash("业主名称已存在。", "error")
            return redirect(url_for("owners"))
        try:
            db().execute(
                "update owners set owner_number = ?, name = ? where id = ?",
                (owner_number, name, owner_id),
            )
            db().execute(
                "update buyers set owner = ? where owner_id = ?",
                (name, owner_id),
            )
            db().commit()
            flash("业主资料已更新。", "success")
        except IntegrityError:
            db().rollback()
            flash("业主编号重复，请换一个编号。", "error")
        return redirect(url_for("owners"))

    @login_required
    def delete_owner(owner_id):
        if not has_action_permission("owners", "delete"):
            abort(403)
        owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
        if not owner:
            abort(404)
        if owner["name"] == "未知":
            flash("默认业主不能删除。", "error")
            return redirect(url_for("owners"))
        used = db().execute(
            "select count(*) as count from buyers where owner_id = ?",
            (owner_id,),
        ).fetchone()["count"]
        if used:
            flash("这个业主已有站点，不能删除。可以编辑业主资料。", "error")
            return redirect(url_for("owners"))
        db().execute("delete from owners where id = ?", (owner_id,))
        db().commit()
        flash("业主已删除。", "success")
        return redirect(url_for("owners"))

    @login_required
    def manufacturers():
        required_action = "create" if request.method == "POST" else "view"
        if not has_action_permission("manufacturers", required_action):
            abort(403)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("请填写厂家名称。", "error")
                return redirect(url_for("manufacturers"))
            if db().execute("select 1 from manufacturers where lower(trim(name)) = lower(trim(?))", (name,)).fetchone():
                flash("厂家名称已存在。", "error")
                return redirect(url_for("manufacturers"))
            try:
                db().execute(
                    "insert into manufacturers (manufacturer_number, name, created_at) values (?, ?, ?)",
                    (next_manufacturer_number(), name, now()),
                )
                db().commit()
                flash("厂家已创建。", "success")
            except IntegrityError:
                db().rollback()
                flash("厂家编号重复，请重试。", "error")
            return redirect(url_for("manufacturers"))
        q = request.args.get("q", "").strip()
        params = []
        where = ""
        if q:
            where = "where manufacturer_number like ? or name like ?"
            params = [f"%{q}%", f"%{q}%"]
        rows = db().execute(
            f"select * from manufacturers {where} order by manufacturer_number asc",
            params,
        ).fetchall()
        return render_template("manufacturers.html", manufacturers=rows, q=q)

    @login_required
    def edit_manufacturer(manufacturer_id):
        if not has_action_permission("manufacturers", "edit"):
            abort(403)
        manufacturer = db().execute("select * from manufacturers where id = ?", (manufacturer_id,)).fetchone()
        if not manufacturer:
            abort(404)
        manufacturer_number = request.form.get("manufacturer_number", "").strip().upper()
        name = request.form.get("name", "").strip()
        if not manufacturer_number or not name:
            flash("请填写厂家编号和名称。", "error")
            return redirect(url_for("manufacturers"))
        if db().execute(
            "select 1 from manufacturers where id != ? and lower(trim(name)) = lower(trim(?))",
            (manufacturer_id, name),
        ).fetchone():
            flash("厂家名称已存在。", "error")
            return redirect(url_for("manufacturers"))
        try:
            db().execute(
                "update manufacturers set manufacturer_number = ?, name = ? where id = ?",
                (manufacturer_number, name, manufacturer_id),
            )
            db().execute(
                "update buyers set equipment_manufacturer = ? where manufacturer_id = ?",
                (name, manufacturer_id),
            )
            db().commit()
            flash("厂家资料已更新。", "success")
        except IntegrityError:
            db().rollback()
            flash("厂家编号重复，请换一个编号。", "error")
        return redirect(url_for("manufacturers"))

    @login_required
    def delete_manufacturer(manufacturer_id):
        if not has_action_permission("manufacturers", "delete"):
            abort(403)
        manufacturer = db().execute("select * from manufacturers where id = ?", (manufacturer_id,)).fetchone()
        if not manufacturer:
            abort(404)
        used = db().execute(
            "select count(*) as count from buyers where manufacturer_id = ?",
            (manufacturer_id,),
        ).fetchone()["count"]
        if used:
            flash("这个厂家已有站点，不能删除。可以编辑厂家资料。", "error")
            return redirect(url_for("manufacturers"))
        db().execute("delete from manufacturers where id = ?", (manufacturer_id,))
        db().commit()
        flash("厂家已删除。", "success")
        return redirect(url_for("manufacturers"))

    @login_required
    def buyers():
        required_action = "create" if request.method == "POST" else "view"
        if not has_action_permission("buyers", required_action):
            abort(403)
        owners_rows = owner_options()
        manufacturers_rows = manufacturer_options()
        countries_rows = country_rows()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            country = country_from_form()
            owner = None
            owner_id = int(request.form["owner_id"]) if request.form.get("owner_id", "").isdigit() else None
            if owner_id:
                owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
                if not owner:
                    flash("请选择有效的业主。", "error")
                    return redirect(url_for("buyers"))
            else:
                owner = unknown_owner()
                owner_id = owner["id"]
            manufacturer = manufacturer_from_form()
            if request.form.get("manufacturer_id") and not manufacturer:
                flash("请选择有效的厂家。", "error")
                return redirect(url_for("buyers"))
            detailed_address = request.form.get("detailed_address", "").strip()
            if not name or not detailed_address:
                flash("请填写站点名称和详细地址。", "error")
                return redirect(url_for("buyers"))
            try:
                manual_coordinates = parse_coordinate_pair(request.form.get("coordinates"))
            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("buyers"))
            client_id = g.user["client_id"] if is_external_manager() else (
                int(request.form["client_id"]) if request.form.get("client_id", "").isdigit() else None
            )
            existing_site = db().execute(
                """
                select buyer_number from buyers
                where coalesce(client_id, 0) = coalesce(?, 0)
                  and lower(trim(name)) = lower(trim(?))
                  and lower(trim(detailed_address)) = lower(trim(?))
                """,
                (client_id, name, detailed_address),
            ).fetchone()
            if existing_site:
                flash(f"站点已存在，编号：{existing_site['buyer_number']}。", "error")
                return redirect(url_for("buyers"))
            try:
                db().execute(
                    """
                    insert into buyers (
                        buyer_number, client_id, country, country_code, name, owner_id, owner, manufacturer_id, contact_name, contact_details,
                        email, site_size, detailed_address, equipment_manufacturer, state_code, latitude, longitude, geocode_address,
                        geocode_status, geocode_attempted_at, geocode_version, manual_coordinates, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        next_buyer_number(),
                        client_id,
                        country["code"],
                        country["code"],
                        name,
                        owner_id,
                        owner["name"] if owner else "",
                        manufacturer["id"] if manufacturer else None,
                        request.form.get("contact_name", "").strip(),
                        request.form.get("contact_details", "").strip(),
                        request.form.get("email", "").strip(),
                        request.form.get("site_size", "").strip(),
                        detailed_address,
                        manufacturer["name"] if manufacturer else "",
                        state_code_from_address(detailed_address)
                        or request.form.get("state_code", "").strip().upper(),
                        manual_coordinates[0] if manual_coordinates else None,
                        manual_coordinates[1] if manual_coordinates else None,
                        detailed_address if manual_coordinates else None,
                        "success" if manual_coordinates else "pending",
                        now() if manual_coordinates else None,
                        geocoder_version if manual_coordinates else None,
                        1 if manual_coordinates else 0,
                        now(),
                    ),
                )
                db().commit()
                flash("站点已创建。", "success")
            except IntegrityError:
                db().rollback()
                flash("站点编号重复，请重试。", "error")
            return redirect(url_for("buyers"))
        q = request.args.get("q", "").strip()
        sort = request.args.get("sort", "buyer_number")
        direction = request.args.get("direction", "asc").lower()
        buyer_sort_columns = {
            "buyer_number": "buyers.buyer_number",
            "country_name": "coalesce(country_local.name, country_zh.name, country_en.name, buyers.country, buyers.country_code)",
            "name": "buyers.name",
            "owner_name": "coalesce(owners.name, buyers.owner)",
            "contact_name": "buyers.contact_name",
            "contact_details": "buyers.contact_details",
            "email": "buyers.email",
            "site_size": "buyers.site_size",
            "equipment_manufacturer": "buyers.equipment_manufacturer",
            "state_code": "buyers.state_code",
            "detailed_address": "buyers.detailed_address",
        }
        if sort not in buyer_sort_columns:
            sort = "buyer_number"
        if direction not in {"asc", "desc"}:
            direction = "asc"
        order_direction = "desc" if direction == "desc" else "asc"
        order_by = f"lower(coalesce({buyer_sort_columns[sort]}, '')) {order_direction}, buyers.name asc, buyers.buyer_number asc"
        params = []
        clauses = []
        if is_external_manager():
            clauses.append("buyers.client_id = ?")
            params.append(g.user["client_id"])
        if q:
            clauses.append(
                """(
                buyers.buyer_number like ? or buyers.name like ? or coalesce(owners.name, buyers.owner) like ? or buyers.contact_name like ?
                or buyers.contact_details like ? or buyers.email like ? or buyers.site_size like ? or buyers.detailed_address like ? or buyers.equipment_manufacturer like ?
                )"""
            )
            params = [f"%{q}%"] * 9
            if is_external_manager():
                params.insert(0, g.user["client_id"])
        where = f"where {' and '.join(clauses)}" if clauses else ""
        rows = db().execute(
            f"""
            select buyers.*, coalesce(owners.name, buyers.owner) as owner_name,
                   owners.owner_number as owner_number,
                   coalesce(country_local.name, country_zh.name, country_en.name, buyers.country, buyers.country_code) as country_name
            from buyers
            left join owners on owners.id = buyers.owner_id
            left join country_translations country_local
              on country_local.country_code = buyers.country_code and country_local.language_code = ?
            left join country_translations country_zh
              on country_zh.country_code = buyers.country_code and country_zh.language_code = 'zh-CN'
            left join country_translations country_en
              on country_en.country_code = buyers.country_code and country_en.language_code = 'en'
            {where}
            order by {order_by}
            """,
            [current_language(), *params],
        ).fetchall()
        clients_rows = db().execute("select id, client_number, name from clients order by client_number").fetchall()
        return render_template(
            "buyers.html",
            buyers=rows,
            clients=clients_rows,
            owners=owners_rows,
            manufacturers=manufacturers_rows,
            countries=countries_rows,
            q=q,
            sort=sort,
            direction=direction,
            import_result=session.pop("buyer_import_result", None),
            state_names=state_names_for_language(current_language()),
        )

    @login_required
    def import_buyers():
        if not has_action_permission("buyers", "create"):
            abort(403)
        uploaded_file = request.files.get("import_file")
        if not uploaded_file or not uploaded_file.filename:
            flash("请选择要导入的站点文件。", "error")
            return redirect(url_for("buyers"))
        client_id = g.user["client_id"] if is_external_manager() else (
            int(request.form["client_id"]) if request.form.get("client_id", "").isdigit() else None
        )
        try:
            result = import_buyers_from_file(uploaded_file, client_id)
            detail_lines = result["details"][:20]
            if len(result["details"]) > 20:
                detail_lines.append(f"还有 {len(result['details']) - 20} 条明细未在弹窗中显示，请查看操作日志。")
            session["buyer_import_result"] = {
                **result,
                "details": detail_lines,
            }
            log_summary = (
                f"导入站点：文件 {uploaded_file.filename}；总行数 {result['total']}；"
                f"新增 {result['created']}；跳过 {result['skipped']}；错误 {result['errors']}；"
                f"自动创建业主 {result['owners_created']}。"
            )
            if result["details"]:
                log_summary = f"{log_summary} 明细：{' | '.join(result['details'])}"
            log_action("import", "buyer", None, uploaded_file.filename, log_summary)
            db().commit()
            flash("站点导入已完成。", "success")
        except ValueError as error:
            db().rollback()
            flash(str(error), "error")
        except IntegrityError:
            db().rollback()
            flash("导入失败：站点编号或业主编号冲突，请检查导入文件后重试。", "error")
        return redirect(url_for("buyers"))

    @login_required
    def edit_buyer(buyer_id):
        if not has_action_permission("buyers", "edit"):
            abort(403)
        buyer = db().execute("select * from buyers where id = ?", (buyer_id,)).fetchone()
        if not buyer:
            abort(404)
        if not can_access_buyer(buyer):
            abort(403)
        if request.method == "POST":
            buyer_number = request.form.get("buyer_number", "").strip().upper()
            name = request.form.get("name", "").strip()
            country = country_from_form(buyer["country_code"] or "US")
            owner = None
            owner_id = int(request.form["owner_id"]) if request.form.get("owner_id", "").isdigit() else None
            if owner_id:
                owner = db().execute("select * from owners where id = ?", (owner_id,)).fetchone()
                if not owner:
                    flash("请选择有效的业主。", "error")
                    return redirect(url_for("edit_buyer", buyer_id=buyer_id))
            else:
                owner = unknown_owner()
                owner_id = owner["id"]
            manufacturer = manufacturer_from_form()
            if request.form.get("manufacturer_id") and not manufacturer:
                flash("请选择有效的厂家。", "error")
                return redirect(url_for("edit_buyer", buyer_id=buyer_id))
            detailed_address = request.form.get("detailed_address", "").strip()
            if not buyer_number or not name or not detailed_address:
                flash("请填写编号、站点名称和详细地址。", "error")
                return redirect(url_for("edit_buyer", buyer_id=buyer_id))
            try:
                pasted_coordinates = parse_coordinate_pair(request.form.get("coordinates"))
            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("edit_buyer", buyer_id=buyer_id))
            clear_manual_coordinates = request.form.get("clear_manual_coordinates") == "1"
            address_changed = normalized_address(buyer["detailed_address"]) != normalized_address(detailed_address)
            try:
                db().execute(
                    """
                    update buyers
                    set buyer_number = ?, client_id = ?, country = ?, country_code = ?, name = ?, owner_id = ?, owner = ?, contact_name = ?,
                        manufacturer_id = ?, contact_details = ?, email = ?, site_size = ?, detailed_address = ?, equipment_manufacturer = ?,
                        state_code = ?
                    where id = ?
                    """,
                    (
                        buyer_number,
                        g.user["client_id"] if is_external_manager() else (
                            int(request.form["client_id"]) if request.form.get("client_id", "").isdigit() else buyer["client_id"]
                        ),
                        country["code"],
                        country["code"],
                        name,
                        owner_id,
                        owner["name"] if owner else "",
                        request.form.get("contact_name", "").strip(),
                        manufacturer["id"] if manufacturer else None,
                        request.form.get("contact_details", "").strip(),
                        request.form.get("email", "").strip(),
                        request.form.get("site_size", "").strip(),
                        detailed_address,
                        manufacturer["name"] if manufacturer else "",
                        state_code_from_address(detailed_address)
                        or request.form.get("state_code", "").strip().upper(),
                        buyer_id,
                    ),
                )
                db().execute(
                    """
                    update service_orders
                    set client_name = ?, buyer_contact_name = ?, buyer_contact_details = ?
                    where buyer_id = ?
                    """,
                    (
                        name,
                        request.form.get("contact_name", "").strip(),
                        request.form.get("contact_details", "").strip(),
                        buyer_id,
                    ),
                )
                if clear_manual_coordinates:
                    db().execute(
                        """
                        update buyers
                        set latitude = null, longitude = null, geocode_address = null,
                            geocode_status = 'pending', geocode_attempted_at = null, geocode_version = null,
                            manual_coordinates = 0
                        where id = ?
                        """,
                        (buyer_id,),
                    )
                elif pasted_coordinates:
                    db().execute(
                        """
                        update buyers
                        set latitude = ?, longitude = ?, geocode_address = ?, geocode_status = 'success',
                            geocode_attempted_at = ?, geocode_version = ?, manual_coordinates = 1
                        where id = ?
                        """,
                        (pasted_coordinates[0], pasted_coordinates[1], detailed_address, now(), geocoder_version, buyer_id),
                    )
                elif buyer["manual_coordinates"]:
                    db().execute(
                        """
                        update buyers
                        set geocode_address = ?, geocode_status = 'success', geocode_version = ?
                        where id = ?
                        """,
                        (detailed_address, geocoder_version, buyer_id),
                    )
                elif address_changed:
                    db().execute(
                        """
                        update buyers
                        set latitude = null, longitude = null, geocode_address = null,
                            geocode_status = 'pending', geocode_attempted_at = null, geocode_version = null,
                            manual_coordinates = 0
                        where id = ?
                        """,
                        (buyer_id,),
                    )
                db().commit()
                flash("站点资料已更新。", "success")
                return redirect(url_for("buyers"))
            except IntegrityError:
                db().rollback()
                flash("站点编号重复，请换一个编号。", "error")
        clients_rows = db().execute("select id, client_number, name from clients order by client_number").fetchall()
        owners_rows = owner_options()
        return render_template(
            "buyer_form.html",
            buyer=buyer,
            clients=clients_rows,
            owners=owners_rows,
            manufacturers=manufacturer_options(),
            countries=country_rows(),
            state_names=state_names_for_language(current_language()),
        )

    @login_required
    def delete_buyer(buyer_id):
        if not has_action_permission("buyers", "delete"):
            abort(403)
        buyer = db().execute("select * from buyers where id = ?", (buyer_id,)).fetchone()
        if not buyer:
            abort(404)
        if not can_access_buyer(buyer):
            abort(403)
        used = db().execute(
            "select count(*) as count from service_orders where buyer_id = ?",
            (buyer_id,),
        ).fetchone()["count"]
        if used:
            flash("这个站点已有工单，不能删除。可以编辑站点资料。", "error")
            return redirect(url_for("buyers"))
        db().execute("delete from buyers where id = ?", (buyer_id,))
        db().commit()
        flash("站点已删除。", "success")
        return redirect(url_for("buyers"))

    app.add_url_rule('/clients', endpoint='clients', view_func=clients, methods=['GET', 'POST'])
    app.add_url_rule('/clients/<int:client_id>/edit', endpoint='edit_client', view_func=edit_client, methods=['GET', 'POST'])
    app.add_url_rule('/clients/<int:client_id>/delete', endpoint='delete_client', view_func=delete_client, methods=['POST'])
    app.add_url_rule('/owners', endpoint='owners', view_func=owners, methods=['GET', 'POST'])
    app.add_url_rule('/owners/<int:owner_id>/edit', endpoint='edit_owner', view_func=edit_owner, methods=['POST'])
    app.add_url_rule('/owners/<int:owner_id>/delete', endpoint='delete_owner', view_func=delete_owner, methods=['POST'])
    app.add_url_rule('/manufacturers', endpoint='manufacturers', view_func=manufacturers, methods=['GET', 'POST'])
    app.add_url_rule('/manufacturers/<int:manufacturer_id>/edit', endpoint='edit_manufacturer', view_func=edit_manufacturer, methods=['POST'])
    app.add_url_rule('/manufacturers/<int:manufacturer_id>/delete', endpoint='delete_manufacturer', view_func=delete_manufacturer, methods=['POST'])
    app.add_url_rule('/buyers', endpoint='buyers', view_func=buyers, methods=['GET', 'POST'])
    app.add_url_rule('/buyers/import', endpoint='import_buyers', view_func=import_buyers, methods=['POST'])
    app.add_url_rule('/buyers/<int:buyer_id>/edit', endpoint='edit_buyer', view_func=edit_buyer, methods=['GET', 'POST'])
    app.add_url_rule('/buyers/<int:buyer_id>/delete', endpoint='delete_buyer', view_func=delete_buyer, methods=['POST'])
    return {
        "clients": clients,
        "edit_client": edit_client,
        "delete_client": delete_client,
        "owners": owners,
        "edit_owner": edit_owner,
        "delete_owner": delete_owner,
        "manufacturers": manufacturers,
        "edit_manufacturer": edit_manufacturer,
        "delete_manufacturer": delete_manufacturer,
        "buyers": buyers,
        "import_buyers": import_buyers,
        "edit_buyer": edit_buyer,
        "delete_buyer": delete_buyer,
    }
