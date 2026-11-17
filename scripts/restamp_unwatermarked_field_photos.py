"""Redraw field photos that were saved without a watermark.

Uses the unwatermarked original when original_relative_path is set, otherwise
the displayed file. Refuses invoice_production. Do not point this at production
unless a human has reviewed the photo list and passed --i-understand-production.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_tool.service_reports.field_capture import stamp_group_watermark  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--photos-root', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--i-understand-production', action='store_true')
    args = parser.parse_args()
    if args.database == 'invoice_production' and not args.i_understand_production:
        raise SystemExit('Refusing invoice_production. This script was not run.')
    if args.database != 'invoice_test' and args.database != 'invoice_production':
        raise SystemExit('Database must be invoice_test or, explicitly, invoice_production.')
    print('dry-run' if args.dry_run else 'would update', args.database, 'under', args.photos_root)
    print('Import stamp_group_watermark from field_capture and update relative_path files in place.')
    print('No database connection is opened by this guard. Wire a reviewed query before use.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
