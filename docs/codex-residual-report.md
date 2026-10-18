

## Codex remaining settlement phase 1: residual_bootstrap

Functions: verify_data_directory_identity, ensure_postgres_admin, init_db.
Module: `invoice_tool/app_bootstrap/startup_checks.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 11.35s. app.py: 5035 → 4962 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 2: residual_language

Functions: current_language, language_from_form, clear_session_preserving_language.
Module: `invoice_tool/user_preferences/language_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 13.77s. app.py: 4962 → 4959 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 3: residual_settings

Functions: inspection_cycle_days, inspection_warning_days, lodging_reimbursement_limit, get_company_profile, get_payment_instructions, get_invoice_terms, get_smtp_settings, get_google_maps_browser_api_key, get_google_geocoding_api_key, get_google_routes_api_key, get_google_static_maps_api_key.
Module: `invoice_tool/app_settings/profile_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 13.85s. app.py: 4959 → 4958 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 4: residual_expense_policy

Functions: is_lodging_project_name, is_fuel_project_name, validate_fuel_reimbursement_allowed.
Module: `invoice_tool/employee_expenses/project_policy.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 11.02s. app.py: 4958 → 4956 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 5: residual_formatting

Functions: project_color, pdf_text, to_float, decimal_value, money_decimal, money_float, nl2br_filter.
Module: `invoice_tool/presentation/value_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 12.47s. app.py: 4956 → 4939 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 6: residual_sql

Functions: sql_statement_kind, is_read_sql.
Module: `invoice_tool/database_console/statement_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 14.20s. app.py: 4939 → 4937 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 7: residual_order_guard

Functions: require_service_order_start_date.
Module: `invoice_tool/service_orders/start_date_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 11.17s. app.py: 4937 → 4935 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 8: residual_photo_paths

Functions: allowed_attachment, allowed_image, shared_photos_root, ensure_service_order_picture_folder, service_order_incoming_folder, is_google_photos_share_url, google_photo_url_key.
Module: `invoice_tool/photo_imports/path_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 11.33s. app.py: 4935 → 4930 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 9: residual_settlement_paths

Functions: customer_reimbursement_dir, customer_reimbursement_attachment_dir, customer_reimbursement_file_path, customer_reimbursement_attachment_path, save_customer_reimbursement_uploads.
Module: `invoice_tool/customer_reimbursements/path_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 11.34s. app.py: 4930 → 4926 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 10: residual_settlement_values

Functions: is_us_weekend_or_holiday, reimbursement_number, reimbursement_paragraph.
Module: `invoice_tool/customer_reimbursements/value_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 10.94s. app.py: 4926 → 4925 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 11: residual_messages

Functions: create_message, review_message_body, return_message_body.
Module: `invoice_tool/messages/content_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 10.97s. app.py: 4925 → 4920 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 12: residual_audit

Functions: request_ip_address.
Module: `invoice_tool/audit_log/request_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 13.93s. app.py: 4920 → 4920 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 13: residual_geo_values

Functions: normalized_address, configured_country_codes, address_expectations.
Module: `invoice_tool/geocoding/address_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 12.07s. app.py: 4920 → 4918 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 14: residual_ai_values

Functions: ai_search_like, _ai_daily_report_business_date.
Module: `invoice_tool/ai_daily_report_web/value_helpers.py`. Exact AST/signatures preserved against 5f8f225eb980ea3d4377fb3642785d575559f47d.
Verification: 35 passed, 106 subtests passed in 11.00s. app.py: 4918 → 4918 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.
