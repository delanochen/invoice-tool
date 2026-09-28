# Agent instructions for this repository

## Shared test server and database

- A shared test environment is available on `192.168.7.202`.
- SSH was verified on 2026-09-27 with host name `debian`, port `22`, and user `root`.
- On this Windows machine, agents may connect non-interactively with:

  ```powershell
  ssh -i "$env:USERPROFILE\.ssh\invoice_test_agent" -o BatchMode=yes root@192.168.7.202
  ```

- The verified PostgreSQL test database is named **`invoice_test`**. Do not use the misspelling `invoice-tesy`.
- PostgreSQL runs in the Docker container `invoice-tool-postgres`; it is not exposed on a public host port. Use the existing application/container network or an explicitly scoped SSH-based workflow when a task requires database access.

## Mandatory safety rules

- Use only `invoice_test` for development, integration tests, migrations, fixtures, and disposable test data.
- Never query, migrate, truncate, reset, seed, or otherwise modify `invoice_production`, `invoice_production_restore_validation`, or any other non-test database.
- Treat the SSH account as privileged. Remote changes must be directly required by the user's task; inspect first and keep every command narrowly scoped.
- Test data may be changed or reset. Never place production data, customer secrets, credentials, tokens, or other sensitive information in the test database.
- Never print, copy, commit, or document private-key contents or database passwords. The private key remains outside the repository at `%USERPROFILE%\.ssh\invoice_test_agent`.
- Before a destructive test-database operation, explicitly verify that the active database name is exactly `invoice_test`.
- Do not change firewall rules, SSH configuration, Docker deployment settings, or production services unless the user explicitly requests that separate operation.

## Application architecture navigation

- Read `docs/app-architecture.md` before starting application work.
- Do not read all of `app.py` by default. Use the architecture map to identify
  the target business domain, search for the endpoint/function/class, and read
  only the necessary context and its direct dependencies.
- New business features should not normally be added to `app.py`; place them in
  the corresponding business module.
- Split `app.py` gradually. Migrate one explicitly bounded scope at a time, and
  do not change business logic while performing a structural migration.
- Every extraction phase must run focused tests. Run the complete test suite at
  important milestones, and commit every phase separately.
- Tests may connect only to PostgreSQL `invoice_test`. They must never connect
  to `invoice_production` or any other non-`invoice_test` database.
