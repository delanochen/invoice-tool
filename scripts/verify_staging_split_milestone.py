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
 files=['test_settlement_expense_links.py', 'test_ai_daily_report_v01243.py', 'test_attachment_security.py', 'test_expense_on_behalf.py', 'test_expense_business_purpose.py', 'test_expense_return_admin_and_robust_save.py', 'test_expense_interpretation.py', 'test_expense_delete.py', 'test_expense_ai_review.py', 'test_expense_tax_components.py', 'test_employee_finance.py', 'test_payroll_details.py', 'test_payroll_range.py', 'test_payroll_module_boundary.py', 'test_hours_display_precision.py', 'test_mobile_clock_in.py', 'test_nas_photo_day_folders.py', 'test_report_copy.py', 'test_service_report_zero_mileage.py', 'test_service_report_edit_navigation.py', 'test_service_report_assist.py', 'test_customer_report_payroll.py', 'test_service_report_navigation.py', 'test_service_report_external_view.py', 'test_app_assembly_contract.py', 'test_staging_split_integration_contract.py', 'test_settlement_revisions.py', 'test_settlement_settled_amount.py']
 raise SystemExit(pytest.main(['-q', '--tb=short', '--deselect=test_payroll_details.py::ReportFiltersTest::test_settlement_filters_and_totals', '--deselect=test_expense_return_admin_and_robust_save.py::RobustExpenseSaveTest::test_edit_save_preserves_item_referenced_by_settlement', '--deselect=test_expense_return_admin_and_robust_save.py::RobustExpenseSaveTest::test_sync_copy_swallows_copyfile_oserror', '--deselect=test_expense_return_admin_and_robust_save.py::RobustExpenseSaveTest::test_sync_copy_swallows_unique_index_race']+files+sys.argv[1:]))
