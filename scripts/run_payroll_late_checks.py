"""Run focused PostgreSQL regressions with existing unittest dependencies."""
import importlib,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tests_pg
tests_pg.activate()
tests_pg.ensure_schema()
modules=[importlib.import_module(name) for name in (
    "tests.test_payroll_late_reports","test_payroll_tax_components",
    "test_payroll_cycle_correction","test_employee_finance","test_employee_payment_date_filter",
    "test_payment_batch_payout","test_payment_statement","tests.test_release_notes")]
suite=unittest.TestSuite()
classes=set()
for module in modules:
    for value in module.__dict__.values():
        if isinstance(value,type) and issubclass(value,unittest.TestCase) and value.__module__==module.__name__:
            classes.add(value)
for cls in classes:
    original=cls.setUpClass
    def setup(_cls,original=original):
        tests_pg.restore_for_test()
        original()
        _cls._payroll_test_snapshot=tests_pg.snapshot_all()
    cls.setUpClass=classmethod(setup)
    original_setup=cls.setUp
    def before(self,original_setup=original_setup):
        tests_pg.restore_for_test(getattr(type(self),"_payroll_test_snapshot",None))
        original_setup(self)
    cls.setUp=before
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
result=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
