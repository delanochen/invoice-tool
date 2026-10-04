"""Persist a previously generated accounting-base check report."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--saved-by", type=int)
    args = parser.parse_args()
    report = json.loads(args.source.read_text(encoding="utf-8"))
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute(
            "insert into accounting_base_check_report"
            "(checked_at,schema_ok,report,saved_by) values(%s,%s,%s,%s)",
            (report["checked_at"], bool(report["schema_ok"]),
             json.dumps(report, ensure_ascii=False), args.saved_by),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
