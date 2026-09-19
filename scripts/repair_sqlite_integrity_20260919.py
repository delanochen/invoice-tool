"""Audited, bounded repair. Default: rehearse on an in-memory snapshot.

Explicit --apply requires --backup-dir. Never imports the application, alters
financial values, moves files, or infers timezone from the machine timezone.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


REPAIR = 'sqlite-integrity-20260919-v1'
ARCHIVE = 'data_repair_archive'
# Exact rows reviewed against the production snapshot. Unknown orphans abort.
EXPECTED = {
    'invoice_items': ({20,21,22,35,36,39,40,41,42,48,49}, 'invoice_id', 'invoices', 'CASCADE'),
    'invoice_attachments': ({5,9,10,15}, 'invoice_id', 'invoices', 'CASCADE'),
    'user_attachments': ({1}, 'user_id', 'users', 'CASCADE'),
    'audit_logs': ({53}, 'user_id', 'users', 'SET NULL'),
    'service_report_save_tokens': ({13,14,22}, 'report_id', 'service_reports', 'CASCADE'),
    'invoice_save_tokens': ({3,5,6,9}, 'invoice_id', 'invoices', 'CASCADE'),
    'expense_save_tokens': ({15,16,17,18,19}, 'expense_id', 'expenses', 'CASCADE'),
    'user_service_orders': ({1,2}, 'user_id', 'users', 'CASCADE'),
}
PARENTS = {
    'invoice_items': {20:3,21:3,22:3,35:5,36:5,39:7,40:7,41:8,42:8,48:11,49:11},
    'invoice_attachments': {5:3,9:7,10:8,15:11},
    'user_attachments': {1:5}, 'audit_logs': {53:5},
    'service_report_save_tokens': {13:10,14:10,22:10},
    'invoice_save_tokens': {3:5,5:7,6:8,9:11},
    'expense_save_tokens': {15:13,16:14,17:15,18:16,19:17},
    'user_service_orders': {1:5,2:5},
}


def q(value):
    return '"' + value.replace('"', '""') + '"'


def json_value(value):
    if isinstance(value, bytes):
        return {'base64': base64.b64encode(value).decode('ascii')}
    raise TypeError(type(value).__name__)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=json_value)


def table_rows(c):
    tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    return {t: {r['_repair_rowid']: {k: r[k] for k in r.keys() if k != '_repair_rowid'}
                for r in c.execute('select rowid as _repair_rowid,* from '+q(t))}
            for t in tables if t != ARCHIVE}


def plan(c):
    changes = []
    for table, rid, parent, fkid in c.execute('pragma foreign_key_check').fetchall():
        if table not in EXPECTED:
            raise ValueError('Unreviewed orphan table: '+table)
        ids, col, expected_parent, action = EXPECTED[table]
        fk = next(r for r in c.execute('pragma foreign_key_list('+q(table)+')') if r['id'] == fkid)
        if rid not in ids or parent != expected_parent or fk['from'] != col or fk['to'] != 'id' or fk['on_delete'] != action:
            raise ValueError('Unreviewed orphan relationship: '+table)
        if c.execute('select '+q(col)+' from '+q(table)+' where rowid=?',(rid,)).fetchone()[0] != PARENTS[table][rid]:
            raise ValueError('Orphan parent differs from reviewed snapshot: '+table)
        changes.append((table, rid, None if action == 'CASCADE' else {col: None},
                        'Missing parent; apply declared ON DELETE '+action+'; preserve original in archive'))

    marker = c.execute("select value from settings where key='settlement_expense_links_migration_v1'").fetchone()
    for row in c.execute('select rowid as _rid,* from customer_reimbursement_expense_links'):
        value = row['selected_at']
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            if not marker or value != marker[0] or row['selected_by'] is not None:
                raise ValueError('Unknown selected_at timezone source')
            changes.append(('customer_reimbursement_expense_links', row['_rid'],
                            {'selected_at': parsed.replace(tzinfo=timezone.utc).isoformat()},
                            "Historical migration uses SQLite datetime('now'), UTC"))
    row = c.execute("select rowid as _rid,* from countries where code='CA'").fetchone()
    if row and datetime.fromisoformat(row['created_at']).tzinfo is None:
        evidence = c.execute('select summary,created_at from audit_logs where id=178').fetchone()
        if (row['created_at'] != '2026-07-03 03:51:30' or not evidence
                or 'countries' not in evidence['summary'] or "'CA'" not in evidence['summary']
                or "datetime('now'" not in evidence['summary']
                or datetime.fromisoformat(evidence['created_at']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') != row['created_at']):
            raise ValueError('Country timestamp audit evidence mismatch')
        changes.append(('countries', row['_rid'], {'created_at': '2026-07-03T03:51:30+00:00'},
                        'Audit 178 records SQLite UTC datetime(now) insertion'))
    for row in c.execute("select rowid as _rid,* from field_photos where watermark_at='' "):
        captured = datetime.fromisoformat(row['captured_at'])
        if (row['id'] not in range(1,17) or row['source'] != 'camera'
                or row['watermark_source'] != 'system' or captured.tzinfo is None
                or row['capture_date'] != '2026-09-04'
                or captured.astimezone(ZoneInfo(row['timezone_name'])).date().isoformat() != row['capture_date']):
            raise ValueError('Unknown legacy watermark source')
        changes.append(('field_photos', row['_rid'], {'watermark_at': row['captured_at']},
                        'Pre-ea79320 system watermark used captured_at; no file/date changes'))
    return changes


def repair(c):
    changes = plan(c)
    before = table_rows(c)
    expected = {t: dict(rows) for t, rows in before.items()}
    if changes:
        c.execute('''create table if not exists data_repair_archive (
            id integer primary key, repair_key text not null, table_name text not null,
            source_rowid integer not null, original_json text not null,
            replacement_json text, reason text not null, repaired_at text not null,
            unique(repair_key,table_name,source_rowid))''')
    summary = {}
    for table, rid, replacement, reason in changes:
        original = before[table][rid]
        c.execute('''insert into data_repair_archive
            (repair_key,table_name,source_rowid,original_json,replacement_json,reason,repaired_at)
            values (?,?,?,?,?,?,?)''',
            (REPAIR, table, rid, encoded(original), encoded(replacement), reason,
             datetime.now(timezone.utc).isoformat()))
        if replacement is None:
            c.execute('delete from '+q(table)+' where rowid=?', (rid,))
            del expected[table][rid]
        else:
            c.execute('update '+q(table)+' set '+','.join(q(k)+'=?' for k in replacement)+' where rowid=?',
                      (*replacement.values(), rid))
            expected[table][rid] = {**original, **replacement}
        key = table + (':archive_remove' if replacement is None else ':update')
        summary[key] = summary.get(key, 0) + 1
    if table_rows(c) != expected:
        raise ValueError('Unexpected data changes outside approved rows/columns')
    if c.execute('pragma foreign_key_check').fetchall():
        raise ValueError('Foreign key check failed')
    if c.execute('pragma integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Integrity check failed')
    if plan(c):
        raise ValueError('Repair is not idempotent')
    return {'changes': summary, 'changed_rows': len(changes), 'foreign_key_violations': 0,
            'integrity': 'ok', 'all_other_values_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir')
    args = parser.parse_args()
    path = Path(args.database).resolve(strict=True)
    if args.apply and not args.backup_dir:
        parser.error('--apply requires --backup-dir')
    if not args.apply:
        source = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
        c = sqlite3.connect(':memory:')
        source.backup(c)
        source.close()
    else:
        c = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('pragma foreign_keys=on')
    if c.execute('pragma foreign_keys').fetchone()[0] != 1:
        raise RuntimeError('Foreign key enforcement unavailable')
    c.execute('begin immediate')
    backup_path = None
    try:
        plan(c)  # Fail closed before creating backup or changing any rows.
        if args.apply:
            folder = Path(args.backup_dir).resolve() / (REPAIR+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            folder.mkdir(mode=0o700, parents=True, exist_ok=False)
            backup_path = folder/'before.sqlite3'
            # Separate reader while this connection holds the write reservation:
            # no other writer can change the snapshot between backup and repair.
            with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as source:
                with sqlite3.connect(str(backup_path)) as target:
                    source.backup(target)
                    if target.execute('pragma integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Backup integrity check failed')
            backup_path.chmod(0o600)
        result = repair(c)
        if args.apply:
            c.commit()
        else:
            c.rollback()
        result.update(mode='applied' if args.apply else 'rehearsal', backup=str(backup_path) if backup_path else None)
        if backup_path:
            result['backup_sha256'] = hashlib.sha256(backup_path.read_bytes()).hexdigest()
            (backup_path.parent/'result.json').write_text(encoded(result), encoding='utf-8')
        print(encoded(result))
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


if __name__ == '__main__':
    main()
