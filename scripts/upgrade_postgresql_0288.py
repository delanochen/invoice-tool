"""Apply the PostgreSQL-only v0.1.341 employee-grade rate nullable migration.

员工等级费率列从 DEFAULT 0 NOT NULL 放宽为可空：以后「没填费率」落 NULL，
「明确填 0」落 0，利润表据此区分「没维护」与「维护成 0」。
只放宽列属性，不改写既有数据。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0288-employee-grade-rate-nullable.sql"

# 需要放宽为可空的费率列
RATE_COLUMNS = (
    "standard_hourly_rate",
    "transport_hourly_rate",
    "overtime_hourly_rate",
    "holiday_hourly_rate",
    "car_hourly_rate",
    "car_mileage_rate",
    "rental_driving_hourly_rate",
)


def run(container, database, sql=None, input_text=None):
    command = ["docker", "exec", *(["-i"] if input_text is not None else []), container,
               "psql", "-U", "invoice_owner", "-d", database, "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        command += ["-At", "-c", sql]
    if input_text is not None:
        command.append("--single-transaction")
    return subprocess.run(command, input=input_text, capture_output=True, text=True, encoding="utf-8")


def nullable_count_sql():
    columns = ",".join("'%s'" % name for name in RATE_COLUMNS)
    return (
        "select count(*) from information_schema.columns where table_schema='public' "
        "and table_name='employee_grades' and column_name in (%s) and is_nullable='YES'" % columns
    )


def schema_ready(container, database):
    result = run(container, database,
                 "select (%s) || '|' || "
                 "(select count(*) from settings where key="
                 "'postgresql_0288_employee_grade_rate_nullable' and value='complete')"
                 % nullable_count_sql())
    return result.returncode == 0 and result.stdout.strip() == "%d|1" % len(RATE_COLUMNS)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if not MIGRATION.is_file():
        print(f"missing migration: {MIGRATION}", file=sys.stderr)
        return 1
    if schema_ready(args.container, args.database):
        print('{"status":"already_upgraded","migration":"0288"}')
        return 0
    result = run(args.container, args.database, input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0288 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0288"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
