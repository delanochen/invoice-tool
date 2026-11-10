"""Apply the invoice_orders migration to invoice_test (or an explicit rehearsal copy).

生产库不在允许列表内：回填演练只允许跑在 invoice_test，或者生产库的一致性副本
（invoice_phase1_rehearsal，由 pg_dump/pg_restore 生成），避免误改生产。
"""
import argparse
from pathlib import Path
import subprocess

ALLOWED_DATABASES = ('invoice_test', 'invoice_phase1_rehearsal')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, choices=list(ALLOWED_DATABASES))
    parser.add_argument('--container', default='invoice-tool-postgres')
    args = parser.parse_args()
    command = ['docker', 'exec', '-i', args.container, 'psql', '-U', 'invoice_owner', '-d', args.database, '-v', 'ON_ERROR_STOP=1']
    identity = subprocess.run(command + ['-At', '-c', 'select current_database()'], text=True, capture_output=True, check=True)
    if identity.stdout.strip() != args.database:
        raise SystemExit('Refusing unexpected database: ' + identity.stdout.strip())
    sql = (Path(__file__).resolve().parents[1] / 'migrations/postgresql/0319-invoice-orders.sql').read_text(encoding='utf-8')
    subprocess.run(command, input=sql, text=True, check=True)
    print('0319 applied to verified ' + args.database)


if __name__ == '__main__':
    main()
