"""Apply the PostgreSQL-only v0.1.286 payment-order consolidation migration."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0286-payment-order-consolidation.sql"


def run(container, database, sql=None, input_text=None):
    command = ["docker", "exec", *(["-i"] if input_text is not None else []), container,
               "psql", "-U", "invoice_owner", "-d", database, "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        command += ["-At", "-c", sql]
    if input_text is not None:
        command.append("--single-transaction")
    return subprocess.run(command, input=input_text, capture_output=True, text=True, encoding="utf-8")


def schema_ready(container, database):
    result = run(
        container,
        database,
        "select (select count(*) from pg_constraint "
        "where conrelid='employee_payment_orders'::regclass "
        "and conname='employee_payment_orders_payment_type_check' "
        "and pg_get_constraintdef(oid) like '%salary%' "
        "and pg_get_constraintdef(oid) not like '%system_payroll%') || '|' || "
        "(select count(*) from settings where key='postgresql_0286_payment_order_consolidation' "
        "and value='complete')",
    )
    return result.returncode == 0 and result.stdout.strip() == "1|1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if not MIGRATION.is_file():
        print(f"missing migration: {MIGRATION}", file=sys.stderr); return 1
    if schema_ready(args.container, args.database):
        print('{"status":"already_upgraded","migration":"0286"}'); return 0
    result = run(args.container, args.database, input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr); return 1
    if not schema_ready(args.container, args.database):
        print("0286 verification failed", file=sys.stderr); return 1
    print('{"status":"upgraded","migration":"0286"}'); return 0


if __name__ == "__main__":
    raise SystemExit(main())
