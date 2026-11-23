"""Apply the project quotation advance payment migration."""
import argparse
import sys
from pathlib import Path

from upgrade_postgresql_0324 import ALLOWED_DATABASES, run


MIGRATION = Path(__file__).resolve().parents[1] / "migrations/postgresql/0325-quotation-advance-payment.sql"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", choices=sorted(ALLOWED_DATABASES), required=True)
    parser.add_argument("--container", default="invoice-tool-postgres")
    args = parser.parse_args()
    identity = run(args.container, args.database, sql="select current_database()")
    if identity.returncode or identity.stdout.strip() != args.database:
        print("database identity verification failed", file=sys.stderr)
        return 1
    result = run(args.container, args.database, input_text=MIGRATION.read_text(encoding="utf-8"))
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
