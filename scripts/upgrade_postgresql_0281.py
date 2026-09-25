"""Apply the v0.1.281 additive PostgreSQL upgrade: expense attachment interpretations.

Fail-closed runner, mirroring scripts/upgrade_postgresql.py for the 0274 table:
requires the '0252-compat-v2' marker (this migration is additive and does not
bump it), applies the migration in ONE psql single transaction with
ON_ERROR_STOP, then verifies the table. Idempotent.

Usage (deploy host):
    python3 scripts/upgrade_postgresql_0281.py --database invoice_tool_production
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0281-expense-attachment-interpretations.sql"
CONTAINER = "invoice-tool-postgres"
ROLE = "invoice_owner"
EXPECTED_VERSION = "0252-compat-v2"
TABLE = "expense_attachment_interpretations"


def psql(container, database, sql):
    command = ["docker", "exec", container, "psql", "-U", ROLE, "-d", database,
               "-v", "ON_ERROR_STOP=1", "-At", "-c", sql]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode and result.stderr:
        print("psql failed: " + result.stderr.strip().splitlines()[-1][:200], file=sys.stderr, flush=True)
    return result.returncode, result.stdout.strip()


def table_exists(container, database):
    code, exists = psql(container, database,
                        "select coalesce((select 1 from information_schema.tables "
                        "where table_schema='public' and table_name='" + TABLE + "'), 0)")
    if code:
        sys.exit(1)
    return exists == "1"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default=CONTAINER)
    args = parser.parse_args()

    if not MIGRATION.is_file():
        print("error: migration file missing: " + str(MIGRATION), file=sys.stderr)
        return 1
    code, version = psql(args.container, args.database,
                         "select coalesce((select version from invoice_schema_version where singleton=1), '')")
    if code:
        return 1
    if version != EXPECTED_VERSION:
        print("error: unexpected schema version marker: " + repr(version), file=sys.stderr)
        return 1
    if table_exists(args.container, args.database):
        print('{"status": "already_upgraded", "schema": "0252-compat-v2"}')
        return 0

    print("applying migration " + MIGRATION.name + " ...", file=sys.stderr)
    with MIGRATION.open("rb") as handle:
        command = ["docker", "exec", "-i", args.container, "psql", "-U", ROLE,
                   "-d", args.database, "-v", "ON_ERROR_STOP=1", "-At", "--single-transaction"]
        result = subprocess.run(command, stdin=handle, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        print("error: migration failed, transaction rolled back.", file=sys.stderr)
        return 1
    if not table_exists(args.container, args.database):
        print("error: post-migration verification failed.", file=sys.stderr)
        return 1
    print('{"status": "upgraded", "schema": "0252-compat-v2"}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
