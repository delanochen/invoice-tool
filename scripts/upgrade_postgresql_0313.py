"""Apply customer-reimbursement revision / series schema (0313)."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0313-customer-reimbursement-revisions.sql"
MARKER = "postgresql_0313_customer_reimbursement_revisions"
TABLE = "customer_reimbursements"
COLUMNS = ("series_id", "version_no", "supersedes_id", "is_current",
           "revision_reason", "superseded_at", "sources_frozen")


def run(container, database, sql=None, input_text=None):
    command = ["docker", "exec", *(["-i"] if input_text is not None else []),
               container, "psql", "-U", "invoice_owner", "-d", database,
               "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        command += ["-At", "-c", sql]
    if input_text is not None:
        command.append("--single-transaction")
    return subprocess.run(command, input=input_text, capture_output=True, text=True,
                          encoding="utf-8")


def schema_ready(container, database):
    checks = [
        ("select count(*) from information_schema.columns "
         "where table_schema='public' and table_name='%s' and column_name='%s'" % (TABLE, column), 1)
        for column in COLUMNS
    ]
    checks += [
        ("select count(*) from information_schema.tables "
         "where table_schema='public' and table_name='customer_reimbursement_series'", 1),
        ("select count(*) from settings where key='%s' and value='complete'" % MARKER, 1),
    ]
    for sql, expected in checks:
        result = run(container, database, sql=sql)
        if result.returncode or result.stdout.strip() != str(expected):
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if not MIGRATION.is_file():
        print(f"missing migration: {MIGRATION}", file=sys.stderr)
        return 1
    if schema_ready(args.container, args.database):
        print('{"status":"already_upgraded","migration":"0313"}')
        return 0
    result = run(args.container, args.database,
                 input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1500:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0313 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0313"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
