"""Apply the 0324 quotation catalog and invoice-map migration (idempotent)."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0324-quotation-catalog-map.sql"
ALLOWED_DATABASES = {"invoice_test", "invoice_production"}


def run(container, database, *, sql=None, input_text=None):
    command = ["docker", "exec", *(["-i"] if input_text is not None else []),
               container, "psql", "-U", "invoice_owner", "-d", database,
               "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        command += ["-At", "-c", sql]
    return subprocess.run(command, input=input_text, capture_output=True, text=True,
                          encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    if args.database not in ALLOWED_DATABASES:
        print(f"refusing unexpected database: {args.database}", file=sys.stderr)
        return 1
    identity = run(args.container, args.database, sql="select current_database()")
    if identity.returncode or identity.stdout.strip() != args.database:
        print("database identity verification failed", file=sys.stderr)
        return 1
    ready = run(args.container, args.database, sql=(
        "select case when "
        "(select count(*) from information_schema.tables "
        "where table_schema='public' and table_name='quotation_invoice_project_map') = 1 "
        "and (select count(*) from information_schema.columns "
        "where table_schema='public' and table_name='quotation_project_lines' and column_name='project_id') = 1 "
        "then 1 else 0 end"
    ))
    if ready.returncode:
        print(ready.stderr.strip(), file=sys.stderr)
        return 1
    if ready.stdout.strip() == "1":
        print('{"status":"already_upgraded","migration":"0324"}')
        return 0
    if not MIGRATION.is_file():
        print(f"missing migration: {MIGRATION}", file=sys.stderr)
        return 1
    result = run(args.container, args.database, input_text=MIGRATION.read_text(encoding="utf-8"))
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    print('{"status":"applied","migration":"0324"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
