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
