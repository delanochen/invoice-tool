"""Read-only structural check for the accounting base."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_TABLES = {
    "accounts", "accounting_periods", "posting_events", "vouchers",
    "voucher_entries", "voucher_source_links", "voucher_settlement_lines",
    "voucher_attachment_links", "accounting_opening_lines",
    "accounting_opening_cutover", "posting_audit", "customer_receipts",
    "receipt_allocations", "customer_prepayments",
}
REQUIRED_TRIGGERS = {
    "voucher_entries_protect_posted", "voucher_source_links_protect_posted",
    "voucher_attachment_links_protect_posted",
    "voucher_settlement_lines_protect_posted", "vouchers_mutation_guard",
    "voucher_entries_balance_check", "vouchers_balance_check",
}


def inspect(connection):
    connection.execute("begin read only")
    read_only = connection.execute("show transaction_read_only").fetchone()[0]
    tables = {row[0] for row in connection.execute(
        "select table_name from information_schema.tables "
        "where table_schema='public' and table_name = any(%s)",
        (list(REQUIRED_TABLES),),
    ).fetchall()}
    triggers = {row[0] for row in connection.execute(
        "select tgname from pg_trigger where not tgisinternal and tgname = any(%s)",
        (list(REQUIRED_TRIGGERS),),
    ).fetchall()}
    setting = connection.execute(
        "select value from settings where key='accounting_base_enabled'"
    ).fetchone()
    connection.rollback()
    missing_tables = sorted(REQUIRED_TABLES - tables)
    missing_triggers = sorted(REQUIRED_TRIGGERS - triggers)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "transaction_read_only": read_only == "on",
        "enabled": bool(setting and setting[0] == "1"),
        "schema_ok": read_only == "on" and not missing_tables and not missing_triggers,
        "missing_tables": missing_tables,
        "missing_triggers": missing_triggers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=False) as connection:
        report = inspect(connection)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["schema_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
