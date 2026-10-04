"""Apply the disabled accounting-base schema migration (0298)."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0298-accounting-base.sql"
MARKER = "postgresql_0298_accounting_base"
TABLES = (
    "accounts", "accounting_periods", "posting_events", "vouchers",
    "voucher_entries", "voucher_source_links", "voucher_settlement_lines",
    "voucher_attachment_links", "accounting_opening_lines",
    "accounting_opening_cutover", "posting_audit", "customer_receipts",
    "receipt_allocations", "customer_prepayments",
    "accounting_base_check_report",
)
TRIGGERS = (
    "voucher_entries_protect_posted", "voucher_source_links_protect_posted",
    "voucher_attachment_links_protect_posted",
    "voucher_settlement_lines_protect_posted", "vouchers_mutation_guard",
    "voucher_entries_balance_check", "vouchers_balance_check",
)


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
    table_names = ",".join("'%s'" % item for item in TABLES)
    trigger_names = ",".join("'%s'" % item for item in TRIGGERS)
    checks = [
        ("select count(*) from information_schema.tables where table_schema='public' "
         f"and table_name in ({table_names})", len(TABLES)),
        ("select count(*) from information_schema.columns where table_schema='public' "
         "and table_name='employee_advance_applications' and column_name in "
         "('posting_event_id','voucher_id','occurrence_no','legacy_anchor')", 4),
        ("select count(*) from settings where key='%s' and value='complete'" % MARKER, 1),
        ("select count(*) from settings where key='accounting_base_enabled' "
         "and value in ('0','1')", 1),
        ("select count(*) from pg_trigger where not tgisinternal "
         f"and tgname in ({trigger_names})", len(TRIGGERS)),
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
        print('{"status":"already_upgraded","migration":"0298"}')
        return 0
    result = run(args.container, args.database,
                 input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0298 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0298"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
