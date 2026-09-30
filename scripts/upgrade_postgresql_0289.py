"""Apply the PostgreSQL-only v0.1.343 employee payment-number prefix migration.

员工付款单编号统一为「一类一前缀」：工资 SL-YYMM-NNNN、员工报销 ER-YYMM-NNNN。
存量单据按 (created_at, id) 同前缀同月份内从 0001 重新连续编号。
幂等：重复执行不会改变已符合规则的单号。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0289-payment-number-prefixes.sql"

MARKER = "postgresql_0289_payment_number_prefixes"

# 所有「前缀-YYMM-NNNN」格式的单号，都不该再有 EP- 前缀，且类型与前缀必须匹配。
MIGRATED_SQL = (
    "select payment_number from employee_payment_orders "
    "where payment_number not like 'SL-%' and payment_number not like 'ER-%'"
)

MISMATCH_SQL = (
    "select payment_number from employee_payment_orders "
    "where payment_type = 'salary' and payment_number not like 'SL-%' "
    "   or payment_type <> 'salary' and payment_number not like 'ER-%'"
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
    unmigrated = run(container, database, MIGRATED_SQL)
    mismatched = run(container, database, MISMATCH_SQL)
    marker = run(container, database,
                 "select count(*) from settings where key='%s' and value='complete'" % MARKER)
    if unmigrated.returncode or mismatched.returncode or marker.returncode:
        return False
    return (
        unmigrated.stdout.strip() == ""
        and mismatched.stdout.strip() == ""
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
        print('{"status":"already_upgraded","migration":"0289"}')
        return 0
    result = run(args.container, args.database,
                 input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0289 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0289"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
