# Database architecture

PostgreSQL is the only supported database backend for production, staging,
development, and tests. The application fails closed when `DATABASE_URL` is
missing or is not a `postgresql://` / `postgres://` URL. Web processes and both
AI workers use the same `PostgreSQLConnection` boundary.

## Schema and migrations

Runtime processes never create or alter tables. The baseline lives in
`migrations/postgresql/0252-compat.sql`; additive migrations are versioned in
`migrations/postgresql/` and applied by `scripts/upgrade_postgresql*.py` before
an application image is deployed. Startup verifies `invoice_schema_version`
and required tables/columns before serving requests.

The connection boundary still translates a reviewed subset of historical SQL
syntax (`?` bind markers, selected date helpers, reviewed upsert forms, and a
few string operators). This is active PostgreSQL application compatibility,
not an optional SQLite backend. Removing it requires converting every caller
and proving equivalent behavior; unsupported DDL and PRAGMA statements fail
closed.

## Deployment, health, backup, and restore

Deployment always combines `docker-compose.yml` with
`deploy/docker-compose.postgresql.yml`. PostgreSQL is attached only to the
internal Docker network and does not publish port 5432 on the host. The runtime
role is `invoice_app`, has no superuser/schema-create privileges, and cannot
modify migration metadata.

`scripts/check_postgresql_runtime.py` performs the read-only database identity,
role, grant, schema-version, and foreign-key checks used by deployment and the
security baseline. `scripts/backup_postgresql.py` creates exclusive
custom-format `pg_dump` archives and verifies that `pg_restore` can read their
table of contents. A backup is considered recoverable only after restoring it
into a separate PostgreSQL validation database and running the same checks.

## Tests

Database tests use only the dedicated PostgreSQL database `invoice_test` on the
shared test server. `tests_pg.py` rejects every other database name before it
connects. The `invoice_test` role is dedicated to that database; tests reset
only its business tables and restore a PostgreSQL seed baseline between cases.
Production databases must never be used for tests, fixtures, migration
rehearsals, or restore validation.

## Historical artifacts

The PostgreSQL cutover runbooks, one-time import/repair utilities, and
`CHANGELOG.md` retain references to SQLite because they document the actual
migration history. They are not imported or invoked by the current runtime,
deployment, health, backup, or test paths.
