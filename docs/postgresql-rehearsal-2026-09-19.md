# PostgreSQL compatibility and isolated migration rehearsal

## Scope and decision

Branch: `codex/postgresql-compatibility`, based on production v0.1.252
(`3280bef1f1fd6cedba4911a25f9bfe9d1d667232`). Production remains SQLite.
This is a tested compatibility candidate, **not authorization or readiness to switch production**.
No production schema, attachment, application image or database configuration was changed.

**2026-09-20 update:** the historical 16 failing cases have been reconciled with
the approved business rules. The corrected SQLite suite passed (1,053 tests;
10 PostgreSQL-specific skips). Two additional PostgreSQL HTTP business chains
also passed in a new disposable acceptance clone. See the follow-up below;
the earlier results are retained as the original rehearsal evidence.

SQLite remains the default when DATABASE_URL is absent. PostgreSQL requires a
separately provisioned database with schema version `0252-compat-v2`. Startup
verifies the version and refuses mismatches; it does not migrate or seed PostgreSQL.

## Implemented compatibility boundary

- psycopg 3.2.10 ClientCursor provides safely bound parameters, including existing
  `? IS NULL` expressions whose PostgreSQL server-side parameter type is ambiguous.
- Explicit RETURNING identities, SQLite-like row access, transaction rollback,
  per-statement savepoints, reviewed upserts, date functions and aggregate queries.
- All first writes and document-number allocation take one transaction-scoped
  advisory lock. This deliberately retains serialized writes in phase one;
  it is not a throughput optimization.
- Historical money columns retain double precision and dates retain text in this
  phase to avoid silently changing calculations or date-only semantics. Native
  NUMERIC/DATE/TIMESTAMPTZ conversion requires its own reviewed migration.
- `audit_logs.entity_id` is explicitly text: it contains both integer entity IDs
  and AI manifest UUIDs. This is a polymorphic identifier, not an integer FK.
- The SQL adapter is bounded, not a general SQLite emulator. Unknown PRAGMAs,
  schema DDL and unreviewed REPLACE targets fail. Database console schema/admin
  operations require native PostgreSQL administration; the console still has
  SQLite-oriented help text and is not a supported migration interface.
- Runtime uses a separate non-superuser role; schema creation/import uses the
  owner role. Grant SELECT only on migration metadata and repair archives.

## Rehearsal environment and evidence

Server directory `/srv/invoice-tool-pg-rehearsal`, independent PostgreSQL 17
container `invoice-pg-rehearsal`, internal Docker network `invoice-pg-isolated`,
no published ports. Test application cannot send external mail/API requests.
Attachment directories and shared photos are independent copies, not hardlinks.
Credentials are random, private env files on the server and are not committed.

SQLite snapshot SHA256:
`bcc6108bafb8419712527309c8c23c303c020bc72411f4d61cedf07cf0b26876`.

| Check | Result |
|---|---|
| Fresh v2 import | 60 tables, 7,720 rows, all row hashes match |
| Foreign keys | 84 validated; no disabled triggers |
| Identity sequences | 51 created, initialized from max ID and SQLite sequence high-water mark |
| Business comparison | Invoice, settlement, MRO, person-days, profit, annual payroll summary/detail match |
| Registered HTTP routes | 155 return 200 on both backends |
| Attachment sample | 25 previews return 200, bytes match |
| Settlement Excel | 22 exports, cell values match |
| Invoice PDF | Sample extracted text matches |
| PostgreSQL targeted tests | 14 passed on final v2 schema |
| AI daily report Phase 9 | 51 passed, including concurrent exactly-once commit and crash compensation |
| SQLite branch full suite | 1,081 passed, 16 failed, 10 skipped |
| Original v0.1.252 full suite | 853 passed, same 16 failures, 260 setup errors |

The original-version setup errors include shared test state leaving `app.db`
as None. The 16 shared failures concern AI draft cancellation, legacy photo
upload/watermark/native picker expectations (including inherited repetitions),
ledger Excel and two payment-term cases. The suite is **not green**; matching
failure names is evidence against new regressions in these cases, not proof
that all production workflows are covered. Do not merge/cut over on this evidence alone.

The first Phase 9 run found audit UUIDs rejected by bigint and a test helper
hardcoded to SQLite. Both were corrected before the 51-test passing run.
A later check against the old v1 test database correctly refused startup;
after upgrading that isolated database, all 14 targeted tests passed.

## Backup and restoration

Fresh import database: `invoice_v2_rehearsal`.
Restoration target: `invoice_restore_rehearsal` (new, empty database).
`pg_dump -Fc --no-owner --no-acl`, followed by
`pg_restore --no-owner --no-acl --exit-on-error`, succeeded.

- Dump: 491,451 bytes, SHA256
  `85629e09f29a69d2ab20ed27c12c6d16c14b9efe9f374743185a0e06b197f575`.
- After restore: 60 tables / 7,720 rows match source hashes; 84 validated FKs;
  51 sequences present. Application role grants were reapplied separately.
- Restored database passed the same business, 155-route, 25-attachment and
  22-Excel comparisons.
- Observed import 0.326 s, dump 0.149 s, restore 1.296 s. These exclude snapshot,
  file copies, deployment and validation; **they are not a downtime estimate**.

Detailed artifacts remain private on the rehearsal server: `scratch/fresh-v2-import.json`,
`rehearsal-v2.dump`, `restore-summary.json`, `check-summary-v2.json`,
`scratch/differential-results.json` and regression logs. Detailed comparisons
contain business data and must not be published with the source code.

## Repeat the isolated import

1. Take a consistent SQLite backup, validate integrity and foreign keys, copy
   attachments independently. Never mount production files writable.
2. Build `deploy/Dockerfile.pg-rehearsal` from the reviewed v0.1.252 image under
   an independent tag. Use a PostgreSQL 17 container on an internal network.
3. Create a new empty database whose name ends in `_rehearsal`. Keep its owner
   URL in a mode-600 env file. Do not use production credentials.
4. In the runner with the source mounted read-only, execute:

   ```sh
   python scripts/migrate_postgresql.py --source /snapshot/source.sqlite3 \
     --target-name invoice_v2_rehearsal --result /scratch/import.json
   ```

   The tool requires DATABASE_URL, exact reviewed schema, intact source FKs,
   exact target identity and an empty public schema. It never drops/truncates
   tables. Import and constraint validation commit as one transaction.
5. Grant runtime SELECT/INSERT/UPDATE/DELETE and sequence USAGE/SELECT; revoke
   schema CREATE and metadata/archive writes. Run `scripts/pg_rehearsal_checks.py`
   using the application env file and independent copied files.
6. Clone a separate test database named `invoice_tests_rehearsal` for
   `test_postgresql_compatibility.py`; tests create disposable records and files.
   Run Phase 9 in another disposable clone. Do not point tests at pristine data.
7. Dump to a private file, restore to another new `_rehearsal` database, compare
   every source table through `scripts.migrate_postgresql.compare`, reapply
   runtime grants and repeat business checks.

## Before a production cutover

Resolve baseline regression failures and obtain clean, isolated test runs;
exercise real browser actions and production-representative concurrent load;
verify backups, disk/connection limits, operational monitoring and scheduled jobs.
Decide the separate native money/date-type roadmap. Prepare a final frozen-write
snapshot, final data/file reconciliation and timed cutover/rollback procedure.
After PostgreSQL accepts new writes, reverting the URL to an old SQLite snapshot
would lose those writes: rollback must account for them explicitly.

References: [psycopg cursors](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html),
[transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[PostgreSQL advisory locks](https://www.postgresql.org/docs/17/functions-admin.html),
[COPY](https://www.postgresql.org/docs/17/sql-copy.html).

## 2026-09-20 regression and business acceptance follow-up

No production business logic was changed in this follow-up. The 16 old failures
represented eight distinct cases, four collected three times through imported
TestCase classes. Corrections:

- Cancel draft: acquire the CSRF token through its actual endpoint, assert
  missing-token rejection, successful cancellation and retained draft row.
- Original-image upload: metadata-free synthetic JPEGs must first return 422
  with `needs_capture_date`, without a database insert. Successful upload and
  legacy-ID retry explicitly supply the user's selected date.
- Original watermark and picker tests: update stale source assertions for
  upload mode retaining original watermarks and empty queue hiding completion.
  These are source-level checks, not real-browser validation.
- Ledger Excel: assert separate customer/site headers by name instead of
  column indexes invalidated by additional columns.
- Payroll: following and rental-driver hourly pay belongs to transport pay;
  mileage car allowance is zero for these cases. Preserve hourly/rate checks.
- Import field-work fixtures as modules, preventing pytest from recollecting
  44 unrelated imported cases. No distinct test coverage was removed.

Results: relevant modules **98 passed**; full corrected SQLite collection
**1,053 passed, 10 skipped**, 6 dependency deprecation warnings, 237.97 seconds.
The full collection ran before the new two-case acceptance module was added;
that module is PostgreSQL-only and was tested separately. No tests were disabled
to conceal baseline failures, and no business permission/date/pay rules were relaxed.

`test_postgresql_acceptance.py` runs only when the parsed database name is
`invoice_acceptance_rehearsal` and the application's file root is under `/scratch`.
The clone was restored from the reviewed v2 dump, with a non-superuser application
role, protected metadata tables and the same internal network. Test fixtures use
unique names and remain only in the disposable clone for inspection.

Two passing HTTP chains (final run 1.43 seconds):

1. Create an employee expense with a JPEG line attachment; repeat the submission
   token and assert one expense; read the actual image endpoint; deny employee
   approval; approve as manager; create the prerequisite work report; select the
   approved expense line into settlement; save and reopen a manual lodging field;
   retain the source link; export Excel; verify copied evidence files exist.
2. Two clients synchronized with a barrier submit the same work-report token;
   both requests complete and exactly one report remains. Check rental-driver
   mileage does not become reimbursable self-drive mileage; payroll yields
   3 hours × 15 = 45 transport pay and zero mileage car allowance.

During development, acceptance fixtures were corrected to use the existing
`expenses.amount` field, actual attachment route, and required report/travel
inputs. These were test setup errors, not PostgreSQL business defects.

Remaining gates: real desktop/mobile browser interactions (JavaScript image
modal, tab navigation, file picker), sustained representative multi-user load,
final runtime operations and a written freeze/cutover/reconciliation/rollback
procedure. HTTP test-client assertions do not establish those browser or load
results. Production still uses SQLite; no main merge or production deployment.
