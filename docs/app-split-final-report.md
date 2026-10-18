# app.py structural migration: final audit

Business implementation was migrated without changing business rules, SQL, permission behavior, route endpoints, methods, transactions or financial calculation policy. The first 73 rounds were implemented by CodeBuddy; from round74 onward implementation, review, tests and commits were performed by Codex with explicit user authorization.

Final app.py: 4554 lines, 410 top-level function definitions (primarily legacy forwarders). Original architecture baseline: 19728 lines. Line count is not the completion criterion.

## Completed scopes

- settlement: 10 separately committed scopes; milestone 495 passed, 4 deselected, 2 warnings, 559 subtests passed in 279.66s (0:04:39).
- routes: 14 separately committed scopes; milestone 496 passed, 4 deselected, 2 warnings, 607 subtests passed in 278.66s (0:04:38).
- helpers: 17 separately committed scopes; milestone 502 passed, 4 deselected, 2 warnings, 747 subtests passed in 270.62s (0:04:30).
- tail: 2 separately committed scopes; milestone 504 passed, 4 deselected, 2 warnings, 761 subtests passed in 273.87s (0:04:33).
- residual: 16 separately committed scopes; milestone 510 passed, 4 deselected, 2 warnings, 858 subtests passed in 279.64s (0:04:39).
- catalogs: 7 separately committed scopes; milestone 512 passed, 4 deselected, 2 warnings, 879 subtests passed in 273.54s (0:04:33).
- menu-catalog: 1 separately committed scopes; milestone 515 passed, 4 deselected, 2 warnings, 881 subtests passed in 276.19s (0:04:36).

## Final boundary verification

19 passed, 872 subtests passed in 8.86s

App retains imports, environment/configuration bindings, Flask/service assembly, explicit current-app dependency resolvers, legacy signatures and hook registrations. Business route bodies, calculations, queries, policies, formatters, lifecycle implementations, rate-limited geocoder transport, error pages and large business catalogs live in their domains. New domain modules do not import root app. Legacy forwarders and service construction retain live application bindings and monkeypatch compatibility. Mutable catalog objects are independently constructed for each app load.

Retained implementation boundaries: db (request connection/cache), close_db (request cleanup), now (clock adapter), _new_formal_save_service, _ai_daily_report_service, _make_photo_classification_service and _attachment_manifest_service (service/provider/callback composition). These are application assembly, not unmigrated business handlers. Small environment and application configuration bindings remain at the composition root.

## Limits

All database execution was guarded to invoice_test. Broad milestones are the previously vetted PostgreSQL test scope, not the entire repository suite. Four pre-existing series_id fixture cases remain explicitly excluded; two existing Pillow warnings remain. No schema change, production database access, merge or deployment. Previously recorded expense cancellation/file rollback, existing schema fixture gaps and XLSX inline-string concerns were not repaired as part of structural migration. Customer contract mileage allowance and company fuel reimbursement remain allowed together.
