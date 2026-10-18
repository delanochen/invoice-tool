"""Legacy-compatible authorization helpers; explicit runtime dependencies."""

def permission_tree_groups(*, DEFAULT_MENU_ROLES, MENU_PERMISSION_GROUPS, ROLE_ACTION_PERMISSION_GROUPS):
    menu_labels = {
        item["key"]: item["label"]
        for group in MENU_PERMISSION_GROUPS
        for item in group["items"]
    }
    seen_keys = set()
    groups = []
    for group in ROLE_ACTION_PERMISSION_GROUPS:
        items = []
        for item in group["items"]:
            seen_keys.add(item["key"])
            items.append(
                {
                    "key": item["key"],
                    "label": item["label"],
                    "menu_key": item["key"] if item["key"] in DEFAULT_MENU_ROLES else "",
                    "actions": list(item["actions"].keys()),
                }
            )
        groups.append({"label": group["label"], "items": items})
    menu_only_items = [
        {"key": key, "label": label, "menu_key": key, "actions": []}
        for key, label in menu_labels.items()
        if key not in seen_keys
    ]
    if menu_only_items:
        groups.insert(0, {"label": "菜单入口", "items": menu_only_items})
    return groups


def can_approve_users(*, g, has_action_permission):
    return g.user and has_action_permission("users", "approve")


def can_view_invoices(*, g, has_action_permission):
    return g.user and has_action_permission("invoices", "view")


def can_view_contracts(*, g, has_action_permission):
    return g.user and has_action_permission("contracts", "view")


def can_manage_contracts(*, g, has_action_permission):
    return g.user and (
        has_action_permission("contracts", "create")
        or has_action_permission("contracts", "edit")
        or has_action_permission("contracts", "delete")
    )


def can_create_invoice(*, g, has_action_permission):
    return g.user and has_action_permission("invoices", "create")


def can_create_service_order(*, g, has_action_permission):
    return g.user and has_action_permission("service_orders", "create")


def can_delete_service_order(*, g, has_action_permission):
    return g.user and has_action_permission("service_orders", "delete")


def can_create_expense(*, g, has_action_permission):
    return g.user and has_action_permission("expenses", "create")


def can_approve_customer_reimbursement(*, g, has_action_permission):
    return g.user and has_action_permission("customer_reimbursements", "approve")


def can_reset_customer_reimbursement(*, g, has_action_permission):
    return g.user and has_action_permission("customer_reimbursements", "reset")


def can_delete_customer_reimbursement(*, g, has_action_permission):
    return g.user and has_action_permission("customer_reimbursements", "delete")


def can_transfer_expense_attachment(expense, *, g, has_action_permission):
    if not g.user:
        return False
    if has_action_permission("customer_reimbursements", "edit"):
        return True
    return (
        expense["status"] in {"draft", "returned"}
        and expense["created_by"] == g.user["id"]
        and has_action_permission("expenses", "edit")
    )


def can_manage_employee_grades(*, g, has_action_permission):
    return g.user and (
        has_action_permission("employee_grades", "create")
        or has_action_permission("employee_grades", "edit")
        or has_action_permission("payroll_subsidies", "edit")
    )


def can_view_labor_payroll_reports(*, g, has_action_permission):
    return g.user and (
        has_action_permission("labor_hours_report", "view")
        or has_action_permission("payroll_report", "view")
        or has_action_permission("payroll_calendar", "view")
    )


def can_view_customer_reimbursement(*, g, has_action_permission):
    return g.user and has_action_permission("customer_reimbursements", "view")


def can_create_service_report(order=None, *, can_access_service_order, g, has_action_permission, is_external_employee, is_external_user):
    if not g.user:
        return False
    if not has_action_permission("service_reports", "create"):
        return False
    if not is_external_user():
        return True
    if is_external_employee() and order is not None:
        return can_access_service_order(order)
    return False


def can_manage_users(*, g, has_action_permission, is_external_user):
    return g.user and not is_external_user() and (
        has_action_permission("users", "create")
        or has_action_permission("users", "edit")
        or has_action_permission("users", "delete")
    )


def can_manage_company_info(*, g, has_action_permission):
    return g.user and has_action_permission("company_info", "edit")


def can_assign_external_employees(*, can_manage_users, is_external_manager):
    return can_manage_users() or is_external_manager()


def can_manage_user_record(user, *, can_manage_users, g, is_external_manager, normalized_role):
    if can_manage_users() or user["id"] == g.user["id"]:
        return True
    return (
        is_external_manager()
        and normalized_role(user["role"]) == "external_employee"
        and user["client_id"] == g.user["client_id"]
    )


def can_manage_buyers(*, g, has_action_permission):
    return g.user and (
        has_action_permission("buyers", "view")
        or has_action_permission("buyers", "create")
        or has_action_permission("buyers", "edit")
        or has_action_permission("buyers", "delete")
    )


def can_manage_owners(*, g, has_action_permission):
    return g.user and (
        has_action_permission("owners", "view")
        or has_action_permission("owners", "create")
        or has_action_permission("owners", "edit")
        or has_action_permission("owners", "delete")
    )


def can_manage_manufacturers(*, g, has_action_permission):
    return g.user and (
        has_action_permission("manufacturers", "view")
        or has_action_permission("manufacturers", "create")
        or has_action_permission("manufacturers", "edit")
        or has_action_permission("manufacturers", "delete")
    )


def can_access_buyer(buyer, *, can_manage_buyers, g, is_external_manager, is_external_user):
    if g.user and can_manage_buyers() and not is_external_user():
        return True
    return is_external_manager() and bool(g.user["client_id"]) and buyer["client_id"] == g.user["client_id"]


def can_view_audit_logs(*, g, has_action_permission):
    return g.user and has_action_permission("audit_logs", "view")


def can_use_database_console(*, g, has_action_permission):
    return g.user and has_action_permission("database_console", "view")


def menu_permission_overrides(*, db, g):
    if not hasattr(g, "_menu_permission_overrides"):
        rows = db().execute("select role, menu_key, is_enabled from role_menu_permissions").fetchall()
        g._menu_permission_overrides = {
            (row["role"], row["menu_key"]): bool(row["is_enabled"])
            for row in rows
        }
    return g._menu_permission_overrides


def has_menu_permission(menu_key, role=None, *, DEFAULT_ACTION_ROLES, DEFAULT_MENU_ROLES, ROLE_OPTIONS, action_permission_overrides, g, menu_permission_overrides, normalized_role):
    if not g.user:
        return False
    role = normalized_role(role)
    if role not in ROLE_OPTIONS:
        return False
    overrides = menu_permission_overrides()
    override_key = (role, menu_key)
    action_overrides = action_permission_overrides()
    if role == "manager" and (
        overrides.get(("finance", menu_key), False)
        or any(
            enabled
            for (permission_role, resource_key, _action_key), enabled
            in action_overrides.items()
            if permission_role == "finance" and resource_key == menu_key
        )
    ):
        return True
    if any(
        enabled
        for (permission_role, resource_key, _action_key), enabled in action_overrides.items()
        if permission_role == role and resource_key == menu_key
    ):
        return True
    if override_key in overrides:
        return overrides[override_key]
    if any(
        role in roles
        for (resource_key, _action_key), roles in DEFAULT_ACTION_ROLES.items()
        if resource_key == menu_key
    ):
        return True
    return role in DEFAULT_MENU_ROLES.get(menu_key, set())


def action_permission_overrides(*, db, g):
    if not hasattr(g, "_action_permission_overrides"):
        rows = db().execute("select role, resource_key, action_key, is_enabled from role_action_permissions").fetchall()
        g._action_permission_overrides = {
            (row["role"], row["resource_key"], row["action_key"]): bool(row["is_enabled"])
            for row in rows
        }
    return g._action_permission_overrides


def has_action_permission(resource_key, action_key, role=None, *, DEFAULT_ACTION_ROLES, DEFAULT_MENU_ROLES, MANAGER_FINANCE_INHERITANCE_EXCEPTIONS, ROLE_OPTIONS, action_permission_overrides, g, menu_permission_overrides, normalized_role):
    if not g.user:
        return False
    role = normalized_role(role)
    if role not in ROLE_OPTIONS:
        return False
    overrides = action_permission_overrides()
    override_key = (role, resource_key, action_key)
    menu_overrides = menu_permission_overrides()
    if action_key != "view" and override_key in overrides:
        return overrides[override_key]
    if role == "manager" and (resource_key, action_key) not in MANAGER_FINANCE_INHERITANCE_EXCEPTIONS and (
        overrides.get(("finance", resource_key, action_key), False)
        or (
            action_key == "view"
            and menu_overrides.get(("finance", resource_key), False)
        )
    ):
        return True
    if action_key == "view" and menu_overrides.get((role, resource_key), False):
        return True
    if action_key == "view" and any(
        enabled
        for (permission_role, permission_resource, permission_action), enabled in overrides.items()
        if permission_role == role
        and permission_resource == resource_key
        and permission_action != "view"
    ):
        return True
    if override_key in overrides:
        return overrides[override_key]
    if action_key == "view" and role in DEFAULT_MENU_ROLES.get(resource_key, set()):
        return True
    if action_key == "view" and any(
        role in roles
        for (permission_resource, permission_action), roles in DEFAULT_ACTION_ROLES.items()
        if permission_resource == resource_key and permission_action != "view"
    ):
        return True
    return role in DEFAULT_ACTION_ROLES.get((resource_key, action_key), set())


def required_action_for_request(*, g, request):
    endpoint = request.endpoint or ""
    method = request.method
    if endpoint == "edit_user" and request.view_args and request.view_args.get("user_id") == g.user["id"]:
        return None
    method_rules = {
        ("accounting_receipts", "GET"): ("accounting_receipts", "view"),
        ("accounting_receipts", "POST"): ("accounting_receipts", "create"),
        ("accounting_prepayment_apply", "GET"): ("accounting_receipts", "edit"),
        ("accounting_prepayment_apply", "POST"): ("accounting_receipts", "edit"),
        ("accounting_invoice_correct", "GET"): ("accounting_corrections", "create"),
        ("accounting_invoice_correct", "POST"): ("accounting_corrections", "create"),
        ("accounting_invoice_correction_reverse", "POST"): ("accounting_corrections", "create"),
        ("accounting_periods", "GET"): ("accounting_periods", "view"),
        ("accounting_periods", "POST"): ("accounting_periods", "view"),
        ("accounting_opening_balances", "GET"): ("accounting_opening", "view"),
        ("accounting_opening_balances", "POST"): ("accounting_opening", "view"),
        ("clients", "GET"): ("clients", "view"),
        ("clients", "POST"): ("clients", "create"),
        ("owners", "GET"): ("owners", "view"),
        ("owners", "POST"): ("owners", "create"),
        ("manufacturers", "GET"): ("manufacturers", "view"),
        ("manufacturers", "POST"): ("manufacturers", "create"),
        ("buyers", "GET"): ("buyers", "view"),
        ("buyers", "POST"): ("buyers", "create"),
        ("work_order_types", "GET"): ("work_order_types", "view"),
        ("work_order_types", "POST"): ("work_order_types", "create"),
        ("projects", "GET"): ("projects", "view"),
        ("projects", "POST"): ("projects", "create"),
        ("countries", "GET"): ("countries", "view"),
        ("countries", "POST"): ("countries", "edit"),
        ("employee_grades", "GET"): ("employee_grades", "view"),
        ("employee_grades", "POST"): ("employee_grades", "edit"),
        ("payroll_subsidies", "GET"): ("payroll_subsidies", "view"),
        ("payroll_subsidies", "POST"): ("payroll_subsidies", "edit"),
        ("payment_terms", "GET"): ("payment_terms", "view"),
        ("payment_terms", "POST"): ("payment_terms", "create"),
        ("users", "GET"): ("users", "view"),
        ("users", "POST"): ("users", "create"),
        ("system_settings", "GET"): ("system_settings", "view"),
        ("system_settings", "POST"): ("system_settings", "edit"),
        ("deepseek_connection_test", "POST"): ("system_settings", "edit"),
        ("database_console", "GET"): ("database_console", "view"),
        ("database_console", "POST"): ("database_console", "execute"),
        ("order_data_transfer", "GET"): ("order_data_transfer", "view"),
        ("order_data_transfer", "POST"): ("order_data_transfer", "execute"),
        ("company_info", "GET"): ("company_info", "view"),
        ("company_info", "POST"): ("company_info", "edit"),
        ("travel_tools_page", "GET"): ("travel_tools", "view"),
        ("travel_tools_route_map_api", "POST"): ("travel_tools", "view"),
        ("travel_tools_hotel_api", "POST"): ("travel_tools", "view"),
        ("knowledge_base", "GET"): ("knowledge_base", "view"),
        ("knowledge_base", "POST"): ("knowledge_base", "create"),
        ("ai_assistant", "GET"): ("ai_assistant", "view"),
        ("ai_assistant_chat", "POST"): ("ai_assistant", "view"),
        ("customer_reimbursement_form", "GET"): ("customer_reimbursements", "view"),
        ("customer_reimbursement_form", "POST"): ("customer_reimbursements", "edit"),
    }
    endpoint_rules = {
        "contracts": ("contracts", "view"),
        "new_contract": ("contracts", "create"),
        "contract_detail": ("contracts", "view"),
        "edit_contract": ("contracts", "edit"),
        "delete_contract": ("contracts", "delete"),
        "delete_contract_attachment": ("contracts", "delete"),
        "quotations": ("quotations", "view"),
        "new_quotation": ("quotations", "create"),
        "quotation_detail": ("quotations", "view"),
        "edit_quotation": ("quotations", "edit"),
        "delete_quotation": ("quotations", "delete"),
        "quotation_pdf": ("quotations", "export"),
        "edit_client": ("clients", "edit"),
        "delete_client": ("clients", "delete"),
        "edit_owner": ("owners", "edit"),
        "delete_owner": ("owners", "delete"),
        "edit_manufacturer": ("manufacturers", "edit"),
        "delete_manufacturer": ("manufacturers", "delete"),
        "edit_buyer": ("buyers", "edit"),
        "delete_buyer": ("buyers", "delete"),
        "import_buyers": ("buyers", "create"),
        "edit_work_order_type": ("work_order_types", "edit"),
        "delete_work_order_type": ("work_order_types", "delete"),
        "edit_project": ("projects", "edit"),
        "delete_project": ("projects", "delete"),
        "edit_payment_term": ("payment_terms", "edit"),
        "toggle_payment_term": ("payment_terms", "edit"),
        "recalculate_payment_term_invoices": ("payment_terms", "edit"),
        "edit_user": ("users", "edit"),
        "update_user_status": ("users", "approve"),
        "delete_user": ("users", "delete"),
        "update_employee_grade_members": ("employee_grades", "edit"),
        "delete_user_attachment": ("users", "edit"),
        "delete_company_attachment": ("company_info", "edit"),
        "preview_knowledge_document": ("knowledge_base", "view"),
        "download_knowledge_document": ("knowledge_base", "view"),
        "edit_knowledge_document": ("knowledge_base", "edit"),
        "upload_knowledge_document_version": ("knowledge_base", "edit"),
        "preview_knowledge_document_version": ("knowledge_base", "view"),
        "download_knowledge_document_version": ("knowledge_base", "view"),
        "delete_knowledge_document": ("knowledge_base", "delete"),
        "service_orders": ("service_orders", "view"),
        "new_service_order": ("service_orders", "create"),
        "edit_service_order": ("service_orders", "edit"),
        "service_order_detail": ("service_orders", "view"),
        "delete_service_order": ("service_orders", "delete"),
        "service_order_map": ("service_orders", "view"),
        "service_order_map_static_image": ("service_orders", "view"),
        "service_order_calendar": ("service_order_calendar", "view"),
        "service_report_query": ("service_reports", "view"),
        "view_service_report": ("service_reports", "view"),
        "new_service_report": ("service_reports", "create"),
        "edit_service_report": ("service_reports", "edit"),
        "delete_service_report": ("service_reports", "delete"),
        "export_service_report": ("service_reports", "export"),
        "delete_report_attachment": ("service_reports", "edit"),
        "expense_processing": ("expenses", "view"),
        "expense_query": ("expenses", "view"),
        "new_expense": ("expenses", "create"),
        "edit_expense": ("expenses", "edit"),
        "expense_detail": ("expenses", "view"),
        "approve_expense": ("expenses", "approve"),
        "return_expense": ("expenses", "approve"),
        "delete_expense": ("expenses", "delete"),
        "delete_expense_attachment": ("expenses", "edit"),
        "invoices": ("invoices", "view"),
        "invoice_query": ("invoices", "view"),
        "new_invoice": ("invoices", "create"),
        "edit_invoice": ("invoices", "edit"),
        "invoice_detail": ("invoices", "view"),
        "delete_invoice": ("invoices", "delete"),
        "send_invoice": ("invoices", "send"),
        "mark_invoice_paid": ("invoices", "pay"),
        "unmark_invoice_paid": ("invoices", "pay"),
        "admin_update_invoice_status": ("invoices", "edit"),
        "void_invoice": ("invoices", "edit"),
        "export_invoice": ("invoices", "export"),
        "export_invoice_pdf": ("invoices", "export"),
        "delete_attachment": ("invoices", "edit"),
        "accounting_vouchers": ("accounting_vouchers", "view"),
        "accounting_voucher_detail": ("accounting_vouchers", "view"),
        "accounting_receipts": ("accounting_receipts", "view"),
        "accounting_prepayment_apply": ("accounting_receipts", "edit"),
        "accounting_invoice_correct": ("accounting_corrections", "create"),
        "accounting_invoice_correction_reverse": ("accounting_corrections", "create"),
        "accounting_periods": ("accounting_periods", "view"),
        "accounting_opening_balances": ("accounting_opening", "view"),
        "accounting_trial_balance": ("accounting_reports", "view"),
        "accounting_income_statement": ("accounting_reports", "view"),
        "accounting_balance_sheet": ("accounting_reports", "view"),
        "accounting_receivable_aging": ("accounting_reports", "view"),
        "accounting_customer_statement": ("accounting_reports", "view"),
        "accounting_sales_tax_liability": ("accounting_reports", "view"),
        "accounting_account_ledger": ("accounting_reports", "view"),
        "customer_reimbursement_query": ("customer_reimbursements", "view"),
        "download_customer_reimbursement": ("customer_reimbursements", "export"),
        "download_customer_reimbursement_excel": ("customer_reimbursements", "export"),
        "preview_customer_reimbursement": ("customer_reimbursements", "export"),
        "approve_customer_reimbursement": ("customer_reimbursements", "approve"),
        "return_customer_reimbursement": ("customer_reimbursements", "approve"),
        "reset_customer_reimbursement": ("customer_reimbursements", "reset"),
        "delete_customer_reimbursement": ("customer_reimbursements", "delete"),
        "delete_customer_reimbursement_attachment": ("customer_reimbursements", "edit"),
        "labor_hours_report": ("labor_hours_report", "view"),
        "payroll_report": ("payroll_report", "view"),
        "payroll_detail_report": ("payroll_report", "view"),
        "payroll_calendar": ("payroll_calendar", "view"),
        "payroll_calendar_batch": ("payroll_calendar", "view"),
        "payroll_calendar_export": ("payroll_calendar", "export"),
        # Phase 4A：Tax Review 工作台（写动作额外要求 tax_review.review）
        "tax_review": ("tax_review", "view"),
        "tax_review_component": ("tax_review", "view"),
        "save_tax_review": ("tax_review", "review"),
        # Phase 4B：Annual Tax Summary（导出额外要求 annual_tax_summary.export）
        "annual_tax_summary": ("annual_tax_summary", "view"),
        "annual_tax_summary_components": ("annual_tax_summary", "view"),
        "annual_tax_summary_export": ("annual_tax_summary", "export"),
        # Phase 6C：CPA Policy Decision Package（权限复用 annual_tax_summary）
        "policy_decisions": ("annual_tax_summary", "view"),
        "policy_decisions_export": ("annual_tax_summary", "export"),
        # Phase 6B：CPA Workpaper（只读 reporting，权限同 annual_tax_summary；
        # 导出额外要求 export。复核动作仍走 4A 的 tax_review.review。）
        "cpa_workpaper": ("annual_tax_summary", "view"),
        "cpa_workpaper_export": ("annual_tax_summary", "export"),
    }
    return method_rules.get((endpoint, method)) or endpoint_rules.get(endpoint)


def can_access_client(client_id, *, g, is_external_manager, is_internal_user):
    if not g.user:
        return False
    if is_internal_user():
        return True
    return is_external_manager() and g.user["client_id"] == client_id


def require_contract_access(contract_id, *, abort, can_access_client, can_view_contracts, db):
    if not can_view_contracts():
        abort(403)
    contract = db().execute("select * from contracts where id = ?", (contract_id,)).fetchone()
    if not contract:
        abort(404)
    if not can_access_client(contract["client_id"]):
        abort(403)
    return contract


def client_filter_clause(alias="invoices", *, g, is_external_employee, is_external_manager):
    if is_external_manager():
        if not g.user["client_id"]:
            return "1 = 0", []
        return f"{alias}.client_id = ?", [g.user["client_id"]]
    if is_external_employee():
        return (
            f"{alias}.service_order_id in (select service_order_id from user_service_orders where user_id = ?)",
            [g.user["id"]],
        )
    return "1 = 1", []


def can_access_service_order(order, *, db, g, is_external_employee, is_external_manager, is_internal_user):
    if not g.user:
        return False
    if is_internal_user():
        return True
    if is_external_manager():
        return bool(g.user["client_id"]) and order["client_id"] == g.user["client_id"]
    if is_external_employee():
        return db().execute(
            "select 1 from user_service_orders where user_id = ? and service_order_id = ?",
            (g.user["id"], order["id"]),
        ).fetchone() is not None
    return False


def require_service_order(order_id, *, abort, can_access_service_order, current_language, db):
    order = db().execute(
        """
        select service_orders.*, work_order_types.name as work_order_type_name,
               coalesce(buyer_country_local.name, buyer_country_zh.name, buyer_country_en.name, buyers.country, buyers.country_code) as buyer_country,
               coalesce(owners.name, buyers.owner) as buyer_owner,
               buyers.email as buyer_email,
               buyers.site_size as buyer_site_size,
               coalesce(order_manufacturers.name, buyers.equipment_manufacturer) as buyer_equipment_manufacturer,
               clients.name as billing_client_name,
               clients.email as billing_client_email,
               contracts.contract_number,
               contracts.title as contract_title,
               contracts.contract_type,
               quotations.quotation_number,
               quotations.customer as quotation_customer,
               quotations.project_name as quotation_project_name,
               quotations.status as quotation_status,
               quotations.current_revision_no as quotation_revision_no,
               quotations.total as quotation_current_total,
               quotations.currency as quotation_currency,
               coalesce(country_local.name, country_zh.name, country_en.name, service_orders.country_code) as country_name,
               coalesce(country_local.region_name, country_zh.region_name, country_en.region_name, service_orders.region_code) as region_name
        from service_orders
        left join work_order_types on work_order_types.id = service_orders.work_order_type_id
        left join buyers on buyers.id = service_orders.buyer_id
        left join manufacturers as order_manufacturers on order_manufacturers.id = service_orders.manufacturer_id
        left join owners on owners.id = buyers.owner_id
        left join clients on clients.id = service_orders.client_id
        left join contracts on contracts.id = service_orders.contract_id
        left join quotations on quotations.id = service_orders.quotation_id
        left join country_translations buyer_country_local
          on buyer_country_local.country_code = buyers.country_code and buyer_country_local.language_code = ?
        left join country_translations buyer_country_zh
          on buyer_country_zh.country_code = buyers.country_code and buyer_country_zh.language_code = 'zh-CN'
        left join country_translations buyer_country_en
          on buyer_country_en.country_code = buyers.country_code and buyer_country_en.language_code = 'en'
        left join country_translations country_local
          on country_local.country_code = service_orders.country_code and country_local.language_code = ?
        left join country_translations country_zh
          on country_zh.country_code = service_orders.country_code and country_zh.language_code = 'zh-CN'
        left join country_translations country_en
          on country_en.country_code = service_orders.country_code and country_en.language_code = 'en'
        where service_orders.id = ?
        """,
        (current_language(), current_language(), order_id),
    ).fetchone()
    if not order:
        abort(404)
    if not can_access_service_order(order):
        abort(403)
    return order


def require_service_report(report_id, *, abort, db, require_service_order):
    report = db().execute("select * from service_reports where id = ?", (report_id,)).fetchone()
    if not report:
        abort(404)
    order = require_service_order(report["service_order_id"])
    return report, order
