"""Apply quotation travel-expense schema (0316)."""
import argparse, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/'migrations'/'postgresql'/'0316-quotation-travel-expenses.sql'
def run(container,database,sql=None,input_text=None):
    cmd=['docker','exec',*(['-i'] if input_text is not None else []),container,'psql','-U','invoice_owner','-d',database,'-v','ON_ERROR_STOP=1']
    if sql is not None: cmd += ['-At','-c',sql]
    if input_text is not None: cmd.append('--single-transaction')
    return subprocess.run(cmd,input=input_text,capture_output=True,text=True,encoding='utf-8')
def ready(c,d):
    r=run(c,d,sql="select count(*) from information_schema.columns where table_schema='public' and table_name='quotations' and column_name='travel_expenses'")
    return not r.returncode and r.stdout.strip()=='1'
def main():
    p=argparse.ArgumentParser(); p.add_argument('--database',required=True); p.add_argument('--container',default='invoice-tool-postgres'); a=p.parse_args()
    if ready(a.container,a.database): print('{"status":"already_upgraded","migration":"0316"}'); return 0
    r=run(a.container,a.database,input_text=MIGRATION.read_text(encoding='utf-8-sig'))
    if r.returncode: print(r.stderr.strip()[-1500:],file=sys.stderr); return 1
    if not ready(a.container,a.database): print('0316 verification failed',file=sys.stderr); return 1
    print('{"status":"upgraded","migration":"0316"}'); return 0
if __name__=='__main__': raise SystemExit(main())
