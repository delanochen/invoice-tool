"""Apply the PostgreSQL-only v0.1.350 employee payment batch migration.

合并发放：同一员工的多张已审核付款单合成一张支票发放（批次号 PB-YYMM-NNNN），
一张支票在银行流水里是一笔支出，对账时整批核销。
幂等：重复执行不会重建已存在的表或列。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0290-payment-batches.sql"

MARKER = "postgresql_0290_payment_batches"

TABLE_SQL = (
    "select count(*) from information_schema.tables "
    "where table_schema='public' and table_name='employee_payment_batches'"
)

COLUMN_SQL = (
    "select count(*) from information_schema.columns where table_schema='public' and ("
    " (table_name='employee_payment_orders' and column_name='batch_id') or"
    " (table_name='bank_transactions' and column_name='matched_batch_id')"
    ")"
)


def run(container, database, sql=None, input_text=None):
    command = ["docker", "exec", *(["-i"] if input_text is not None else []), container,
               "psql", "-U", "invoice_owner", "-d", database, "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        command += ["-At", "-c", sql]
    if input_text is not None:
        command.append("--single-transaction")
    return subprocess.run(command, input=input_text, capture_output=True, text=True, encoding="utf-8")


def schema_ready(container, database):
    table = run(container, database, TABLE_SQL)
    columns = run(container, database, COLUMN_SQL)
    marker = run(container, database,
                 "select count(*) from settings where key='%s' and value='complete'" % MARKER)
    if table.returncode or columns.returncode or marker.returncode:
        return False
    return (
        table.stdout.strip() == "1"
        and columns.stdout.strip() == "2"
        and marker.stdout.strip() == "1"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if not MIGRATION.is_file():
        print(f"missing migration: {MIGRATION}", file=sys.stderr)
        return 1
    if schema_ready(args.container, args.database):
        print('{"status":"already_upgraded","migration":"0290"}')
        return 0
    result = run(args.container, args.database,
                 input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0290 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0290"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
