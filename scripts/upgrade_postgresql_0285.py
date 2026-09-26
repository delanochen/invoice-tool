"""Apply the PostgreSQL-only v0.1.285 employee finance/assets migration."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0285-employee-finance-assets.sql"
TABLES = (
    "bank_accounts", "employee_salary_agreements", "employee_advances", "employee_payment_orders",
    "payment_order_sources", "payment_order_events", "employee_advance_applications",
    "bank_transactions", "assets", "asset_events", "asset_photos",
)


def run(container, database, sql=None, input_text=None):
    command = ["docker", "exec", *( ["-i"] if input_text is not None else []), container,
               "psql", "-U", "invoice_owner", "-d", database, "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        command += ["-At", "-c", sql]
    if input_text is not None:
        command.append("--single-transaction")
    return subprocess.run(command, input=input_text, capture_output=True, text=True, encoding="utf-8")


def schema_ready(container, database):
    names = ",".join("'" + name + "'" for name in TABLES)
    result = run(
        container, database,
        "select (select count(*) from information_schema.tables where table_schema='public' "
        f"and table_name in ({names})) || '|' || "
        "(select count(*) from information_schema.columns where table_schema='public' "
        "and table_name='employee_advances' and column_name='attachment_stored_filename')",
    )
    return result.returncode == 0 and result.stdout.strip() == f"{len(TABLES)}|1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if not MIGRATION.is_file():
        print(f"missing migration: {MIGRATION}", file=sys.stderr); return 1
    if schema_ready(args.container, args.database):
        print('{"status":"already_upgraded","migration":"0285"}'); return 0
    result = run(args.container, args.database, input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr); return 1
    if not schema_ready(args.container, args.database):
        print("0285 verification failed", file=sys.stderr); return 1
    print('{"status":"upgraded","migration":"0285"}'); return 0


if __name__ == "__main__":
    raise SystemExit(main())
