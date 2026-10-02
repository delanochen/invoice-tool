"""Apply the PostgreSQL users.english_name migration (v0.1.xxx).

幂等：列已存在且已打标记则直接跳过；不改任何既有行数据。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0296-users-english-name.sql"

MARKER = "postgresql_0296_users_english_name"

# 「列不存在」= 未升级；列存在 = 已升级（配合 settings 标记双保险）。
CHECK_SQL = """
select count(*) from information_schema.columns
where table_schema = 'public' and table_name = 'users' and column_name = 'english_name'
"""


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
        (run(container, database, CHECK_SQL), 1),
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
        print('{"status":"already_upgraded","migration":"0296"}')
        return 0
    result = run(args.container, args.database,
                 input_text=MIGRATION.read_text(encoding="utf-8-sig"))
    if result.returncode:
        print(result.stderr.strip()[-1000:], file=sys.stderr)
        return 1
    if not schema_ready(args.container, args.database):
        print("0296 verification failed", file=sys.stderr)
        return 1
    print('{"status":"upgraded","migration":"0296"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
