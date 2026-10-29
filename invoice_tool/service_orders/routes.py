"""Work-order CRUD endpoints with explicit application dependencies."""
import shutil
from .settlement_dates import optional_settlement_date, exact_settlement_date_filter
from decimal import Decimal
from flask import abort, flash, g, redirect, render_template, request, url_for


def register_service_order_routes(app, *,
    EXPENSE_STATUS_LABELS,
    STATUS_LABELS,
    can_create_service_order,
    can_delete_service_order,
    can_manage_buyers,
    can_view_customer_reimbursement,
    country_from_form,
    country_rows,
    current_language,
    customer_reimbursement_item_expense_amount,
    customer_reimbursement_items,
    db,
    ensure_service_order_picture_folder,
    expense_access_filter,
    is_external_employee,
    is_external_manager,
    is_external_user,
    is_manager,
    latest_customer_reimbursement,
    log_action,
    login_required,
    manufacturer_from_form,
    manufacturer_options,
    next_service_order_number,
    normalized_role,
    now,
    order_employee_settlement_rows,
    report_duration_hours,
    require_service_order,
    service_order_delete_blockers,
    service_order_dependency_counts,
    service_order_photo_folder,
    service_order_photo_summary,
    sync_order_commercial_document,
):
    @login_required
    def service_orders():
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        client_id = request.args.get("client_id", "").strip()
        buyer_id = request.args.get("buyer_id", "").strip()
        manufacturer_id = request.args.get("manufacturer_id", "").strip()
        work_order_type_id = request.args.get("work_order_type_id", "").strip()
        settlement = request.args.get("settlement", "").strip()
        invoice = request.args.get("invoice", "").strip()
        payment = request.args.get("payment", "").strip()
        clauses = ["1 = 1"]
        params = []
        if is_external_manager():
            if not g.user["client_id"]:
                clauses.append("1 = 0")
            else:
                clauses.append("service_orders.client_id = ?")
                params.append(g.user["client_id"])
        elif is_external_employee():
            clauses.append(
                "service_orders.id in (select service_order_id from user_service_orders where user_id = ?)"
            )
            params.append(g.user["id"])
        if q:
            clauses.append(
                "(service_orders.order_number like ? or service_orders.client_name like ? or clients.name like ? "
                "or coalesce(owners.name, buyers.owner) like ? or service_orders.client_order_number like ? "
                "or service_orders.site_address like ? or coalesce(order_manufacturers.name, buyers.equipment_manufacturer) like ? "
                "or work_order_types.name like ?)"
            )
            params.extend([f"%{q}%"] * 8)
        if status in {"not_started", "open", "settling", "closed"}:
            clauses.append("service_orders.status = ?")
            params.append(status)
        if client_id.isdigit():
            clauses.append("service_orders.client_id = ?")
            params.append(int(client_id))
        if manufacturer_id.isdigit():
            clauses.append("service_orders.manufacturer_id = ?")
            params.append(int(manufacturer_id))
        if work_order_type_id.isdigit():
            clauses.append("service_orders.work_order_type_id = ?")
            params.append(int(work_order_type_id))
        if settlement == "yes":
            clauses.append("exists (select 1 from customer_reimbursements crf where crf.service_order_id = service_orders.id)")
        elif settlement == "no":
            clauses.append("not exists (select 1 from customer_reimbursements crf where crf.service_order_id = service_orders.id)")
        if invoice == "yes":
            clauses.append("exists (select 1 from invoices ivf where ivf.service_order_id = service_orders.id and ivf.status != 'void')")
        elif invoice == "no":
            clauses.append("not exists (select 1 from invoices ivf where ivf.service_order_id = service_orders.id and ivf.status != 'void')")
        if payment == "paid":
            clauses.append("exists (select 1 from invoices pv where pv.service_order_id = service_orders.id and pv.status != 'void') and not exists (select 1 from invoices pu where pu.service_order_id = service_orders.id and pu.status != 'void' and pu.paid_at is null)")
        elif payment == "unpaid":
            clauses.append("exists (select 1 from invoices pu where pu.service_order_id = service_orders.id and pu.status != 'void' and pu.paid_at is null)")
        settlement_filters = exact_settlement_date_filter(request.args, clauses, params)
        if buyer_id.isdigit():
            clauses.append("service_orders.buyer_id = ?")
            params.append(int(buyer_id))
            # 带 buyer_id 过滤时把站点名传给模板：工作区标签页标题取自页面 <title>，
            # 不传的话「业主查询 → 工单查看」打开的标签和菜单里的「工单」标签
            # 都叫「工单」，用户分不清哪个是哪个（也无法靠标题区分）。
            buyer_row = db().execute(
                "select buyers.name, coalesce(owners.name, buyers.owner) as owner_name"
                " from buyers left join owners on owners.id = buyers.owner_id where buyers.id = ?",
                (int(buyer_id),),
            ).fetchone()
            buyer_name = (buyer_row["name"] or buyer_row["owner_name"] or "") if buyer_row else ""
        else:
            buyer_name = ""
        rows = db().execute(
            f"""
            select service_orders.*,
                   clients.name as customer_name,
                   work_order_types.name as work_order_type_name,
                   coalesce(owners.name, buyers.owner) as buyer_owner,
                   coalesce(order_manufacturers.name, buyers.equipment_manufacturer) as buyer_equipment_manufacturer,
                   contracts.contract_number,
                   quotations.quotation_number,
                   quotations.customer as quotation_customer,
                   count(distinct service_reports.id) as report_count,
                   -- 报销：该工单所有报销单的明细条目数之和（与「日报」列同为条数口径）。
                   -- 用相关子查询而不是 join expenses，免得 group by 行集膨胀。
                   (select count(*) from expense_items
                     join expenses on expenses.id = expense_items.expense_id
                    where expenses.service_order_id = service_orders.id) as expense_item_count,
                   count(distinct invoices.id) as invoice_count,
                   count(distinct case when invoices.paid_at is not null then invoices.id end) as paid_invoice_count,
                   -- 是否已有工单结算单：用相关子查询，不再 join 一张一对多的表，
                   -- 免得 group by 行集膨胀（列表页「工单结算」列只表达有/无）。
                   (select count(*) from customer_reimbursements
                     where customer_reimbursements.service_order_id = service_orders.id) as settlement_count
            from service_orders
            left join clients on clients.id = service_orders.client_id
            left join work_order_types on work_order_types.id = service_orders.work_order_type_id
            left join buyers on buyers.id = service_orders.buyer_id
            left join manufacturers as order_manufacturers on order_manufacturers.id = service_orders.manufacturer_id
            left join owners on owners.id = buyers.owner_id
            left join contracts on contracts.id = service_orders.contract_id
            left join quotations on quotations.id = service_orders.quotation_id
            left join service_reports on service_reports.service_order_id = service_orders.id
            left join invoices on invoices.service_order_id = service_orders.id and invoices.status != 'void'
            where {" and ".join(clauses)}
            group by service_orders.id, clients.id, work_order_types.id, owners.id, buyers.id, order_manufacturers.id, contracts.id, quotations.id
            order by service_orders.created_at desc, service_orders.id desc
            """,
            params,
        ).fetchall()
        filter_clients = db().execute("select id, name from clients order by name").fetchall()
        filter_buyers = db().execute(
            "select buyers.id, buyers.name, coalesce(owners.name, buyers.owner) as owner_name "
            "from buyers left join owners on owners.id = buyers.owner_id "
            "order by coalesce(owners.name, buyers.owner), buyers.name"
        ).fetchall()
        filter_manufacturers = db().execute("select id, name from manufacturers order by name").fetchall()
        filter_work_order_types = db().execute("select id, name from work_order_types order by name").fetchall()
        return render_template(
            "service_orders.html",
            orders=rows,
            q=q,
            status=status,
            client_id=client_id,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            **settlement_filters,
            manufacturer_id=manufacturer_id,
            work_order_type_id=work_order_type_id,
            settlement=settlement,
            invoice=invoice,
            payment=payment,
            filter_clients=filter_clients,
            filter_buyers=filter_buyers,
            filter_manufacturers=filter_manufacturers,
            filter_work_order_types=filter_work_order_types,
        )


    @login_required
    def new_service_order():
        if not can_create_service_order():
            abort(403)
        buyers_rows = db().execute(
            """
            select buyers.*, coalesce(owners.name, buyers.owner) as owner_name,
                   coalesce(country_local.name, country_zh.name, country_en.name, buyers.country, buyers.country_code) as country_name
            from buyers
            left join owners on owners.id = buyers.owner_id
            left join country_translations country_local
              on country_local.country_code = buyers.country_code and country_local.language_code = ?
            left join country_translations country_zh
              on country_zh.country_code = buyers.country_code and country_zh.language_code = 'zh-CN'
            left join country_translations country_en
              on country_en.country_code = buyers.country_code and country_en.language_code = 'en'
            order by owner_name, buyers.name, buyers.buyer_number
            """,
            (current_language(),),
        ).fetchall()
        clients_rows = db().execute("select * from clients order by client_number, name").fetchall()
        work_order_types_rows = db().execute(
            "select * from work_order_types where is_active = 1 order by code, name"
        ).fetchall()
        contracts_rows = db().execute(
            """
            select contracts.*, clients.name as client_name
            from contracts join clients on clients.id = contracts.client_id
            where contracts.status in ('signed', 'active')
            order by clients.name, contracts.contract_number
            """
        ).fetchall()
        quotations_rows = db().execute(
            """
            select quotations.*, clients.name as client_name
            from quotations
            left join clients on clients.id = quotations.client_id
            where quotations.status in ('draft', 'sent', 'accepted')
            order by quotations.quotation_date desc, quotations.id desc
            """
        ).fetchall()
        manufacturers_rows = manufacturer_options()
        if not buyers_rows:
            flash("请先由会计或经理维护站点资料。", "error")
            return redirect(url_for("buyers") if can_manage_buyers() else url_for("service_orders"))
        if not clients_rows:
            flash("请先由管理员或经理维护客户资料。", "error")
            return redirect(url_for("clients") if is_manager() else url_for("service_orders"))
        if not work_order_types_rows:
            flash("请先由管理员或经理维护工单类型。", "error")
            return redirect(url_for("work_order_types") if is_manager() else url_for("service_orders"))
        if request.method == "POST":
            country = country_from_form()
            buyer = db().execute(
                "select * from buyers where id = ?",
                (request.form.get("buyer_id"),),
            ).fetchone()
            manufacturer = manufacturer_from_form()
            work_order_type = db().execute(
                "select * from work_order_types where id = ? and is_active = 1",
                (request.form.get("work_order_type_id"),),
            ).fetchone()
            client = db().execute(
                "select * from clients where id = ?",
                (request.form.get("client_id"),),
            ).fetchone()
            contract_id = request.form.get("contract_id") or None
            contract = None
            if contract_id:
                contract = db().execute(
                    "select * from contracts where id = ?",
                    (contract_id,),
                ).fetchone()
            quotation_id = request.form.get("quotation_id") or None
            quotation = None
            if quotation_id:
                quotation = db().execute(
                    "select * from quotations where id = ?",
                    (quotation_id,),
                ).fetchone()
            site_address = request.form.get("site_address", "").strip()
            client_order_number = request.form.get("client_order_number", "").strip()
            if (
                not buyer
                or not client
                or not work_order_type
                or not site_address
                or not client_order_number
                or (request.form.get("manufacturer_id") and not manufacturer)
            ):
                flash("请选择客户、站点和工单类型，并填写服务现场地址、服务订单号码。", "error")
                return redirect(url_for("new_service_order"))
            if contract_id and (not contract or contract["client_id"] != client["id"]):
                flash("关联合同必须属于所选客户。", "error")
                return redirect(url_for("new_service_order"))
            if quotation_id and (
                not quotation
                or (quotation["client_id"] and quotation["client_id"] != client["id"])
            ):
                flash("关联报价单必须属于所选客户。", "error")
                return redirect(url_for("new_service_order"))
            order_number = next_service_order_number()
            cursor = db().execute(
                """
                insert into service_orders (
                    order_number, client_id, contract_id, quotation_id, settlement_basis, buyer_id, manufacturer_id, client_name, buyer_contact_name, buyer_contact_details,
                    site_address, client_order_number, start_date, expected_settlement_date, work_order_type_id,
                    region_code, country_code, created_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_number,
                    client["id"],
                    contract["id"] if contract else None,
                    quotation["id"] if quotation else None,
                    "quotation" if quotation else "contract_actual",
                    buyer["id"],
                    manufacturer["id"] if manufacturer else buyer["manufacturer_id"],
                    buyer["name"],
                    buyer["contact_name"],
                    buyer["contact_details"],
                    site_address,
                    client_order_number,
                    request.form.get("start_date") or None,
                    optional_settlement_date(request.form.get("expected_settlement_date")),
                    work_order_type["id"],
                    country["region_code"],
                    country["code"],
                    g.user["id"],
                    now(),
                ),
            )
            log_action("create", "service_order", cursor.lastrowid, order_number, f"站点：{buyer['name']}")
            # 工单统一绑定：同步到商务单据父表真实外键（合同或报价单二选一）
            sync_order_commercial_document(
                db(), cursor.lastrowid,
                contract["id"] if contract else None,
                quotation["id"] if quotation else None,
            )
            folder_created = True
            try:
                ensure_service_order_picture_folder(order_number)
            except OSError:
                folder_created = False
                app.logger.exception("Failed to create server pictures folder for service order %s", order_number)
                flash("工单已创建，但服务器照片文件夹创建失败，请检查共享照片目录或权限。", "error")
            db().commit()
            if folder_created:
                flash("工单已创建，服务器照片文件夹已自动创建。", "success")
            return redirect(url_for("service_orders"))
        return render_template(
            "service_order_form.html",
            order=None,
            clients=clients_rows,
            buyers=buyers_rows,
            work_order_types=work_order_types_rows,
            contracts=contracts_rows,
            quotations=quotations_rows,
            manufacturers=manufacturers_rows,
            form_title="新建工单",
            start_date_only=False,
            countries=country_rows(),
        )


    @login_required
    def edit_service_order(order_id):
        order = require_service_order(order_id)
        if normalized_role() in {"employee", "external_employee", "external_manager"} and order["status"] == "closed":
            flash("已关闭的工单不能编辑。", "error")
            return redirect(url_for("service_order_detail", order_id=order_id))
        if is_external_user():
            if request.method == "POST":
                db().execute(
                    "update service_orders set start_date = ? where id = ?",
                    (request.form.get("start_date") or None, order_id),
                )
                log_action(
                    "update",
                    "service_order",
                    order_id,
                    order["order_number"],
                    "修改工单开始日期",
                )
                db().commit()
                flash("工单开始日期已保存。", "success")
                return redirect(url_for("service_order_detail", order_id=order_id))
            return render_template(
                "service_order_form.html",
                order=order,
                clients=[],
                buyers=[],
                work_order_types=[],
                form_title="修改工单开始日期",
                start_date_only=True,
                countries=[],
            )
        buyers_rows = db().execute(
            """
            select buyers.*, coalesce(owners.name, buyers.owner) as owner_name,
                   coalesce(country_local.name, country_zh.name, country_en.name, buyers.country, buyers.country_code) as country_name
            from buyers
            left join owners on owners.id = buyers.owner_id
            left join country_translations country_local
              on country_local.country_code = buyers.country_code and country_local.language_code = ?
            left join country_translations country_zh
              on country_zh.country_code = buyers.country_code and country_zh.language_code = 'zh-CN'
            left join country_translations country_en
              on country_en.country_code = buyers.country_code and country_en.language_code = 'en'
            order by owner_name, buyers.name, buyers.buyer_number
            """,
            (current_language(),),
        ).fetchall()
        clients_rows = db().execute("select * from clients order by client_number, name").fetchall()
        work_order_types_rows = db().execute(
            """
            select * from work_order_types
            where is_active = 1 or id = ?
            order by is_active desc, code, name
            """,
            (order["work_order_type_id"],),
        ).fetchall()
        contracts_rows = db().execute(
            """
            select contracts.*, clients.name as client_name
            from contracts join clients on clients.id = contracts.client_id
            where contracts.status in ('signed', 'active') or contracts.id = ?
            order by clients.name, contracts.contract_number
            """,
            (order["contract_id"] or 0,),
        ).fetchall()
        quotations_rows = db().execute(
            """
            select quotations.*, clients.name as client_name
            from quotations
            left join clients on clients.id = quotations.client_id
            where quotations.status in ('draft', 'sent', 'accepted') or quotations.id = ?
            order by quotations.quotation_date desc, quotations.id desc
            """,
            (order["quotation_id"] or 0,),
        ).fetchall()
        manufacturers_rows = manufacturer_options()
        if request.method == "POST":
            country = country_from_form(order["country_code"])
            buyer = db().execute(
                "select * from buyers where id = ?",
                (request.form.get("buyer_id"),),
            ).fetchone()
            manufacturer = manufacturer_from_form()
            work_order_type = db().execute(
                "select * from work_order_types where id = ?",
                (request.form.get("work_order_type_id"),),
            ).fetchone()
            client = db().execute(
                "select * from clients where id = ?",
                (request.form.get("client_id"),),
            ).fetchone()
            contract_id = request.form.get("contract_id") or None
            contract = None
            if contract_id:
                contract = db().execute("select * from contracts where id = ?", (contract_id,)).fetchone()
            quotation_id = request.form.get("quotation_id") or None
            quotation = None
            if quotation_id:
                quotation = db().execute("select * from quotations where id = ?", (quotation_id,)).fetchone()
            site_address = request.form.get("site_address", "").strip()
            if (
                not buyer
                or not client
                or not work_order_type
                or not site_address
                or (request.form.get("manufacturer_id") and not manufacturer)
            ):
                flash("请选择有效的客户、站点和工单类型，并填写服务现场地址。", "error")
                return redirect(url_for("edit_service_order", order_id=order_id))
            if contract_id and (not contract or contract["client_id"] != client["id"]):
                flash("关联合同必须属于所选客户。", "error")
                return redirect(url_for("edit_service_order", order_id=order_id))
            if quotation_id and (
                not quotation
                or (quotation["client_id"] and quotation["client_id"] != client["id"])
            ):
                flash("关联报价单必须属于所选客户。", "error")
                return redirect(url_for("edit_service_order", order_id=order_id))
            if (
                request.form.get("status") == "closed"
                and order["status"] != "closed"
                and service_order_dependency_counts(order_id)["invoices"] == 0
            ):
                flash("该工单尚未开具发票，不能变更为“已完成”。请先为工单开具发票。", "error")
                return redirect(url_for("edit_service_order", order_id=order_id))
            db().execute(
                """
                update service_orders
                set client_id = ?, contract_id = ?, quotation_id = ?, buyer_id = ?, manufacturer_id = ?, client_name = ?, buyer_contact_name = ?, buyer_contact_details = ?,
                    site_address = ?, client_order_number = ?, start_date = ?, expected_settlement_date = ?,
                    work_order_type_id = ?, status = ?, region_code = ?, country_code = ?
                where id = ?
                """,
                (
                    client["id"],
                    contract["id"] if contract else None,
                    quotation["id"] if quotation else None,
                    buyer["id"],
                    manufacturer["id"] if manufacturer else buyer["manufacturer_id"],
                    buyer["name"],
                    buyer["contact_name"],
                    buyer["contact_details"],
                    site_address,
                    request.form.get("client_order_number", "").strip(),
                    request.form.get("start_date") or None,
                    optional_settlement_date(request.form.get("expected_settlement_date")),
                    work_order_type["id"],
                    request.form.get("status", "open"),
                    country["region_code"],
                    country["code"],
                    order_id,
                ),
            )
            log_action("update", "service_order", order_id, order["order_number"], "修改工单信息")
            sync_order_commercial_document(
                db(), order_id,
                contract["id"] if contract else None,
                quotation["id"] if quotation else None,
            )
            db().commit()
            flash("工单已保存。", "success")
            return redirect(url_for("service_order_detail", order_id=order_id))
        return render_template(
            "service_order_form.html",
            order=order,
            clients=clients_rows,
            buyers=buyers_rows,
            work_order_types=work_order_types_rows,
            contracts=contracts_rows,
            quotations=quotations_rows,
            manufacturers=manufacturers_rows,
            form_title="编辑工单",
            start_date_only=False,
            countries=country_rows(),
            invoice_count=service_order_dependency_counts(order_id)["invoices"],
        )


    @login_required
    def service_order_detail(order_id):
        order = require_service_order(order_id)
        reports_rows = db().execute(
            """
            select service_reports.*, group_concat(worker_users.name, ', ') as worker_names,
                   creator_users.name as creator_name
            from service_reports
            left join service_report_workers on service_report_workers.report_id = service_reports.id
            left join users as worker_users on worker_users.id = service_report_workers.user_id
            left join users as creator_users on creator_users.id = service_reports.created_by
            where service_reports.service_order_id = ?
            group by service_reports.id, creator_users.id
            order by service_reports.report_date desc, service_reports.id desc
            """,
            (order_id,),
        ).fetchall()
        invoices_rows = db().execute(
            """
            select invoices.*, clients.name as client_name
            from invoices left join clients on clients.id = invoices.client_id
            where invoices.service_order_id = ? and invoices.status != 'void'
            order by invoices.issue_date desc, invoices.id desc
            """,
            (order_id,),
        ).fetchall()
        if is_external_user():
            expenses_rows = []
        else:
            access_clause, access_params = expense_access_filter()
            expense_access = f"and {access_clause}"
            expense_params = [order_id, *access_params]
            expenses_rows = db().execute(
                f"""
                select expenses.*, users.name as creator_name, beneficiaries.name as beneficiary_name
                from expenses left join users on users.id = expenses.created_by
                left join users as beneficiaries on beneficiaries.id = coalesce(expenses.beneficiary_id, expenses.created_by)
                where expenses.service_order_id = ? {expense_access}
                order by expenses.created_at desc, expenses.id desc
                """,
                expense_params,
            ).fetchall()
        expense_total = sum((Decimal(str(row["amount"] or 0)) for row in expenses_rows), Decimal("0"))
        expense_currency = expenses_rows[0]["currency"] if expenses_rows else "USD"
        customer_reimbursement = latest_customer_reimbursement(order_id) if can_view_customer_reimbursement() else None
        employee_settlements = (
            order_employee_settlement_rows(db(), order_id, report_duration_hours)
            if normalized_role() in {"admin", "manager", "finance"}
            else []
        )
        # 结算明细：工单详情页的「工单结算」面板按只读表格展示（此前只有小计/合计，
        # 明细只在编辑页可见）。金额口径与编辑页一致——差旅各项走
        # customer_reimbursement_item_expense_amount()「手改优先、绝不与 auto 相加」。
        settlement_items = customer_reimbursement_items(customer_reimbursement["id"]) if customer_reimbursement else []
        settlement_items = [dict(item) for item in settlement_items]
        for item in settlement_items:
            item["expense_amounts"] = {
                key: customer_reimbursement_item_expense_amount(item, key)
                for key in ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")
            }
        delete_blockers = service_order_delete_blockers(order_id)
        photo_summary = (
            service_order_photo_summary(order["order_number"])
            if can_delete_service_order() and not delete_blockers
            else {"exists": False, "nonempty": False, "file_count": 0, "bytes": 0}
        )
        return render_template(
            "service_order_detail.html",
            order=order,
            reports=reports_rows,
            invoices=invoices_rows,
            expenses=expenses_rows,
            expense_total=expense_total,
            expense_currency=expense_currency,
            customer_reimbursement=customer_reimbursement,
            settlement_items=settlement_items,
            employee_settlements=employee_settlements,
            can_manage_employee_settlements=normalized_role() in {"admin", "manager", "finance"},
            can_delete_order=can_delete_service_order() and not delete_blockers,
            delete_blockers=delete_blockers,
            photo_summary=photo_summary,
            labels=STATUS_LABELS,
            expense_labels=EXPENSE_STATUS_LABELS,
        )


    @login_required
    def delete_service_order(order_id):
        order = require_service_order(order_id)
        if not can_delete_service_order():
            abort(403)
        blockers = service_order_delete_blockers(order_id)
        if blockers:
            flash(f"这个工单已有{', '.join(blockers)}，不能删除。", "error")
            return redirect(url_for("service_order_detail", order_id=order_id))
        photo_summary = service_order_photo_summary(order["order_number"])
        if photo_summary["nonempty"] and request.form.get("confirm_photo_cleanup") != "yes":
            flash("这个工单的照片目录不为空，请确认同时永久删除全部照片。", "error")
            return redirect(url_for("service_order_detail", order_id=order_id))
        photo_folder = service_order_photo_folder(order["order_number"])
        if photo_folder.exists():
            try:
                shutil.rmtree(photo_folder)
            except OSError:
                app.logger.exception("Unable to remove photo folder for service order %s", order["order_number"])
                flash("照片目录清理失败，工单没有删除。请检查服务器文件权限后重试。", "error")
                return redirect(url_for("service_order_detail", order_id=order_id))
        db().execute("delete from user_service_orders where service_order_id = ?", (order_id,))
        db().execute("delete from service_orders where id = ?", (order_id,))
        log_action("delete", "service_order", order_id, order["order_number"], f"站点：{order['client_name']}")
        db().commit()
        flash("工单及照片目录已删除；这个工单号可以重新使用。", "success")
        return redirect(url_for("service_orders"))


    app.add_url_rule('/service-orders', endpoint='service_orders', view_func=service_orders, methods=['GET'])
    app.add_url_rule('/service-orders/new', endpoint='new_service_order', view_func=new_service_order, methods=['GET', 'POST'])
    app.add_url_rule('/service-orders/<int:order_id>/edit', endpoint='edit_service_order', view_func=edit_service_order, methods=['GET', 'POST'])
    app.add_url_rule('/service-orders/<int:order_id>', endpoint='service_order_detail', view_func=service_order_detail, methods=['GET'])
    app.add_url_rule('/service-orders/<int:order_id>/delete', endpoint='delete_service_order', view_func=delete_service_order, methods=['POST'])
    return {'service_orders': service_orders, 'new_service_order': new_service_order, 'edit_service_order': edit_service_order, 'service_order_detail': service_order_detail, 'delete_service_order': delete_service_order}
