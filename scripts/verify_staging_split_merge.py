"""Run merge regression tests only against the verified invoice_test database."""
from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
if not sys.flags.utf8_mode:
 os.execv(sys.executable,[sys.executable,'-X','utf8',str(Path(__file__).resolve()),*sys.argv[1:]])
import tests_pg
tests_pg.activate()
original=tests_pg.connection
def guarded_connection():
 c=original()
 if c.execute('select current_database()').fetchone()[0]!='invoice_test':
  c.close();raise RuntimeError('Refusing non-invoice_test database')
 return c
tests_pg.connection=guarded_connection
with guarded_connection() as guard:
 print('Verified active database: invoice_test',flush=True)
 if not guard.execute('select pg_try_advisory_lock(733252099)').fetchone()[0]:raise RuntimeError('Another suite is running')
 import pytest
 files=['test_app_assembly_contract.py','test_settlement_revisions.py','test_settlement_settled_amount.py','test_settlement_expense_links.py','test_customer_reimbursement_mro.py','test_profitability_pass_through.py','test_settlement_panel_detail.py','test_payment_terms.py','test_hours_display_precision.py','test_attachment_security.py']
 raise SystemExit(pytest.main(['-q','--tb=short',*files,*sys.argv[1:]]))
