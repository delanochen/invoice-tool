"""Explicit frozen-snapshot migration entrypoint, not an automatic cutover.

Requires an approved maintenance window with all writers stopped. The flag
attests to that operational step; this script cannot prove external writers are
stopped. It never freezes services, changes app configuration, or drops data.
The original rehearsal-only command retains its database-name restriction.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.migrate_postgresql import _migrate_snapshot


def validate(source, source_sha, url, target_host, target_name, confirm_target, writes_frozen):
    if not writes_frozen or confirm_target != target_name:
        raise ValueError('Writer freeze attestation and exact target confirmation are required')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', target_name):
        raise ValueError('Invalid target database name')
    if not re.fullmatch(r'[a-f0-9]{64}', source_sha):
        raise ValueError('Expected snapshot SHA-256 is required')
    parts = urlsplit(url)
    if (parts.scheme not in ('postgres', 'postgresql') or parts.query or parts.fragment
            or not target_host or parts.hostname != target_host
            or unquote(parts.path) != '/' + target_name
            or parts.username != 'invoice_owner' or parts.port not in (None, 5432)):
        raise ValueError('Migration URL does not match the explicit host, database and owner role')
    source = Path(source)
    if not source.is_absolute() or source.suffix != '.sqlite3' or source.is_symlink():
        raise ValueError('Use an absolute standalone .sqlite3 snapshot, not a live database')
    source = source.resolve(strict=True)
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('Snapshot has an active SQLite sidecar')
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha:
        raise ValueError('Snapshot SHA-256 does not match')
    return source


def run(args, url):
    source = validate(args.source, args.source_sha256, url, args.target_host,
                      args.target_name, args.confirm_target, args.writes_frozen)
    result_path = Path(args.result)
    if not result_path.is_absolute():
        raise ValueError('Result must be an absolute new file path')
    # Reserve output before any DB writes, preventing an accidental overwrite.
    descriptor = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        result = _migrate_snapshot(source, url, args.target_name,
                                   expected_sha=args.source_sha256, expected_user='invoice_owner')
        json.dump(result, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    return {k: v for k, v in result.items() if k != 'tables'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--target-host', required=True)
    parser.add_argument('--target-name', required=True)
    parser.add_argument('--confirm-target', required=True)
    parser.add_argument('--writes-frozen', action='store_true')
    parser.add_argument('--result', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args, os.environ['DATABASE_URL'])))
