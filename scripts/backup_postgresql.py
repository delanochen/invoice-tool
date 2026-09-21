"""Create an exclusive private archive from a named local PostgreSQL container.

Archive verification checks pg_restore can read its TOC, not a full restore.
Use a separate restore rehearsal to prove recovery. Never prints credentials.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def backup(container, database, output):
    if container not in {'invoice-pg-rehearsal', 'invoice-tool-postgres'}:
        raise ValueError('Only explicitly named invoice PostgreSQL containers are allowed')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', database):
        raise ValueError('Invalid database name')
    output = Path(output)
    if not output.is_absolute() or not output.parent.is_dir():
        raise ValueError('Output must have an existing absolute parent directory')
    if output.suffix != '.dump':
        raise ValueError('Output must end in .dump')
    command = ['docker', 'exec', container]
    identity = subprocess.check_output(command + [
        'psql', '-U', 'invoice_owner', '-d', database, '-XAt', '-v',
        'ON_ERROR_STOP=1', '-c', 'select current_database()'], text=True).strip()
    if identity != database:
        raise ValueError('Database identity mismatch')
    # O_EXCL prevents overwriting a previous backup, including through a symlink.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as archive:
        subprocess.run(command + ['pg_dump', '-U', 'invoice_owner', '-d', database,
                                  '-Fc', '--no-owner', '--no-acl'], stdout=archive, check=True)
        archive.flush()
        os.fsync(archive.fileno())
    with output.open('rb') as archive:
        subprocess.run(['docker', 'exec', '-i', container, 'pg_restore', '--list'],
                       stdin=archive, stdout=subprocess.DEVNULL, check=True)
    digest = hashlib.sha256()
    with output.open('rb') as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'database': database, 'archive': str(output), 'bytes': output.stat().st_size,
            'sha256': digest.hexdigest(), 'archive_readable': True,
            'full_restore_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(backup(args.container, args.database, args.output)))
