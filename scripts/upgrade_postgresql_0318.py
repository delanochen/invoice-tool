"""Apply the optional work-order date migration to invoice_test only."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,choices=['invoice_test'])
    parser.add_argument('--container',default='invoice-tool-postgres')
    args=parser.parse_args()
    command=['docker','exec','-i',args.container,'psql','-U','invoice_owner','-d',args.database,'-v','ON_ERROR_STOP=1']
    identity=subprocess.run(command+['-At','-c','select current_database()'],text=True,capture_output=True,check=True)
    if identity.stdout.strip()!='invoice_test':
        raise SystemExit('Refusing non-test database')
    sql=(Path(__file__).resolve().parents[1]/'migrations/postgresql/0318-service-order-expected-settlement-date.sql').read_text(encoding='utf-8')
    subprocess.run(command,input=sql,text=True,check=True)
    print('0318 applied to verified invoice_test')


if __name__=='__main__':
    main()
