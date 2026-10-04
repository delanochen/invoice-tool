"""Apply quotation revision and settlement-source migration 0299."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0299-quotation-pricing-revisions.sql"
MARKER = "postgresql_0299_quotation_pricing_revisions"


def run(container, database, sql=None, input_text=None):
    command = ["docker", "exec", *(["-i"] if input_text is not None else []), container,
               "psql", "-U", "invoice_owner", "-d", database, "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        command += ["-At", "-c", sql]
    if input_text is not None:
        command.append("--single-transaction")
    return subprocess.run(command, input=input_text, capture_output=True, text=True, encoding="utf-8")


def schema_ready(container, database):
    checks = [
        (run(container, database, "select count(*) from information_schema.tables where table_schema='public' and table_name='quotation_revisions'"), "1"),
        (run(container, database, "select count(*) from information_schema.columns where table_schema='public' and table_name='service_orders' and column_name='settlement_basis'"), "1"),
        (run(container, database, "select count(*) from information_schema.columns where table_schema='public' and table_name='customer_reimbursements' and column_name in ('settlement_basis','quotation_revision_id')"), "2"),
        (run(container, database, f"select count(*) from settings where key='{MARKER}' and value='complete'"), "1"),
    ]
    return all(result.returncode == 0 and result.stdout.strip() == expected for result, expected in checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if schema_ready(args.container, args.database):
        print('{"status":"already_upgraded","migration":"0299"}')
        return 0
    result = run(args.container, args.database, input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0299 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0299"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
