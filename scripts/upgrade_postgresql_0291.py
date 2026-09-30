"""Apply the PostgreSQL-only v0.1.352 tax classification migration.

第一阶段：只创建税务分类所需的数据结构（4 张新表 + 3 张表共 8 个新列），
不改任何金额计算 / 报销 / 往来账 / 付款批次 / 对账 / 状态机逻辑。

幂等：重复执行不会重建已存在的表或列；组件默认配置用
ON CONFLICT DO NOTHING，已在库里的配置行不会被覆盖。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0291-tax-classification.sql"

MARKER = "postgresql_0291_tax_classification"

NEW_TABLES = (
    "worker_tax_status_history",
    "payroll_component_tax_config",
    "employee_payment_components",
    "employee_payment_tax_reviews",
)

# (table_name, column_name)
NEW_COLUMNS = (
    ("employee_payment_orders", "taxable_compensation_total"),
    ("employee_payment_orders", "accountable_reimbursement_total"),
    ("employee_payment_orders", "tax_review_required_total"),
    ("expenses", "business_purpose"),
    ("expenses", "substantiated"),
    ("expenses", "tax_category"),
    ("expense_items", "tax_category"),
    ("expense_items", "substantiated"),
)

TABLE_SQL = (
    "select count(*) from information_schema.tables "
    "where table_schema='public' and table_name in (%s)"
    % ",".join("'%s'" % name for name in NEW_TABLES)
)

COLUMN_SQL = (
    "select count(*) from information_schema.columns where table_schema='public' and ("
    + " or ".join(
        "(table_name='%s' and column_name='%s')" % pair for pair in NEW_COLUMNS
    )
    + ")"
)

BASE_COMPONENT_CODES = (
    "standard_pay",
    "overtime_pay",
    "holiday_pay",
    "following_allowance",
    "rental_driving_allowance",
    "self_drive_allowance",
    "report_writing_fee",
    "base_salary",
    "meal_allowance",
)

SEED_SQL = (
    "select count(distinct component_code) from payroll_component_tax_config "
    "where component_code in (%s)"
    % ",".join("'%s'" % code for code in BASE_COMPONENT_CODES)
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
    checks = [
        (run(container, database, TABLE_SQL), len(NEW_TABLES)),
        (run(container, database, COLUMN_SQL), len(NEW_COLUMNS)),
        (run(container, database, SEED_SQL), len(BASE_COMPONENT_CODES)),
        (run(container, database,
             "select count(*) from settings where key='%s' and value='complete'" % MARKER), 1),
    ]
    if any(item[0].returncode for item in checks):
        return False
    return all(item[0].stdout.strip() == str(item[1]) for item in checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if not MIGRATION.is_file():
        print(f"missing migration: {MIGRATION}", file=sys.stderr)
        return 1
    if schema_ready(args.container, args.database):
        print('{"status":"already_upgraded","migration":"0291"}')
        return 0
    result = run(args.container, args.database,
                 input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0291 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0291"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
