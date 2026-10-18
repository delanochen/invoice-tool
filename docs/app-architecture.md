# `app.py` architecture map and modularization plan

## Scope and baseline

This document describes the repository at commit
`5cdc400fbaf2267ea46f174442a9d07b6b976258`. At that baseline `app.py` has
19,728 lines and is 847,515 bytes. The analysis is static: no route, endpoint,
database behavior, permission rule, template, or business function was moved or
changed while preparing this map.

Line counts below are approximate. Several blocks contain helpers used by more
than one domain, so the domain estimates should be used for sequencing rather
than as an exact partition of the file.

## Current execution model

`app.py` currently performs all of these jobs:

1. imports third-party libraries and domain modules;
2. reads environment configuration and defines storage paths and constants;
3. creates the global `Flask` instance;
4. owns the request-scoped PostgreSQL connection and startup checks;
5. defines authentication, permission, template, audit, notification, file, and
   formatting helpers;
6. implements most business queries and services directly;
7. declares 202 route decorators directly on the global app;
8. registers seven already-extracted route modules with
   `register_*(app, globals())`;
9. validates PostgreSQL and bootstraps the first admin at import time.

The dominant call shape is currently:

```text
Flask route in app.py
  -> permission helper in app.py
  -> business/helper functions in app.py or an imported service
  -> db().execute(...) directly
  -> render_template / send_file / redirect
```

There is no repository layer. Routes frequently contain validation, state
transitions, SQL, audit logging, notification, filesystem changes, and response
construction in the same function. This is the main reason moving a route by
itself is unsafe.

## Major domains

The file contains 20 major business/infrastructure domains.

| # | Domain | Principal locations in `app.py` | Approx. lines owned | Important entry points and helpers |
|---|---|---:|---:|---|
| 1 | Application/bootstrap/database | 140-249, 597-838, 19713-19728 | 500 | global paths/config, `app`, `db`, `close_db`, `verify_data_directory_identity`, `ensure_postgres_admin`, `init_db`, final module registration/startup |
| 2 | Authentication/session/language/PWA | 839-887, 1221-1337, 6616-6858 | 430 | `current_user`, `load_user`, `login_required`, `admin_required`, `login`, `register`, `logout`, language switch, mobile app manifest/IPA, web manifest/service worker |
| 3 | Permissions/navigation/access scope | 250-596, 1305-1985, 12706-12779 | 1,050 | menu/action matrices, `has_menu_permission`, `has_action_permission`, `required_action_for_request`, client/order/invoice/contract access guards, menu-permission administration |
| 4 | Shared settings, formatting and application services | 970-1219, 1986-2168, 6090-6196, 6574-6615, 19474-19528 | 720 | settings getters, timezone/company/SMTP/API-key access, money/date formatting, generic named attachments, messages, audit logs, email-delivery history, error handlers |
| 5 | Customers, sites and reference/catalog data | 2169-2445, 7448-8455 | 1,380 | clients, buyers, owners, manufacturers, countries/translations, work-order types, projects, buyer CSV import and numbering |
| 6 | Contracts, contract rates and payment terms | 2446-2584, 6896-7447 | 700 | contract CRUD/attachments, `contract_form_values`, due-date/payment-term functions; rate routes and rate calculation are already in `rate_engine.py` |
| 7 | Employees/users/grades | 1156-1206, 8397-9301 | 950 | users, registration/profile, user attachments and order assignments, employee grades/membership, employee-rate integration, payroll subsidy settings |
| 8 | Work orders, calendar, map and geocoding | 2802-2913, 6197-6573, 14312-14507, 15149-15995 | 1,420 | order access/dependency checks, CRUD/detail, calendar lanes, buyer/order map, geocoder chain, Google static map, Google Photos import trigger |
| 9 | Daily/service reports | 2914-3109, 3703-3800, 5873-6090, 16556-16999, 17332-17518, 19164-19401 | 1,650 | report CRUD, workers/parts, mileage evidence, assist plan, navigation, view/export, DOCX generation, save-token handling |
| 10 | Photos and field/mobile capture | 3110-3600, 17002-17329 | 900 | shared-photo path safety, Google Photos download, report photo storage, mobile clock-in, browse/download/preview/thumbnail; field-work routes also live in `field_work.py` |
| 11 | Employee reimbursement/expenses | 5522-5872, 17519-18257 | 1,090 | expense form/items/attachments, approval/return/delete, duplicate detection, AI interpretation/review, payment-order creation/cancellation bridge |
| 12 | Customer reimbursement/settlement | 3801-5521, 15996-16555 | 2,280 | rate resolution, labor/expense merge, settlement totals/gates, PDF/email/attachments, approval/reset/delete, invoice bridge; expense-selection UI is partly in `settlement_review.py` |
| 13 | Invoices, delivery and exports | 2585-2801, 18258-19163, 19402-19473 | 1,430 | invoice CRUD/status/payment, items and attachments, settlement-to-invoice conversion, HTML/PDF/ZIP generation, SMTP delivery |
| 14 | Payroll and labor | 3893-4040, 13815-14747 | 1,100 | labor-hour splitting, holidays, payroll ranges/details/calendar, payslip and batch payloads, XLSX export; employee payments consume `payroll_rows_for_period` |
| 15 | Reports/dashboard/audit/export | 6859-6895, 13421-13930, 14508-15148, 19529-19712 | 1,250 | dashboard metrics/charts, invoice/order/buyer/report/expense/settlement queries, visible-table XLSX, processing queue, audit report |
| 16 | General AI assistant and AI settings | 9302-9929 | 630 | system settings, DeepSeek/vision settings, cross-domain business search, LLM call/tool loop, connection test, assistant page/chat |
| 17 | AI daily report | 1338-1552, 9930-12671 | 2,950 | draft access/CSRF, chat, draft lifecycle, photo discovery/classification, validation, workers, mileage, attachment manifest, compliance and formal save |
| 18 | Admin/company/knowledge/database console | 12706-13420 | 1,000 | permission editor, database console, knowledge-document versions/files, company profile and attachments |
| 19 | Finance/payments/loans/banking/assets integration | imports at 82, calls at 15049 and 18006, registration at 19719 | fewer than 50 in `app.py` | implementation is already concentrated in the 922-line `employee_finance.py`; app supplies payroll, expense notification, DB, permission and audit dependencies |
| 20 | Existing external modules and wiring | imports at 59-129, registration at 19713-19719 | about 80 | `field_work`, `staff_reports`, `rate_engine`, `settlement_review`, `profitability`, `travel_tools`, `employee_finance`, plus AI service packages |

The estimates overlap where a helper serves more than one domain; the physical
line-range inventory at the end of this document gives a non-overlapping view.

## Domain details and dependencies

### Application, database and startup

- Functions: `db`, `close_db`, `verify_data_directory_identity`,
  `ensure_postgres_admin`, `init_db`, `now`.
- Database: `settings`, `users`, `invoice_schema_version` through
  `verify_postgres_schema`.
- External modules: `database.PostgreSQLConnection`, schema verifier and writer
  lock.
- Important behavior: importing `app` executes data-directory verification,
  schema verification, and admin bootstrap. Tests therefore must activate the
  `invoice_test` URL before importing the module.
- Risk: an application factory cannot be introduced safely until import-time
  startup and module registration are separated without changing startup
  failure semantics.

### Authentication, permissions and navigation

- Routes: `login`, `register`, `logout`, `switch_language`, mobile app/PWA
  endpoints, `messages`, `message_detail`, `workspace`, and
  `menu_permissions`.
- Tables: `users`, `countries`, `country_translations`, `messages`,
  `role_menu_permissions`, `role_action_permissions`, `user_service_orders`,
  `audit_logs`.
- Templates/static: `login.html`, `register.html`, `messages.html`,
  `workspace.html`, `menu_permissions.html`, `base.html`; `register.js`,
  `messages.js`, `workspace.js/css`, `navigation.js`, `ui-i18n.js`.
- Cross-domain calls: almost every domain calls `login_required`,
  `has_action_permission`, `normalized_role`, and an object-specific access
  guard. `required_action_for_request` contains a centralized endpoint-to-
  permission map, so every route move must preserve its endpoint name.

### Customers, sites and reference data

- Routes: clients, owners, manufacturers, buyers/import, countries,
  work-order types and projects CRUD.
- Tables: `clients`, `payment_terms`, `owners`, `manufacturers`, `buyers`,
  `countries`, `country_translations`, `work_order_types`, `projects`,
  `invoice_items`, `expense_items`, `expenses`.
- Templates/static: `clients.html`, `client_form.html`, `owners.html`,
  `manufacturers.html`, `buyers.html`, `buyer_form.html`, `countries.html`,
  `work_order_types.html`, `work_order_type_form.html`, `projects.html`,
  `project_form.html`; `countries.js`, `service-order-form.js`, common ERP grid
  assets.
- Cross-domain calls: payment terms feed clients and invoices; buyers feed work
  orders, maps and mobile clock-in; projects are shared by invoices, expenses,
  settlements and profitability. Project normalization/merge functions are
  catalog migration logic, not truly global helpers.

### Contracts, rates and payment terms

- Routes: contract list/create/detail/edit/delete and attachment routes;
  payment-term list/edit/toggle/recalculate. Contract-rate routes are
  registered by `rate_engine.register_rate_routes`.
- Tables: `contracts`, `contract_attachments`, `clients`, `service_orders`,
  `contract_rate_versions`, `contract_rate_items`, `payment_terms`, `invoices`.
- Templates/static: `contracts.html`, `contract_form.html`,
  `contract_detail.html`, `contract_rate_versions.html`, `payment_terms.html`;
  `payment-terms.js`, attachment preview assets.
- Cross-domain calls: service orders select contracts; settlement billing calls
  `contract_rate`; invoices calculate due dates from the client's payment term.
  `rate_engine.py` calls back into app via contract access, permissions, DB,
  audit and time helpers.

### Employees, payroll and employee finance

- Employee routes: users/profile/status/delete, user attachment endpoints,
  employee-grade state/membership/delete, payroll subsidy settings.
- Payroll routes: labor report, payroll report/detail/calendar/batch/export.
- Tables: `users`, `user_attachments`, `user_service_orders`,
  `employee_grades`, `employee_rate_versions`, `employee_rate_items`,
  `service_reports`, `service_report_workers`, `settings`.
- Templates/static: `users.html`, `user_form.html`, `employee_grades.html`,
  member fragments, `payroll_subsidies.html`, `labor_hours_report.html`,
  `payroll_report.html`, `payroll_detail_report.html`, `payroll_calendar.html`;
  user attachment/address/order-assignment scripts and report/grid assets.
- Cross-domain calls: service reports are payroll's source of hours;
  `rate_engine.employee_rate` supplies cost/pay rates; `employee_finance.py`
  calls `payroll_rows_for_period` when generating payment orders. User templates
  call the finance module's `employee_finance_summary` Jinja global.

### Work orders, maps and geocoding

- Routes: order list/new/edit/detail/delete, calendar, map/static image,
  geocode queue/retry and Google Photos import.
- Tables: `service_orders`, `clients`, `buyers`, `owners`, `manufacturers`,
  `contracts`, `work_order_types`, `user_service_orders`, `service_reports`,
  `expenses`, `customer_reimbursements`, `invoices`.
- Templates/static: `service_orders.html`, `service_order_form.html`,
  `service_order_detail.html`, `service_order_calendar.html`,
  `service_order_map.html`; service-order form/map scripts, grid assets and
  attachment ZIP support.
- External services: Google Geocoding/Maps, U.S. Census geocoder, Nominatim,
  `travel_tools.static_maps`.
- Cross-domain calls: detail view aggregates reports, expenses, settlement and
  invoices. Deletion checks all four domains. Access scope is reused by reports,
  photos, AI daily report and field work.

### Service reports, photos and field work

- Routes: report create/edit/delete/view/export, mileage evidence and assist;
  report attachments; mobile clock-in; shared-photo browse/download/preview;
  field routes registered by `field_work.py`.
- Tables: `service_reports`, `service_report_workers`,
  `service_report_saved_parts`, `service_report_replaced_parts`,
  `service_report_attachments`, `service_report_mileage_evidence`,
  `service_report_save_tokens`, `field_photos`, `clock_in_photos`, `users`,
  `service_orders`.
- Templates/static: `service_report_form.html`, `service_report_view.html`,
  `service_report_query.html`, `mobile_clock_in.html`, `field_work.html`,
  `field_photo_query.html`, `field_repair_report.html`; service-report,
  photo-ledger/time/type, field-work/watermark and mobile-clock-in assets.
- External modules: `service_report_assist`, `trip_policy`, `travel_tools`, PIL,
  python-docx and `image_processing`.
- Cross-domain calls: payroll and profitability consume report hours; settlement
  merges report labor/mileage; AI formal save creates reports and attachments;
  report deletion resets linked AI drafts.

### Expenses

- Routes: create/edit/detail/approve/return/delete; duplicate review; attachment
  preview/download/delete/transfer; interpretation and AI review.
- Tables: `expenses`, `expense_items`, `expense_attachments`,
  `expense_save_tokens`, `expense_duplicate_checks`,
  `expense_attachment_interpretations`, `expense_ai_reviews`, `projects`,
  `users`, `service_orders`, `employee_payment_orders`.
- Templates/static: `expense_form.html`, `expense_detail.html`,
  `expense_processing.html`, `expense_query.html`; `expense-form.js`,
  `expense-sources.js`, attachment preview/download assets.
- External modules: `ai_interpretation`, `ai_review` and workers.
- Cross-domain calls: approval creates/reopens an employee payment order;
  reset/delete cancels that order; selected approved items feed settlement;
  attachments can be copied to settlement and invoice outputs.

### Customer reimbursement/settlement

- Routes: settlement form, PDF/XLSX download/preview, approve/return/reset/delete,
  source synchronization and attachment endpoints. Expense-selection review is
  registered by `settlement_review.py`.
- Tables: `customer_reimbursements`, `customer_reimbursement_items`,
  `customer_reimbursement_attachments`,
  `customer_reimbursement_expense_links`, `expense_settlement_invoice_map`,
  plus reports/workers, expenses/items, projects, contracts/rates and invoices.
- Templates/static: `customer_reimbursement_form.html`,
  `settlement_expense_review.html`, `customer_reimbursement_query.html`;
  `customer-reimbursement.js`, `expense-sources.js`, report/grid assets.
- External modules: `rate_engine`, `settlement_review`, reportlab and XLSX helper.
- Cross-domain calls: this is the highest-coupled financial aggregate. It reads
  work orders, reports, workers, expenses, contract rates and mileage, then can
  create an invoice and send documents. Move it only after those interfaces are
  stable.

### Invoices

- Routes: list/create/edit/detail/status/void/delete/paid/unpaid/send;
  attachment and HTML/PDF/ZIP export endpoints.
- Tables: `invoices`, `invoice_items`, `invoice_attachments`,
  `invoice_save_tokens`, `clients`, `projects`, `service_orders`,
  `customer_reimbursements`, `email_delivery_logs`, `messages`.
- Templates/static: `invoices.html`, `invoice_form.html`, `invoice_detail.html`,
  `invoice_export.html`, `email_invoice.html`, invoice form/detail and attachment
  scripts.
- External libraries: ReportLab, python-docx, PIL, `smtplib`.
- Cross-domain calls: consumes client payment terms, work orders and settlement
  outputs; copies report/expense/settlement attachments; dashboard and reports
  query invoice totals and payment status.

### AI assistant and AI daily report

- General assistant routes: assistant page/chat and DeepSeek test.
- General assistant tables: `settings`, `llm_configs`, `knowledge_documents`,
  and read-only queries over invoices, orders, expenses, settlements and users.
- Daily-report routes: all `/api/ai/daily-report/*` and the two review-center
  pages, including draft/photo/worker/mileage/manifest/formal-save lifecycle.
- Daily-report tables: `ai_daily_report_drafts`, actions, photos, evidence,
  confirmations, manifests/sources/roles/prepared-assets/formal-commits, plus
  users, work orders, reports and report attachments.
- Templates/static: `ai_assistant.html`, `ai_daily_report_drafts.html`,
  `ai_daily_report_draft_detail.html`; `ai-assistant.js`,
  `ai-daily-report-drafts.js`, `ai-daily-report-review.js`.
- External modules: `llm_config`, `ai_interpretation`, `ai_review`, the
  `ai_daily_report` package, Google Routes and travel services.
- Cross-domain calls: AI daily report uses employee resolution, work-order
  access, photo storage, travel/mileage, and formal service-report save. It is
  large but much of its service logic is already outside `app.py`; the remaining
  route/orchestration layer should move only after those domain contracts exist.

### Finance, employee payments, advances, banking and assets

These features are already physically concentrated in `employee_finance.py`
rather than `app.py`:

- employee payments and salary agreements;
- employee advances (the current employee-loan model);
- finance overview and employee ledger;
- bank accounts and opening balances;
- bank transactions and CSV import;
- exact-match bank reconciliation;
- asset records, events, QR links and photos.

Tables include `employee_payment_orders`, `payment_order_sources`,
`payment_order_events`, `employee_salary_agreements`, `employee_advances`,
`employee_advance_applications`, `bank_accounts`, `bank_transactions`, `assets`,
`asset_events` and `asset_photos`, with joins to `users`, `expenses` and
`service_orders`. Templates are `employee_payments.html`,
`employee_payment_detail.html`, `employee_advances.html`,
`finance_overview.html`, `employee_ledger.html`, `bank_accounts.html`,
`bank_transactions.html`, `bank_reconciliation.html`, `assets.html` and
`asset_detail.html`.

The module is cohesive at the feature-family level, but its 922 lines now cover
four separable aggregates. It receives these app globals: `db`, `now`,
`login_required`, `has_action_permission`, `normalized_role`,
`lock_number_allocation`, `log_action`, `payroll_rows_for_period`,
`notify_expense_participants`, and `DATA_DIR`. App also calls
`ensure_expense_payment_order` and `cancel_expense_payment_order` using
`globals()`.

No dedicated company-loan route, table, template, service, or Python symbol was
found. Company loans must therefore not be treated as an existing extractable
domain; adding that feature would be separate product work.

## Shared helper assessment

### Helpers that are genuinely application-wide

- request-scoped database access: `db`, `close_db`;
- clock/timezone abstraction: `now`, `app_timezone`;
- authentication/session primitives: `current_user`, `login_required`;
- permission gateway: `has_menu_permission`, `has_action_permission`, role
  normalization and object-scope guard interfaces;
- audit/notification boundary: `log_action`, `create_message`, `notify_role`;
- settings access through a small typed settings service;
- generic safe path/file-response primitives, once separated from each domain's
  directory and ownership policy;
- presentation-only formatting filters such as `money`, `hours` and
  `local_datetime`.

### Helpers that look shared but belong to a domain

- `next_client_number`, buyer/owner/manufacturer numbering and import helpers:
  customer/site catalog;
- project normalization and MRO merge: project catalog;
- `invoice_totals`, `payment_label`, invoice attachment helpers and number
  allocation: invoices;
- contract form/attachment helpers: contracts;
- service-order access/dependency/count/geocoding helpers: work orders;
- report storage, workers, parts, time and photo helpers: service reports/photos;
- expense beneficiary, duplicate and attachment helpers: expenses;
- all reimbursement rate/merge/total/PDF helpers: settlements;
- payroll periods, holiday split and payslip helpers: payroll;
- `build_simple_xlsx`: export infrastructure, not payroll;
- DeepSeek search/call helpers: AI assistant;
- `safe_attachment_response` can be shared, but directory selection and
  authorization must stay in the owning domain.

### Shared code that should not move first

The permission matrices and `required_action_for_request`, DB/session lifecycle,
template globals, settings/key access, audit/message helpers, common path config,
and endpoint names are currently consumed by nearly every domain. Moving them
before explicit interfaces exist would create imports back into `app.py` and
increase coupling. Leave them in `app.py` as a compatibility kernel until most
business domains have moved.

## Existing and latent circular dependencies

There is no direct Python import cycle today because extracted modules do not
import `app`. Instead, seven modules receive the complete app namespace through
`globals()`. This hides several logical cycles:

- `app -> employee_finance -> app.payroll_rows_for_period` and
  `app.expense approval -> employee_finance`;
- `app -> settlement_review -> app settlement helpers`;
- `app -> profitability -> app report/reimbursement helpers`;
- `app -> rate_engine -> app contract access/permissions/audit`;
- `app -> field_work` and `travel_tools` -> app auth/config/access helpers;
- `app -> AI daily services -> formal service-report save`, while report delete
  calls back into AI draft reset logic;
- invoices depend on settlement documents while settlement can create invoices;
- templates and permission dispatch depend on stable global endpoint names.

If any extracted module starts importing `app`, these latent cycles become real
import cycles. Every phase must pass a narrow dependency object or import from a
lower-level shared package; it must never import the top-level `app.py`.

Tests also patch functions directly on the imported `app` module. Compatibility
re-exports are needed until those tests and any operational scripts have moved
to stable module APIs.

## Recommended final package structure

Use `invoice_tool/`, not `app/`, while the root `app.py` exists; an `app/`
package beside `app.py` creates ambiguous imports.

```text
app.py                         # transitional compatibility entry point
invoice_tool/
  application.py              # create_app and final registration
  dependencies.py             # explicit dependency protocols/container
  db.py                        # request-scoped connection wiring
  config.py                    # environment/path configuration
  auth/
    routes.py
    service.py
  permissions/
    policy.py
    routes.py
  catalog/
    routes.py                  # countries, projects, work-order types
    service.py
  customers/
    routes.py                  # clients, sites/buyers, owners, manufacturers
    service.py
    geocoding.py
  employees/
    routes.py
    service.py
  contracts/
    routes.py
    service.py
    rates.py                   # absorb rate_engine only after contracts move
  work_orders/
    routes.py
    service.py
    calendar.py
    maps.py
  service_reports/
    routes.py
    service.py
    documents.py
  photos/
    routes.py
    storage.py
    field.py
  expenses/
    routes.py
    service.py
    attachments.py
  settlements/
    routes.py
    service.py
    documents.py
  invoices/
    routes.py
    service.py
    documents.py
    delivery.py
  payroll/
    routes.py
    service.py
    exports.py
  finance/
    payments.py
    advances.py
    banking.py
    assets.py
    routes.py
  ai/
    assistant_routes.py
    daily_report_routes.py
  knowledge/
    routes.py
    service.py
  reports/
    routes.py
    dashboard.py
    exports.py
  admin/
    routes.py
    settings.py
  shared/
    audit.py
    files.py
    formatting.py
    notifications.py
    time.py
```

Do not force every directory to contain `routes.py`, `services.py` and
`repository.py`. Add a repository/query module only when SQL is reused or a
route/service split materially reduces coupling. Templates and static assets can
stay in the existing root directories throughout the migration.

## Endpoint and registration strategy

Early phases should use a small `register_routes(app, deps)` function that calls
`app.get`, `app.post`, or `app.add_url_rule` with the existing endpoint name.
This matches the current registration style and avoids Blueprint endpoint
prefixes. Keep temporary imports/re-exports in root `app.py` for tests and
scripts that access functions there.

Blueprints are recommended after most routes have moved. A Flask Blueprint
normally changes `endpoint` from `name` to `blueprint.name`; therefore Blueprint
adoption must either:

1. add explicit compatibility URL rules for every old endpoint name; or
2. update every Python/template/static `url_for` reference and retain aliases
   until compatibility is proven.

Do not combine Blueprint conversion with business extraction in the same
commit.

## Progressive extraction phases

Every phase preserves URL paths, endpoint names, schema, SQL semantics,
permissions, templates and responses. Each phase is a standalone commit and can
be rolled back independently. All database tests use only PostgreSQL
`invoice_test`.

### Phase 1 — Knowledge base pilot

- Move: lines 12875-13326, including PDF extraction/storage, document/version
  lookup, CRUD, preview and download routes.
- Destination: `invoice_tool/knowledge/routes.py` and `service.py` (one file is
  acceptable initially).
- Estimated reduction: 440-470 lines.
- Compatibility: root re-exports for helper names; register routes directly on
  `app` with unchanged endpoint names.
- Dependencies: explicit `db`, `now`, `login_required`, permission check,
  `log_action`, `KNOWLEDGE_BASE_DIR`, size limits; no import of root `app`.
- Templates/static: `knowledge_base.html`, `knowledge-base.js`, attachment
  preview assets.
- Tables: `knowledge_documents`, `knowledge_document_versions`, `users`.
- Circular risk: low. General AI assistant reads the same tables but does not
  call the knowledge route helpers.
- Tests: `test_knowledge_base.py`, permission/menu tests, attachment security;
  then the complete suite. Risk: low.

### Phase 2 — Customers and reference catalog

- Move: client, owner, manufacturer, buyer/import, country, project and
  work-order-type helpers/routes (2169-2445 and 7448-8455).
- Destination: `catalog/` plus `customers/`; keep project normalization in
  catalog service.
- Estimated reduction: 1,250-1,400 lines.
- Compatibility: re-export number/import/project helpers used by tests; retain
  all endpoint names.
- Templates: client/buyer/owner/manufacturer/country/project/work-order-type
  templates and their existing static files.
- Tables: catalog/customer tables plus invoice/expense item references used by
  project merge.
- Cross dependencies: payment terms, work orders, clock-in, invoices, expenses
  and profitability.
- Circular risk: medium; do not import work-order or invoice modules from the
  catalog. Expose IDs/data through service functions.
- Tests: basic-data permissions, registration address, manufacturer, map/static
  map and project/settlement mapping tests; complete suite. Risk: medium.

### Phase 3 — Employees and employee grades

- Move: user/profile/attachment/order-assignment and employee-grade routes and
  helpers, plus payroll subsidy settings only if kept as a separate small
  payroll dependency.
- Destination: `employees/routes.py`, `employees/service.py`.
- Estimated reduction: 800-950 lines.
- Compatibility: root aliases for user/order assignment and grade helper names;
  preserve `employee_finance_summary` template global.
- Templates/static: users, user form, grades and fragments; user address,
  assignment and attachment scripts.
- Tables: `users`, `user_attachments`, `user_service_orders`,
  `employee_grades`, rate-version tables, settings.
- Cross dependencies: permissions, reports, payroll, expenses, assets and AI
  employee resolution.
- Circular risk: medium. Finance may query employees but employee service must
  not import finance; template summary remains injected at application wiring.
- Tests: employee grades, registration, user/menu/security and staff-report
  tests; complete suite. Risk: medium.

### Phase 4 — Contracts, rates and payment terms

- Move: contract routes/helpers/attachments and payment-term routes/helpers.
  Adapt `rate_engine.py` only enough to receive a narrow contracts dependency;
  do not rewrite calculations.
- Destination: `contracts/routes.py`, `service.py`, later `rates.py`.
- Estimated reduction: 650-750 lines from `app.py`.
- Compatibility: preserve contract/payment-term helper exports and all route
  endpoint names; keep the existing rate endpoint names.
- Templates/static: contract and payment-term templates/scripts.
- Tables: contracts/attachments/rate versions/items, clients, payment terms,
  invoices and service orders.
- Cross dependencies: customers, work orders, settlement and invoices.
- Circular risk: medium because `rate_engine` currently calls app contract
  guards through `globals()`.
- Tests: payment terms, contract/rate coverage through settlement,
  reimbursement and PostgreSQL acceptance tests; complete suite. Risk: medium.

### Phase 5 — Work orders, calendar, maps and geocoding

- Move: order access/dependency helpers, CRUD/detail/calendar/map routes,
  geocoding and Google Photos job trigger.
- Destination: `work_orders/routes.py`, `service.py`, `calendar.py`, `maps.py`;
  generic geocoder clients may live under `customers/geocoding.py`.
- Estimated reduction: 1,250-1,450 lines.
- Compatibility: app aliases for `require_service_order`, access/filter and
  geocode functions; endpoint names unchanged.
- Templates/static: all service-order list/form/detail/calendar/map assets.
- Tables: work orders and all detail-page/deletion dependency tables.
- Cross dependencies: customers/contracts/users; downstream links to reports,
  expenses, settlement and invoices.
- Circular risk: high. Detail aggregation must call lower-level query
  interfaces, not import downstream route modules.
- Tests: all service-order, map, status/calendar, Google photo and attachment ZIP
  tests; PostgreSQL acceptance; complete suite. Risk: high.

### Phase 6 — Service reports and photos

- Move: report storage/time/worker/part helpers, report CRUD/view/export,
  mileage evidence, assist plan, mobile clock-in and shared-photo routes.
- Destination: `service_reports/` and `photos/`; preserve `field_work.py` as a
  separately registered adapter until its dependency contract is narrowed.
- Estimated reduction: 2,000-2,400 lines.
- Compatibility: broad temporary root re-exports are expected because tests and
  AI/report modules patch these helpers directly.
- Templates/static: report forms/views/query, mobile clock-in, field work and
  all report/photo scripts.
- Tables: all report/worker/part/attachment/evidence/save-token/photo tables.
- Cross dependencies: work orders, employees, rates, travel, payroll,
  settlement, profitability and AI daily report.
- Circular risk: very high, especially AI formal save versus report deletion.
- Tests: all report, photo, field-work, mobile clock-in, travel/assist and AI
  formal-save tests; complete suite mandatory. Risk: very high.

### Phase 7 — Payroll and labor

- Move: holiday/time splitting, labor queries, payroll period/range/detail,
  payslip/batch/calendar and payroll export logic.
- Destination: `payroll/routes.py`, `service.py`, `exports.py`.
- Estimated reduction: 950-1,150 lines.
- Compatibility: root aliases for `payroll_rows_for_period/range` until finance
  uses an explicit payroll service.
- Templates/static: labor/payroll reports and calendar, report grid/export
  assets.
- Tables: reports/workers/users/grades/rates/settings and salary agreements.
- Cross dependencies: service reports and employee rates; finance creates
  payment orders from payroll output.
- Circular risk: high until `employee_finance` receives a payroll service
  callable rather than the whole app namespace.
- Tests: payroll range/details/customer report, employee grades, employee
  finance and acceptance tests; complete suite mandatory. Risk: high.

### Phase 8 — Employee expenses

- Move: expense business helpers/routes, attachments, duplicate checks,
  interpretation and review orchestration.
- Destination: `expenses/routes.py`, `service.py`, `attachments.py`.
- Estimated reduction: 1,000-1,150 lines.
- Compatibility: root aliases for settlement and tests; preserve direct
  monkeypatch points during transition.
- Templates/static: expense forms/detail/query/processing and expense scripts.
- Tables: all expense tables, projects, users, orders and payment-order link.
- Cross dependencies: work orders, settlements, AI review and employee finance.
- Circular risk: high because expense approval/cancellation calls finance while
  finance payment completion notifies expense participants.
- Tests: all expense, duplicate, AI review/interpretation, transfer and payment
  tests; complete suite mandatory. Risk: high.

### Phase 9 — Settlements/customer reimbursement

- Move: all settlement calculation, source merge, attachments, PDF/email and
  routes; absorb `settlement_review.py` only after explicit expense/report/rate
  interfaces exist.
- Destination: `settlements/routes.py`, `service.py`, `documents.py`.
- Estimated reduction: 2,200-2,500 lines.
- Compatibility: temporary root exports for calculation helpers heavily patched
  by tests; all endpoint aliases retained.
- Templates/static: settlement form/review/query and reimbursement scripts.
- Tables: settlement tables plus report, expense, rate, project and invoice
  dependencies.
- Cross dependencies: work orders, reports, expenses, contracts/rates and
  invoices.
- Circular risk: very high; settlement-to-invoice must call an injected invoice
  creator, not import invoice routes.
- Tests: every reimbursement/settlement/profitability test, settlement XLSX,
  expense mapping, payment terms and acceptance; complete suite mandatory.
  Risk: very high.

### Phase 10 — Invoices, documents and delivery

- Move: invoice helpers/routes, attachments, settlement conversion, PDF/ZIP/HTML
  rendering and SMTP delivery.
- Destination: `invoices/routes.py`, `service.py`, `documents.py`, `delivery.py`.
- Estimated reduction: 1,350-1,500 lines.
- Compatibility: root aliases for totals/export/email helpers and endpoint names.
- Templates/static: invoice list/form/detail/export/email and invoice scripts.
- Tables: invoice tables, clients, projects, work orders, settlement and email
  delivery/audit/message tables.
- Cross dependencies: payment terms, work orders, settlement, reports and
  dashboard/reporting.
- Circular risk: high around settlement conversion and attachment collection.
- Tests: payment terms, customer report/payroll, attachments, invoice portions
  of acceptance/E2E tests and email/map tests; complete suite mandatory.
  Risk: high.

### Phase 11 — Finance module normalization

- Move: no large block from `app.py`; split `employee_finance.py` into payments,
  advances, banking/reconciliation and assets after payroll, expense, employee
  and work-order services are stable.
- Destination: `invoice_tool/finance/`.
- Estimated `app.py` reduction: 20-50 lines; approximately 922 external lines
  become four cohesive modules.
- Compatibility: preserve all current route endpoints and the
  `employee_finance_summary`, `ensure_expense_payment_order` and
  `cancel_expense_payment_order` APIs.
- Templates/tables: the existing finance/asset templates and finance tables.
- Cross dependencies: explicit payroll service, expense notification service,
  employee directory, work-order lookup, audit and permissions.
- Circular risk: medium after prior phases; high if attempted earlier.
- Tests: `test_employee_finance.py`, payroll, expense, users/assets and
  PostgreSQL acceptance; complete suite mandatory. Risk: medium.

### Phase 12 — General AI assistant

- Move: AI settings helpers, business-record search, DeepSeek call/tool loop and
  assistant routes. Keep general system settings separate if necessary.
- Destination: `ai/assistant_routes.py` and existing AI provider/config modules.
- Estimated reduction: 500-650 lines.
- Compatibility: preserve patch points such as `call_deepseek_chat` in root app
  until tests move.
- Templates/static: AI assistant and company/system settings where relevant.
- Tables: settings/LLM configs/knowledge plus read-only domain searches.
- Cross dependencies: query-only interfaces from invoices, work orders,
  expenses, settlement, users and knowledge.
- Circular risk: medium; the assistant must not import route modules.
- Tests: AI assistant, business-default/security and settings tests; complete
  suite. Risk: medium.

### Phase 13 — AI daily report orchestration

- Move: AI-draft permissions/CSRF and all remaining AI daily-report routes and
  orchestration.
- Destination: `ai/daily_report_routes.py`; keep existing
  `ai_daily_report/` service package.
- Estimated reduction: 2,800-3,050 lines.
- Compatibility: retain root route/helper aliases used by extensive tests and
  preserve every API endpoint name and response shape.
- Templates/static: AI daily draft list/detail and both JS bundles.
- Tables: all AI daily tables plus work-order, user, report and attachment
  dependencies.
- Cross dependencies: explicit work-order, employee, report, photo, travel and
  formal-save interfaces.
- Circular risk: very high; this phase should follow reports/photos/work orders.
- Tests: all `test_ai_daily_report_*`, photo classification, report/formal-save,
  travel and acceptance tests; complete suite mandatory. Risk: very high.

### Phase 14 — Dashboard, reports, exports and profitability

- Move: dashboard metrics/charts, report-center queries, processing/audit views,
  common XLSX export and the already-extracted profitability registration.
- Destination: `reports/routes.py`, `dashboard.py`, `exports.py`.
- Estimated reduction: 1,100-1,350 lines.
- Compatibility: preserve report endpoint names, query parameter behavior and
  Jinja context; keep `export_visible_report` contract unchanged.
- Templates/static: dashboard and all query/report templates, report grid,
  multiselect and export assets.
- Tables: read-only joins across most business domains plus audit logs.
- Cross dependencies: almost every domain, but mostly query-only after earlier
  phases expose stable readers.
- Circular risk: high if reports import route modules; low if they consume query
  services.
- Tests: report filters, payroll/labor, profitability, ledger export, grid/UI
  contract and acceptance tests; complete suite mandatory. Risk: high.

### Phase 15 — Platform kernel, Blueprints and application factory

- Move: remaining auth/admin/company/messages/settings, shared file/audit/time
  utilities, explicit dependency container and registration wiring.
- Destination: `application.py`, `auth/`, `permissions/`, `admin/`, `shared/`.
- Estimated reduction: enough to leave approximately 800-1,200 lines in the
  root compatibility entry point, or make root `app.py` a very small importer
  once deployment entry points are deliberately changed.
- Compatibility: this is the only appropriate stage to introduce `create_app`
  and then Blueprint registration. Maintain old endpoint aliases and import-time
  startup behavior until deployment and tests explicitly switch.
- Templates/static: `base.html`, auth/admin/company/messages/workspace/PWA
  assets.
- Tables: users, permissions, settings, company/user attachments, messages and
  audit logs.
- Cross dependencies: all modules through stable interfaces only.
- Circular risk: highest architectural risk, but lower operational risk after
  business modules no longer depend on root `app`.
- Tests: startup/admin security, login/session/permissions/PWA/workspace,
  PostgreSQL deploy/schema checks and the complete suite. Risk: very high.

## Recommended order and stopping points

Phase 1 is deliberately the knowledge base, not finance. Knowledge base code is
contiguous, owns its files and tables, has one main template, has limited
cross-domain calls, and already has focused tests. It exercises route
registration, endpoint preservation, file handling and compatibility exports
without entering the high-coupling financial workflows.

Finance is not a good first extraction because it is already outside `app.py`.
Its remaining problem is dependency shape, not physical location. Splitting it
before payroll and expense interfaces exist would hard-code the present hidden
cycle instead of removing it.

After each phase: run its focused tests, inspect `app.url_map` for unchanged
rules/endpoints/methods, run `git diff --check`, then run the complete suite
before committing. Do not combine two phases merely because the first was
small.

## Highest-risk regions

1. settlement calculation and settlement-to-invoice conversion;
2. service reports/photos and AI formal save/delete synchronization;
3. AI daily-report route orchestration;
4. expense approval/payment/cancellation lifecycle;
5. payroll-to-employee-payment generation;
6. invoice document/attachment/email assembly;
7. centralized permission dispatch keyed by endpoint name;
8. application factory conversion because startup currently runs at import.

## Long-term target

After all phases, the root `app.py` should contain only compatibility imports
and deployment entry wiring, while `invoice_tool/application.py` owns Flask app
creation, configuration, extension setup and module registration. A practical
intermediate target is 800-1,200 lines; after deployment/test entry points move
to `create_app`, root `app.py` can fall below 100 lines.

Blueprints and an application factory are recommended, but only in Phase 15.
Using them earlier would mix endpoint/import-lifecycle changes with business
movement and make rollback much harder.

## Physical line-range inventory

This non-overlapping inventory helps future agents avoid reading the whole file:

| Lines | Contents |
|---:|---|
| 1-596 | imports, paths, global constants, status/role/menu/action policy definitions |
| 597-838 | Flask instance, DB/startup, project cleanup and admin bootstrap |
| 839-1219 | locale/country/settings/API keys and generic named attachments |
| 1221-2168 | request user/session, AI draft access, permissions, access guards and formatting/Jinja globals |
| 2169-2445 | client/site numbering, owner/manufacturer and buyer import helpers |
| 2446-2584 | contract number/attachment/form helpers |
| 2585-2801 | invoice numbering and attachment helpers |
| 2802-3800 | work-order/report access, report/shared-photo storage, save tokens and form parsing |
| 3801-5521 | settlement calculations, source merge, files, PDF and email |
| 5522-5872 | expense access, files and duplicate detection |
| 5873-6090 | service-report workers/options/parts/form persistence and invoice loader |
| 6090-6196 | messages, audit and email-delivery logs |
| 6197-6573 | geocoding clients and order/buyer geocode persistence |
| 6574-6615 | role notifications and message text |
| 6616-6858 | login/register/logout/language/messages/PWA/workspace routes |
| 6859-7190 | dashboard and contract routes |
| 7190-7447 | payment-term logic/routes |
| 7448-8396 | clients, owners, manufacturers, buyers, work-order types and projects |
| 8397-9301 | countries, employee grades, payroll subsidies and users |
| 9302-9929 | system/AI settings and general AI assistant |
| 9930-12671 | AI daily-report orchestration and review-center routes |
| 12672-12874 | AI draft pages, permission editor and database console |
| 12875-13326 | knowledge base |
| 13327-13420 | company profile/attachments |
| 13421-15148 | invoice/report queries, payroll, calendar, exports, expense queue and audit report |
| 15149-15995 | work-order list/calendar/map/CRUD/detail and photo import |
| 15996-16555 | settlement routes and attachments |
| 16556-17518 | service-report, mobile clock-in and shared-photo routes |
| 17519-18257 | expense routes, attachment AI and settlement transfer |
| 18258-19473 | attachment ZIP, invoices, exports, DOCX and email |
| 19474-19712 | error handlers and dashboard/report chart helpers |
| 19713-19728 | external module registration and startup |


## Completed structural extractions (2026-10-17)

These phases build on the map above; historical line locations remain a navigation baseline.

- Payment terms: `invoice_tool/payment_terms/{calculations,services,routes}.py`, committed as `4798b77`. Legacy helper entry points and endpoint names are retained.
- Contracts and attachments: `invoice_tool/contracts/{services,routes}.py`. Five helpers and eight routes moved; contract access guards and rate/settlement engines remain in their existing locations. Services receive explicit runtime dependencies through legacy wrappers. Routes receive explicit dependencies at registration; callable dependencies resolve the current app helper and attachment root at request time.
- No application factory or Blueprint migration is included in these phases. No business rule, schema, template, or deployment changes are included.

Contract regression: `test_contracts_module_contract.py`; structural equivalence: `python scripts/check_contracts_structure.py` (compare against preceding `4798b77`).


### Work-order CRUD extraction (phase 3a)

`invoice_tool/service_orders/services.py` now owns `next_service_order_number`,
`service_order_dependency_counts`, and `service_order_delete_blockers`.
`invoice_tool/service_orders/routes.py` registers the five existing list/create/edit/detail/delete
endpoints. The app keeps legacy helper wrappers and view aliases. Dependencies are explicit;
request-time callbacks resolve the current app helpers, including helpers used in comprehensions.

Shared access guards, calendar, maps, geocoding, photos/import jobs, settlement services and invoice
services remain in their existing modules. Their business logic is unchanged.

Checks: `test_service_orders_module_contract.py` and
`python scripts/check_service_orders_structure.py` (compare with `10c2570`).
Existing settlement-column fixtures lack required `customer_reimbursements.series_id` in the shared
test schema; their five failures were observed before this extraction and remain separately recorded.


### Work-order calendar and map extraction (phase 3b)

`invoice_tool/service_orders/calendar_map_services.py` contains calendar access filters,
calendar rows/weeks/lanes, buyer map queries/payloads and visitor-country detection.
`invoice_tool/service_orders/calendar_map_routes.py` registers calendar, map, static image,
geocode-next and retry-failed endpoints. App-level helpers and views remain compatible.

Map configuration and date values resolve at request time; callable dependencies resolve the
current app helper. Geocoder clients, geocoding persistence and exception type remain in app.py.
Static-map HTTP client implementation and templates/scripts remain unchanged.

Checks: `test_calendar_map_module_contract.py`, existing map/status tests and selected existing
calendar regression cases; `python scripts/check_calendar_map_structure.py` compares with `d0daa0d`.


### Service-report reading and attachment access (phase 4a)

`invoice_tool/service_reports/report_read_services.py` owns attachment grouping and global/same-order
adjacent report queries. `report_read_routes.py` registers read-only view, Word export, attachment
preview and attachment delete endpoints. App-level helper wrappers and view aliases remain.
Dependencies resolve the current app helper at call time; navigation still uses existing access filters.

Report create/edit/delete, save-token idempotency, detail persistence, uploads/storage relocation,
generated mileage evidence, mobile capture and Word document generation remain in their existing locations.
This phase changes no authorization, payroll/settlement lock, persistence or filesystem rule.

Checks: `test_report_read_module_contract.py`, existing report navigation/edit-navigation/external-view/copy
and attachment safety tests; `python scripts/check_report_read_structure.py` compares against `911a3ac`.


### Service-report save lifecycle (phase 4b)

`invoice_tool/service_reports/report_write_services.py` owns save-token claim/finalize, report workers,
worker/writer options, posted writer validation, parts queries/parsing, form defaults and worker/parts
persistence. `report_write_routes.py` registers create/edit/delete with the original URL, endpoint,
GET/POST methods, guards, statement order and transaction boundaries.

App wrappers preserve legacy call signatures and resolve helper dependencies at call time. Date and
report attachment root resolve dynamically. Upload/storage relocation, image/shared-photo processing,
mileage evidence and time/mileage calculation implementations remain in their existing locations.

Check `python scripts/check_report_write_structure.py` for function-body equivalence and exact
URL/endpoint/HTTP-method registration. `test_report_write_module_contract.py` covers idempotency,
validation/upload rollback, payroll-reference deletion guard and report cleanup.


### Service-report attachment storage (phase 4c)

`invoice_tool/service_reports/storage.py` owns 16 report-specific helpers: storage context, relative/
absolute paths, directory creation/pruning, relocation, upload enumeration, image-compression adapter,
normalized photo names, duplicate detection, image/file/shared-photo persistence, generated evidence
replacement and upload orchestration. The original app helper signatures remain as wrappers with
explicit dependencies resolved for every call, including the current attachment root and photo folders.

No route moves in this phase. Generic file hashing, shared-photo path resolution, photo import jobs,
image compression implementation, mileage-evidence/assist endpoints and DOCX rendering remain where
they were. File/SQL ordering, duplicate rules and error translation are preserved.

Checks: `test_report_storage_module_contract.py` uses temporary real image/PDF files; run
`python scripts/check_report_storage_structure.py` to compare with `72ee9e3`.


### Report assist preview and mileage evidence (phase 4d)

`invoice_tool/service_reports/assist_routes.py` registers the existing assist-plan preview and
mileage-evidence generation POST endpoints. Public app view aliases and endpoint/action-policy names
are retained. Current DATA_DIR, SHARED_PHOTOS_DIR, trip default, datetime and callable services resolve
through explicit callbacks; no globals dictionary or reverse app import is introduced.

Photo discovery/metadata, employee resolution, route calculation, evidence image creation and report
storage implementations remain in their existing modules. Authorization, fuel-efficient route policy,
provider timeout/retry values, fingerprint reuse/upsert and commit/rollback ordering remain unchanged.

Checks: `test_report_assist_module_contract.py` mocks external services and exercises real invoice_test
HTTP/database behavior. `python scripts/check_report_assist_structure.py` verifies function bodies and
exact URL/endpoint/view/method registration against `4b7da14`.


### Report DOCX generation and Word packaging (phase 4e)

`invoice_tool/service_reports/documents.py` owns `build_service_report_docx`,
`service_report_docx_filename` and `service_order_report_word_attachments`. The app retains same-signature
wrappers and supplies explicit runtime dependencies, including python-docx/Pillow objects and the
current worker, part, attachment and company helpers. No route is moved.

Formatting, page geometry, tables, text filtering, picture conversion/error fallback, filename collision
handling, report ordering, MIME types and error messages are preserved. Delivery/email/settlement/ZIP
logic remains in its existing location and consumes the legacy app entry points.

`test_report_documents_module_contract.py` compares actual DOCX ZIP member contents with a separately
compiled previous renderer from `3604351`: XML/style/relationships and media bytes, excluding only ZIP
container timestamps by comparing member contents. It also checks real HTTP export and Word packaging.
Run `python scripts/check_report_documents_structure.py` for structural equivalence.


### Mobile clock-in and shared-photo access (phase 4f)

`invoice_tool/photos/services.py` owns clock-in site scope, shared-photo/picture path resolution,
relative paths, image counts and order photo status. `routes.py` registers mobile clock-in page/upload,
shared-photo browse/download/preview/thumbnail. Legacy app helper signatures and view aliases remain;
callable dependencies and date/datetime/extension settings resolve at runtime.

Original client/internal-user guards, capture-time/coordinate/image validation, NAS folder layout,
cached/fallback thumbnails, picture-only download rules, ZIP deduplication and close-time cleanup remain
unchanged. Generic image validation, shared-root configuration, photo worker, Google Photos import and
field-photo ledger implementation stay in their current modules.

Checks: `test_photo_capture_module_contract.py`, existing clock-in/NAS day-folder tests;
`python scripts/check_photo_capture_structure.py` checks bodies and exact route registration against
`329071d`. Test files and ZIPs are contained in temporary directories.


### Report forms and shared time/mileage calculations (phase 4g)

`invoice_tool/service_reports/forms.py` owns posted time, worker IDs/rows and form-derived travel time,
service hours and mileage. `calculations.py` owns billed mileage, minute parsing/rounding, hour text,
per-worker duration fallback and actual report date. Twelve legacy app signatures remain as wrappers.

Current request/form policies and cross-helper callbacks resolve on every call. Payroll, employee
settlement and customer billing callers keep their app entry points; no change is made to payroll
generation, rates, subsidies, per-person/per-vehicle mileage, trip policies, rounding or date fallback.
Holiday calculations and generic display filters remain in their existing locations. No route moves.

Checks: `test_report_calculations_module_contract.py` compares with separately compiled `29b690c`
functions; payroll detail/range/boundary and hour display tests verify downstream behavior.
Run `python scripts/check_report_calculations_structure.py` at this phase's commit for equivalence.
One imported settlement-filter fixture lacks current required series_id; its failure was observed
before migration and is separately deselected, without modifying business logic or schema.


### Employee expense forms and item persistence (phase 5a)

`invoice_tool/employee_expenses/forms.py` owns expense defaults, ordered item reads,
posted item validation and item persistence. Four app entry points retain their signatures
and resolve explicit dependencies on every call. Existing item IDs are preserved on edits;
removal of settlement-linked items remains forbidden. Attachment cleanup retains its policy.
Routes, beneficiary access, approval, payment, numbering and duplicate detection stay in place.
No amount, fuel classification, currency or submission rule changes.

Checks: `test_expense_forms_module_contract.py`, expense on-behalf/business-purpose/robust-save
regressions and settlement link display tests. At this phase's commit run
`python scripts/check_expense_forms_structure.py` for equivalence against `1ee793d`.
Three pre-existing settlement fixtures omit required series_id and are separately excluded.


### Employee expense attachment storage (phase 5b)

`invoice_tool/employee_expenses/storage.py` owns attachment directory/path resolution,
SHA-256/image fingerprint generation, individual and item-bound uploads, ordered attachment
reads and line grouping. Seven app entry points retain signatures and pass current dependencies
explicitly on each call. File type restrictions, random stored names, upload metadata, file save
error handling and best-effort image hashing remain unchanged. General uploads stay rejected;
legacy unassigned files remain visible in the empty-key group.

Duplicate matching/review, access checks, transfer/synchronization and routes remain in app.py.
Checks: test_expense_storage_module_contract.py covers actual temporary-file/invoice_test storage
round trips and failure paths; expense interpretation/deletion and prior expense tests cover callers.
At this phase's commit use scripts/check_expense_storage_structure.py for equivalence to 2c718e4.
Three previously reproduced series_id fixture failures are still separately excluded.


### Employee expense duplicate checks (phase 5c)

`invoice_tool/employee_expenses/duplicates.py` owns image hash distance, attachment match
context, lazy fingerprints, optional AI analysis, duplicate-check generation and result reads.
Six legacy app wrappers resolve explicit dependencies per call; review routes remain in app.py.
Scoring, thresholds, date/amount tolerance, beneficiary matching, same-line evidence suppression,
candidate limits, three-call AI budget and historical pending-result visibility remain unchanged.
AI settings and network client implementations stay at their current entry points.

Checks: test_expense_duplicates_module_contract.py compares scoring database writes against
independently compiled 79b89d3 functions for 192 combinations, checks AI budget/file fallbacks,
and verifies identical synthetic receipts, rerun behavior and manager review in invoice_test.
At this phase's commit use scripts/check_expense_duplicates_structure.py for AST equivalence.
Three previously reproduced series_id fixture failures remain separately excluded.


### Employee expense access and participants (phase 5d)

`invoice_tool/employee_expenses/access.py` owns beneficiary fallback/options/form selection,
expense query visibility, access/deletion guards, required expense loading and participant
notifications. Eight legacy app signatures retain runtime dependency resolution. Internal
active-recipient eligibility, historical disabled recipient retention, original actor identity,
manager/admin/finance visibility, draft/returned deletion and notification deduplication remain
unchanged. Route approval, attachment-specific access, order guards and messaging stay in place.

Checks: test_expense_access_module_contract.py compares 240 role/user/beneficiary/status
combinations, signatures and beneficiary form errors with 898a05f; checks notifications and
denied expense loading before order loading. Existing on-behalf tests cover HTTP integration.
At this commit run scripts/check_expense_access_structure.py for equivalence.
Three previously reproduced series_id fixture failures remain separately excluded.


### Employee expense form endpoints and save tokens (phase 5e)

`invoice_tool/employee_expenses/expense_write_routes.py` registers new_expense, edit_expense
and expense_detail; expense_write_services.py owns claim/finish expense save-token persistence.
Original URLs, endpoints, methods, login wrappers and app function aliases remain intact.
App callbacks and expense status labels resolve on every request, preserving runtime patches.
Form validation, draft/submitted transitions, token replay handling, transaction rollback,
attachment/duplicate/sync auxiliary failure handling and notifications remain unchanged.
Approval, return/cancel/delete, payment and attachment-specific routes stay in app.py.

Checks: test_expense_write_module_contract.py verifies endpoint/method identity, repeated new/edit
tokens, validation rollback and unexpected save rollback followed by retry with the same token.
At this phase's commit use scripts/check_expense_write_structure.py for equivalence to 11878e0.
Three previously reproduced series_id fixture failures remain separately excluded.


### Employee expense duplicate review and return endpoints (phase 5f)

`invoice_tool/employee_expenses/expense_review_routes.py` registers duplicate review and
return_expense with original POST URLs, endpoint names, app aliases and login guards.
Dependencies resolve through explicit callbacks. Duplicate review role/status validation,
return-state/reason checks, reviewer metadata, participant notifications and audit remain unchanged.
Approval/cancel/delete remain in app.py because their legacy payment calls consume app globals;
that interface must be bounded before moving them. Payment semantics are unchanged.

Checks: test_expense_review_module_contract.py verifies endpoint identity, return reason/state,
actor/beneficiary retention, notifications and denied employee action; previous duplicate tests
cover all review statuses and roles. At this commit use scripts/check_expense_review_structure.py.
Three previously reproduced series_id fixture failures remain separately excluded.


### Employee expense attachment access endpoints (phase 5g)

`invoice_tool/employee_expenses/expense_attachment_routes.py` registers attachment preview,
download, interpretation reads and immediate interpretation requests. Original URLs, endpoint
names, methods, login guards and app aliases remain intact. Expense access precedes file/model
access; interpretation JSON, force behavior, commit and 200/502 status mapping remain unchanged.
Attachment roots resolve per request. AI review, attachment deletion/transfer and payment routes
stay in their current locations. No model settings or external clients move.

Checks: test_expense_attachment_module_contract.py validates endpoint identity, participant
access, denied unrelated access, original downloaded bytes/filename, missing IDs and mocked
interpretation success/failure. At this commit use scripts/check_expense_attachment_structure.py.
Three previously reproduced series_id fixture failures remain separately excluded.


### Employee expense attachment deletion endpoint (phase 5h)

`invoice_tool/employee_expenses/expense_attachment_delete_routes.py` registers the original
POST delete_expense_attachment endpoint with its login wrapper and app alias. Creator/manager
permission, draft/returned restriction, missing-file tolerance, bidirectional duplicate-reference
cleanup, database rollback and redirects remain unchanged. File removal still precedes database
cleanup; this structural change does not introduce filesystem transactionality.
Checks: test_expense_attachment_delete_module_contract.py plus existing deletion regressions.
At this commit use scripts/check_expense_attachment_delete_structure.py for equivalence to 5314a6b.
Three previously reproduced series_id fixture failures remain separately excluded.


### Employee expense AI review endpoint (phase 5i)

`invoice_tool/employee_expenses/expense_ai_review_routes.py` registers expense_ai_review.
Original URL, GET/POST methods, login/action permission guards and app alias remain intact.
GET reads existing records only; POST forces the existing review task. Attachment roots resolve
per request. JSON, missing-record defaults, 200/502 mapping and commits remain unchanged.
Model execution, prompts, worker scheduling, approval and payment remain in their existing modules.
Checks: test_expense_ai_review_module_contract.py and existing test_expense_ai_review.py.
At this commit use scripts/check_expense_ai_review_structure.py for equivalence to 0d3df49.
Three previously reproduced series_id fixture failures remain separately excluded.


### Employee expense attachment transfer endpoint (phase 5j)

`invoice_tool/employee_expenses/expense_transfer_routes.py` registers transfer_expense_attachment.
URL, POST method, login/access checks and app alias remain intact. Existing transfer detection,
draft/returned settlement eligibility, unique-race success handling, copy/audit/commit/rollback
and history-replace JSON/redirect behavior remain unchanged. Exception classes are injected as
classes, not callbacks. File copying and settlement services stay in their existing modules.
Checks: test_expense_transfer_module_contract.py compares 36 branch/response combinations with
8443d0e and verifies HTTP denial/missing-file-record guards. Actual settlement copy integration
is not covered by these mock-boundary cases; legacy series_id fixtures require separate repair.
At this commit use scripts/check_expense_transfer_structure.py for equivalence.


### Employee expense approval and explicit payment bridge (phase 5k)

`invoice_tool/employee_expenses/expense_approval_routes.py` registers approve_expense.
expense_approval_services.py adapts the existing employee_finance.ensure_expense_payment_order
using exactly db, now, log_action, lock_number_allocation and worker_tax_status_lookup.
The route calls an explicit single-ID callback instead of passing module globals. No new module
imports app or calls globals(). Existing payment generation, component snapshot/reopen/idempotency
and rollback semantics remain in employee_finance. Cancel/delete payment calls stay in app.py.

Checks: test_expense_approval_module_contract.py verifies transitive API keys, runtime dependency
identity, repeat approval and snapshot rollback; test_expense_tax_components.py tests actual
payment components in invoice_test. test_employee_finance.py now locates the moved approval.
At this commit scripts/check_expense_approval_structure.py normalizes only the documented
payment-call adapter difference before comparing the route body to d571d83.


### Employee expense cancellation and explicit payment bridge (phase 5l)

`invoice_tool/employee_expenses/expense_cancel_routes.py` registers cancel_expense.
expense_cancel_services.py passes exactly db, now and log_action to the existing payment
cancellation function. The documented single-ID callback replaces app globals; other route
behavior is unchanged. Reviewed/pending/paid/reconciled payment guards, reason requirements,
cancellation metadata, logs and commits retain their prior behavior. Delete/reset flows stay put.
Important existing behavior: a draft payment cancellation exception is logged and the expense
cancellation still commits. This can leave a cancelled expense with a draft payment; not fixed
in structural work. Tests explicitly preserve and document it for a separate business fix.
Checks: test_expense_cancel_module_contract.py compares 84 state/payment/reason cases and
confirms both old/new routes fail on missing invoice_test cancel_reason; successful route-level
integration remains unverified. Prior payment/tax snapshot tests cover the payment service.
At this commit scripts/check_expense_cancel_structure.py normalizes only the payment adapter
call difference before comparing the route to df2f6e5.


### Employee expense deletion and reused payment cancellation adapter (phase 5m)

`invoice_tool/employee_expenses/expense_delete_routes.py` registers delete_expense and reuses
expense_cancel_services.py to pass db, now and log_action to existing payment cancellation.
Original URL/login/app alias, delete eligibility, paid/reconciled protection, unpaid payment
children cleanup, bidirectional duplicate links, rollback and redirects remain unchanged.
The existing attachment-directory removal still precedes database work; rollback cannot restore
files. This risk is explicitly tested and recorded, not fixed during structural extraction.
Checks: test_expense_delete_module_contract.py plus existing deletion/payment/tax regressions.
At this commit scripts/check_expense_delete_structure.py normalizes only the payment adapter
call difference before comparing to 22db058. Batch/reset payment calls remain in their domain.


### Employee expense query and processing queue (phase 5n)

`invoice_tool/employee_expenses/expense_query_routes.py` registers expense_query and
expense_processing. GET URLs, endpoint names, login/internal guards and app aliases remain.
Filters, access clauses, beneficiary identity, item-level query rows, pending/paid rules and
queue priority retain their original SQL. Status/payout label dictionaries resolve per request.
Query total follows filtered item rows; queue totals cover all accessible approved expenses
independently of visible row filters. The process_expense_action write endpoint stays in app.py.
Checks: test_expense_query_module_contract.py validates multi-item totals, filters, priority,
participant visibility and options without legacy settlement fixtures.
At this commit use scripts/check_expense_query_structure.py for equivalence to b0f360b.


### Employee expense workflow reset endpoint (phase 5o)

`invoice_tool/employee_expenses/expense_reset_routes.py` registers process_expense_action.
It reuses the existing db/now/log_action payment cancellation adapter, replacing only the
app-globals call. Original POST URL/login/internal/admin guards, reset_workflow-only action,
invalid/missing expense handling, paid-payment rejection, reset metadata, logs and commit remain.
Repeated reset may log/reset expense metadata again but does not create a second payment cancel
event. Components remain unchanged. test_employee_finance.py now locates this moved endpoint.
Checks: test_expense_reset_module_contract.py uses actual ER records to verify guards, repeated
reset and snapshot preservation. At this commit scripts/check_expense_reset_structure.py
normalizes only the explicit payment adapter call difference against 6510dc8.


### Invoice forms (ten-round series 1/10)

`invoice_tool/invoices/forms.py` owns invoice_items_from_form, posted_client_id, posted_service_order_id.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice tokens (ten-round series 2/10)

`invoice_tool/invoices/tokens.py` owns claim_invoice_save_token, finish_invoice_save_token.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice numbering (ten-round series 3/10)

`invoice_tool/invoices/numbering.py` owns next_invoice_number, invoice_number_exists.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice storage (ten-round series 4/10)

`invoice_tool/invoices/storage.py` owns invoice_attachment_dir, attachment_file_path, invoice_attachment_path, get_invoice_attachments, save_uploaded_attachment.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice attachment_names (ten-round series 5/10)

`invoice_tool/invoices/attachment_names.py` owns normalized_attachment_filename, existing_attachment_names, duplicate_attachment_names, validate_attachment_uploads, unique_invoice_attachment_filename.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice copying (ten-round series 6/10)

`invoice_tool/invoices/copying.py` owns copy_file_to_invoice_attachment, copy_customer_reimbursement_attachments_to_invoice, copy_mileage_proofs_to_invoice.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice exports (ten-round series 7/10)

`invoice_tool/invoices/exports.py` owns build_invoice_zip, render_invoice_export_html.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice email_attachments (ten-round series 8/10)

`invoice_tool/invoices/email_attachments.py` owns customer_reimbursement_email_attachments, unique_email_attachment_filename, invoice_email_attachments.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice pdf (ten-round series 9/10)

`invoice_tool/invoices/pdf.py` owns render_invoice_pdf.
Legacy app signatures delegate with explicit dependencies resolved per call. Bodies and business
policies remain identical to eca0599; no route is moved. Cumulative equivalence check:
`scripts/check_invoice_extraction_structure.py`; tests: `test_invoice_extraction_contract.py`.


### Invoice extraction ten-round checkpoint

Nine helper extractions and one cross-domain verification round complete. app.py: 13567 lines.
Full scope and limitations: `docs/invoice-ten-rounds-report.md`. This checkpoint does not
claim complete repository test coverage or resolution of previously documented business risks.


### Invoice attachment_read routes (1/10)

`invoice_tool/invoices/attachment_read_routes.py` owns download_attachment, preview_attachment.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice attachment_write routes (2/10)

`invoice_tool/invoices/attachment_write_routes.py` owns upload_attachment, delete_attachment.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice export routes (3/10)

`invoice_tool/invoices/export_routes.py` owns export_invoice, export_invoice_pdf.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice detail routes (4/10)

`invoice_tool/invoices/detail_routes.py` owns invoice_detail.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice send routes (5/10)

`invoice_tool/invoices/send_routes.py` owns send_invoice.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice admin_status routes (6/10)

`invoice_tool/invoices/admin_status_routes.py` owns admin_update_invoice_status.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice payment routes (7/10)

`invoice_tool/invoices/payment_routes.py` owns mark_invoice_paid, unmark_invoice_paid.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice void routes (8/10)

`invoice_tool/invoices/void_routes.py` owns void_invoice.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice delete routes (9/10)

`invoice_tool/invoices/delete_routes.py` owns delete_invoice.
URL/methods and business bodies match 27f12df; runtime dependencies are explicit.
Checks: `scripts/check_invoice_routes_structure.py`, `test_invoice_routes_contract.py`.


### Invoice route ten-round checkpoint

Nine bounded route groups and one guarded cross-domain checkpoint complete.
See `docs/invoice-routes-ten-rounds-report.md` for results and coverage limits.


### CodeBuddy round 1/20: invoice_access

`invoice_tool/invoices/access.py` owns require_invoice_access.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 2/20: invoice_totals

`invoice_tool/invoices/totals.py` owns invoice_totals.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 3/20: invoice_recognition

`invoice_tool/invoices/recognition.py` owns recognize_invoice_if_accounting_enabled.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 4/20: invoice_uploads

`invoice_tool/invoices/uploads.py` owns uploaded_attachments_from_request.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 5/20: invoice_loading

`invoice_tool/invoices/loading.py` owns load_invoice, invoice_service_order.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 6/20: invoice_create

`invoice_tool/invoices/create.py` owns create_invoice_from_form.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 7/20: invoice_update

`invoice_tool/invoices/update.py` owns update_invoice_from_form.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 8/20: invoice_mail

`invoice_tool/invoices/mail.py` owns send_invoice_email.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 9/20: named_attachment

`invoice_tool/shared_attachments/named.py` owns save_named_attachment.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 10/20: company_attachments

`invoice_tool/shared_attachments/company.py` owns get_company_attachments.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 11/20: delivery_record

`invoice_tool/email_delivery/record.py` owns record_email_delivery.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 12/20: delivery_summary

`invoice_tool/email_delivery/summary.py` owns email_delivery_summary.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 13/20: image_detection

`invoice_tool/shared_attachments/images.py` owns is_image_attachment, is_customer_reimbursement_image_attachment.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 14/20: xlsx_columns

`invoice_tool/report_exports/columns.py` owns xlsx_column_name.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 15/20: xlsx_single

`invoice_tool/report_exports/single_sheet.py` owns build_simple_xlsx.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 16/20: xlsx_multi

`invoice_tool/report_exports/multi_sheet.py` owns build_multi_sheet_xlsx.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 17/20: iso_date

`invoice_tool/report_exports/dates.py` owns parsed_iso_date.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 18/20: labor_entries

`invoice_tool/report_exports/labor.py` owns labor_report_entries.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 19/20: smtp_transport

`invoice_tool/email_delivery/transport.py` owns send_email.
Legacy app signatures resolve explicit dependencies per call; bodies match d3ea404.
Verified by `test_codebuddy_twenty_contract.py`; independent review/testing by Codex.


### CodeBuddy round 1/20: dashboard_metrics

`invoice_tool/dashboard/metrics.py` owns get_metrics.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 2/20: dashboard_monthly

`invoice_tool/dashboard/charts.py` owns monthly_chart.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 3/20: dashboard_projects

`invoice_tool/dashboard/projects.py` owns available_projects_for_access.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 4/20: dashboard_options

`invoice_tool/dashboard/projects.py` owns dashboard_project_options.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 5/20: report_options

`invoice_tool/dashboard/projects.py` owns report_project_options.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 6/20: project_chart

`invoice_tool/dashboard/charts.py` owns monthly_project_chart.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 7/20: paid_chart

`invoice_tool/dashboard/charts.py` owns monthly_paid_chart.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 8/20: setting_write

`invoice_tool/app_settings/storage.py` owns get_setting, set_setting.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 9/20: setting_float

`invoice_tool/app_settings/numbers.py` owns setting_float.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 10/20: setting_int

`invoice_tool/app_settings/numbers.py` owns setting_int.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.


### CodeBuddy round 11/20: positive_int

`invoice_tool/app_settings/numbers.py` owns positive_int.
Legacy app signatures resolve explicit dependencies per call; bodies match d14b2af.
Verified by `test_codebuddy_dashboard_contract.py`; independent review/testing by Codex.
