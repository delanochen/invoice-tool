

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
