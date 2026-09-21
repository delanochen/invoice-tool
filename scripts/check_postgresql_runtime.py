"""Read-only verification of a running invoice PostgreSQL configuration."""
import argparse
import json
import subprocess


CHECK = r'''
import json,os,sys
from urllib.parse import urlsplit
import psycopg
from database import SCHEMA_VERSION
expected,host=sys.argv[1:]
url=os.environ.get('DATABASE_URL','')
parts=urlsplit(url)
if parts.scheme not in ('postgresql','postgres') or parts.hostname!=host:
    raise SystemExit('Unexpected PostgreSQL endpoint')
if parts.path != '/'+expected or parts.username != 'invoice_app':
    raise SystemExit('Unexpected PostgreSQL database or runtime role')
with psycopg.connect(url,connect_timeout=10,options='-c default_transaction_read_only=on -c statement_timeout=10000') as c:
    name,role=c.execute('select current_database(),current_user').fetchone()
    if (name,role)!=(expected,'invoice_app'): raise SystemExit('Runtime database identity mismatch')
    if c.execute('select rolsuper or rolcreatedb or rolcreaterole from pg_roles where rolname=current_user').fetchone()[0]:
        raise SystemExit('Runtime role has excessive privileges')
    if c.execute("select has_schema_privilege(current_user,'public','CREATE')").fetchone()[0]:
        raise SystemExit('Runtime role may create schema objects')
    version=c.execute('select version from invoice_schema_version where singleton=1').fetchone()
    if not version or version[0]!=SCHEMA_VERSION: raise SystemExit('Schema version mismatch')
    for table in ('invoice_schema_version','invoice_sqlite_columns','data_repair_archive'):
        if c.execute("select has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE')",(table,)).fetchone()[0]:
            raise SystemExit('Protected table is writable by runtime role')
    if c.execute("select count(*) from pg_constraint where connamespace='public'::regnamespace and contype='f' and not convalidated").fetchone()[0]:
        raise SystemExit('Unvalidated foreign keys')
print(json.dumps({'database':name,'role':role,'schema':version[0],'read_only_check':'passed'}))
'''


def check(container, database, host):
    if container not in ('invoice-tool', 'invoice-pg-browser'):
        raise ValueError('Unexpected application container')
    result = subprocess.run(['docker', 'exec', container, 'python', '-c', CHECK,
                             database, host], capture_output=True, text=True)
    if result.returncode:
        # Driver exceptions can contain endpoint details; do not echo them.
        raise RuntimeError('PostgreSQL runtime check failed; verify endpoint, role, schema and grants')
    return json.loads(result.stdout)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='invoice-tool')
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', default='postgres')
    args = parser.parse_args()
    print(json.dumps(check(args.container, args.database, args.host)))
