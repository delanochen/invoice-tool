

## Codex remaining settlement phase 1: authentication

Functions: login, register.
Module: `invoice_tool/authentication/routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 12.03s. app.py: 9956 → 9886 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 2: system_settings

Functions: system_settings, menu_permissions, database_console.
Module: `invoice_tool/app_settings/admin_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 11.18s. app.py: 9886 → 9627 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 3: assistant

Functions: deepseek_connection_test, ai_assistant, ai_assistant_chat.
Module: `invoice_tool/ai_assistant/web_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 11.18s. app.py: 9627 → 9542 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 4: ai_confirmation

Functions: ai_daily_report_chat, ai_daily_report_confirm.
Module: `invoice_tool/ai_daily_report_web/confirmation_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.75s. app.py: 9542 → 9086 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 5: ai_validation

Functions: ai_daily_report_draft_preview, ai_daily_report_draft_validation, ai_daily_report_draft_acknowledge.
Module: `invoice_tool/ai_daily_report_web/validation_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 11.08s. app.py: 9086 → 8890 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 6: ai_evidence

Functions: ai_daily_report_draft_photo_preview, ai_daily_report_draft_evidence_preview, ai_daily_report_draft_prepare_attachments, ai_daily_report_draft_formal_save, ai_daily_report_manifest_compliance_review, ai_daily_report_draft_manifest, ai_daily_report_manifest_asset_preview.
Module: `invoice_tool/ai_daily_report_web/evidence_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.92s. app.py: 8890 → 8533 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 7: ai_workers

Functions: ai_daily_report_update_worker, ai_daily_report_staff_search, ai_daily_report_add_worker, ai_daily_report_remove_worker.
Module: `invoice_tool/ai_daily_report_web/worker_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.94s. app.py: 8533 → 8332 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 8: ai_work

Functions: ai_daily_report_recalculate_mileage, ai_daily_report_update_arrival_departure, ai_daily_report_update_work_item.
Module: `invoice_tool/ai_daily_report_web/work_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 11.11s. app.py: 8332 → 8120 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 9: report_queries

Functions: export_visible_report, customer_reimbursement_query, audit_log_report.
Module: `invoice_tool/query_pages/settlement_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.72s. app.py: 8120 → 7932 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 10: order_attachments

Functions: import_google_photos_for_service_order, download_service_order_attachments.
Module: `invoice_tool/service_orders/attachment_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.61s. app.py: 7932 → 7866 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 11: settlement_form

Functions: customer_reimbursement_form, download_customer_reimbursement, download_customer_reimbursement_excel, preview_customer_reimbursement.
Module: `invoice_tool/customer_reimbursements/form_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.77s. app.py: 7866 → 7599 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 12: settlement_actions

Functions: approve_customer_reimbursement, sync_customer_reimbursement_sources, ignore_customer_reimbursement_source, unignore_customer_reimbursement_source, return_customer_reimbursement, reset_customer_reimbursement, delete_customer_reimbursement.
Module: `invoice_tool/customer_reimbursements/action_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.51s. app.py: 7599 → 7300 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.


## Codex remaining settlement phase 13: settlement_attachments

Functions: download_customer_reimbursement_attachment, preview_customer_reimbursement_attachment, delete_customer_reimbursement_attachment.
Module: `invoice_tool/customer_reimbursements/attachment_routes.py`. Exact AST/signatures preserved against 17c6a25561a8f32f2573eb2ac91f61fffa9e0cb1.
Verification: 30 passed, 57 subtests passed in 10.56s. app.py: 7300 → 7280 lines.
Executor: Codex, directly authorized by user. invoice_test only; no merge/deploy.
