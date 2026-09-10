import unittest
from datetime import date
from unittest.mock import patch
from test_expense_on_behalf import ExpenseOnBehalfTest

class PayrollRangeTest(unittest.TestCase):
    def setUp(self):
        self.f = ExpenseOnBehalfTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.m = self.f.app

    def test_manager_custom_range_and_worker(self):
        self.f.login('Manager')
        worker = str(self.f.people['Submitter'])
        with patch.object(self.m, 'payroll_rows_for_range', wraps=self.m.payroll_rows_for_range) as calculate:
            response = self.f.http.get('/reports/payroll', query_string=dict(period_start='2026-08-01', period_end='2026-09-30', worker_id=worker))
        self.assertEqual(response.status_code, 200)
        calculate.assert_called_once_with(date(2026,8,1), date(2026,9,30), date(2026,10,14), worker)
        self.assertIn('name="period_end"', response.text)
        self.assertIn('name="worker_id"', response.text)
        self.assertNotIn('两周结束日期', response.text)

    def test_employee_cannot_select_other_employee(self):
        self.f.login('Submitter')
        with patch.object(self.m, 'payroll_rows_for_range', wraps=self.m.payroll_rows_for_range) as calculate:
            response = self.f.http.get('/reports/payroll', query_string=dict(period_start='2026-09-01', period_end='2026-09-01', worker_id=str(self.f.people['Beneficiary'])))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calculate.call_args.args[3], str(self.f.people['Submitter']))
        self.assertNotIn('name="worker_id"', response.text)

    def test_reversed_or_invalid_dates_rejected(self):
        self.f.login('Manager')
        for end in ('2026-08-01', 'invalid'):
            self.assertEqual(self.f.http.get('/reports/payroll', query_string=dict(period_start='2026-09-01', period_end=end)).status_code,400)

if __name__ == '__main__':
    unittest.main()
