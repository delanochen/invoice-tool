"""Apply customer-reimbursement expense ignore mechanism (0306)."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0306-customer-reimbursement-expense-ignores.sql"
MARKER = "postgresql_0306_customer_reimbursement_expense_ignores"
TABLE = "customer_reimbursement_expense_ignores"


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
        ("select count(*) from information_schema.tables "
         "where table_schema='public' and table_name='%s'" % TABLE, 1),
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
        print('{"status":"already_upgraded","migration":"0306"}')
        return 0
    result = run(args.container, args.database,
                 input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1500:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0306 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0306"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
