"""Apply the field repairs migration to invoice_test only.

仓库约定：开发与验证只在 invoice_test 进行，生产副本不参与演练。
"""
import argparse
from pathlib import Path
import subprocess

ALLOWED_DATABASES = ('invoice_test',)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, choices=list(ALLOWED_DATABASES))
    parser.add_argument('--container', default='invoice-tool-postgres')
    args = parser.parse_args()
    command = ['docker', 'exec', '-i', args.container, 'psql', '-U', 'invoice_owner', '-d', args.database, '-v', 'ON_ERROR_STOP=1']
    identity = subprocess.run(command + ['-At', '-c', 'select current_database()'], text=True, capture_output=True, check=True)
    if identity.stdout.strip() != args.database:
        raise SystemExit('Refusing unexpected database: ' + identity.stdout.strip())
    sql = (Path(__file__).resolve().parents[1] / 'migrations/postgresql/0320-field-repairs.sql').read_text(encoding='utf-8')
    subprocess.run(command, input=sql, text=True, check=True)
    print('0320 applied to verified ' + args.database)


if __name__ == '__main__':
    main()
