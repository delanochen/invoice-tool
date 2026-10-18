

## Codex remaining settlement phase 1: authorization

Functions: permission_tree_groups, can_approve_users, can_view_invoices, can_view_contracts, can_manage_contracts, can_create_invoice, can_create_service_order, can_delete_service_order, can_create_expense, can_approve_customer_reimbursement, can_reset_customer_reimbursement, can_delete_customer_reimbursement, can_transfer_expense_attachment, can_manage_employee_grades, can_view_labor_payroll_reports, can_view_customer_reimbursement, can_create_service_report, can_manage_users, can_manage_company_info, can_assign_external_employees, can_manage_user_record, can_manage_buyers, can_manage_owners, can_manage_manufacturers, can_access_buyer, can_view_audit_logs, can_use_database_console, menu_permission_overrides, has_menu_permission, action_permission_overrides, has_action_permission, required_action_for_request, can_access_client, require_contract_access, client_filter_clause, can_access_service_order, require_service_order, require_service_report.
Module: `invoice_tool/authorization/policy_helpers.py`. Exact AST/signatures preserved against 234203ed57d6a4be18f2709b9c1e17cffa761400.
Verification: 35 passed, 149 subtests passed in 12.05s. app.py: 7127 → 6696 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 2: ai_workflow

Functions: reset_ai_drafts_for_deleted_service_report, _auto_prepare_draft_media, _build_review_center_preview, _formal_save_preview_summary, _run_phase7_validation, _manifest_error_response, _fold_staff_query.
Module: `invoice_tool/ai_daily_report_web/workflow_helpers.py`. Exact AST/signatures preserved against 234203ed57d6a4be18f2709b9c1e17cffa761400.
Verification: 35 passed, 149 subtests passed in 12.32s. app.py: 6696 → 6423 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 3: identity

Functions: current_user, login_required, is_safe_redirect_target, admin_required, is_external_user, is_external_manager, is_external_employee, normalized_role, requires_user_address, is_internal_user, is_manager.
Module: `invoice_tool/authentication/identity_helpers.py`. Exact AST/signatures preserved against 234203ed57d6a4be18f2709b9c1e17cffa761400.
Verification: 35 passed, 149 subtests passed in 11.75s. app.py: 6423 → 6369 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 4: ai_access

Functions: ai_daily_report_csrf_token, require_ai_daily_report_csrf, can_view_ai_daily_report_draft, can_edit_ai_daily_report_draft, can_prepare_attachment_manifest, can_formal_save_draft, ai_daily_report_draft_list_filters.
Module: `invoice_tool/ai_daily_report_web/access_helpers.py`. Exact AST/signatures preserved against 234203ed57d6a4be18f2709b9c1e17cffa761400.
Verification: 35 passed, 149 subtests passed in 11.93s. app.py: 6369 → 6185 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 5: report_locks

Functions: order_has_approved_reimbursement, report_locked_by_approved_reimbursement.
Module: `invoice_tool/customer_reimbursements/report_locks.py`. Exact AST/signatures preserved against 234203ed57d6a4be18f2709b9c1e17cffa761400.
Verification: 35 passed, 149 subtests passed in 12.23s. app.py: 6185 → 6165 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.
