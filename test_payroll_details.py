import unittest
from datetime import date
from unittest.mock import patch
from test_report_query_filters import ReportFiltersTest


class PayrollDetailsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = ReportFiltersTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.m, self.f = self.fixture.m, self.fixture.f
        with self.m.app.app_context():
            db = self.m.db()
            grade = db.execute("""insert into employee_grades
                (grade_name,base_salary,meal_daily_amount,standard_hourly_rate,transport_hourly_rate,
                 overtime_hourly_rate,holiday_hourly_rate,created_at)
                values ('Detail test',100,25,20,10,30,40,'now')""").lastrowid
            db.execute('update users set employee_grade_id=?', (grade,))
            db.execute('update service_reports set total_service_hours=8, report_writer_id=?', (self.f.people['Beneficiary'],))
            db.execute('insert into service_report_workers (report_id,user_id,travel_mode,travel_hours,driving_miles) values (?,?,\'self_drive\',2,20)',
                       (self.fixture.reports[1], self.f.people['Submitter']))
            db.commit()

    def test_details_reconcile_and_daily_meal_not_duplicated(self):
        with self.m.app.app_context():
            args = (date(2026,9,1), date(2026,9,30), date(2026,10,14))
            summary = self.m.payroll_rows_for_range(*args)
            detail = self.m.payroll_rows_for_range(*args, detail=True)
        for key, value in summary['totals'].items():
            self.assertAlmostEqual(value, detail['totals'][key], msg=key)
        rows = [r for r in detail['rows'] if r['worker_id'] == self.f.people['Submitter']]
        self.assertEqual(sum(r['meal_allowance'] for r in rows), 25)
        self.assertEqual(sum(r['base_salary'] for r in rows), 100)
        self.assertEqual(len([r for r in rows if r['service_order_id']]), 2)

    def test_filters_scope_and_render(self):
        query = {'date_from':'2026-09-01','date_to':'2026-09-30'}
        page = self.f.http.get('/reports/payroll-details', query_string=query)
        self.assertEqual(page.status_code, 200)
        self.assertIn('薪酬明细报表', page.text)
        self.assertIn('report-export.js', page.text)
        with patch.object(self.m,'render_template',return_value='ok') as render:
            self.f.http.get('/reports/payroll-details',query_string={**query,'order_id':str(self.fixture.second),'worker_id':str(self.f.people['Submitter'])})
        rows = render.call_args.kwargs['rows']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['service_order_id'],self.fixture.second)
        self.f.login('Submitter')
        with patch.object(self.m,'render_template',return_value='ok') as render:
            response=self.f.http.get('/reports/payroll-details',query_string={**query,'worker_id':str(self.f.people['Unrelated'])})
        self.assertEqual(response.status_code,200)
        self.assertTrue(all(r['worker_id']==self.f.people['Submitter'] for r in render.call_args.kwargs['rows']))
        self.assertEqual(self.f.http.get('/reports/payroll-details?date_from=bad').status_code,400)


if __name__ == '__main__':
    unittest.main()
