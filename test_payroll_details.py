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

    def test_transport_is_paid_once_and_self_drive_is_mileage(self):
        with self.m.app.app_context():
            db = self.m.db()
            db.execute("update employee_grades set car_allowance_method='hourly',car_mileage_rate=0.5,rental_driving_hourly_rate=15")
            db.execute("update service_report_workers set travel_mode='following',travel_hours=3 where user_id=? and report_id=?", (self.f.people['Submitter'],self.fixture.reports[0]))
            db.execute("update service_report_workers set travel_mode='rental_drive',travel_hours=4 where user_id=?", (self.f.people['Beneficiary'],))
            db.commit()
            rows = self.m.payroll_rows_for_range(date(2026,9,1),date(2026,9,30),date(2026,10,14))['rows']
            worker = next(r for r in rows if r['worker_id']==self.f.people['Submitter'])
            self.assertEqual(worker['transport_hours'],3)
            self.assertEqual(worker['transport_pay'],30)
            self.assertEqual(worker['self_drive_allowance'],10)
            rental = next(r for r in rows if r['worker_id']==self.f.people['Beneficiary'])
            self.assertEqual(rental['transport_pay'],60)
            for row in rows:
                slip = self.m.payroll_payslip_payload(row)
                self.assertAlmostEqual(sum(line['amount'] for line in slip['lines']),row['total_pay'],places=2)
                self.assertEqual(row['transport_pay'],row['following_allowance']+row['rental_driving_allowance'])
                self.assertEqual(row['subsidy_total'],row['self_drive_allowance']+row['meal_allowance']+row['report_writing_fee'])
            exported = self.m.payroll_row_export(worker)
            self.assertEqual(exported['随行时长'],3)
            self.assertNotIn('交通工资',exported)
        response=self.f.http.get('/payroll/calendar/batch?period_start=2026-09-01')
        self.assertEqual(response.status_code,200)
        batch=response.get_json()
        self.assertAlmostEqual(batch['totals']['total_pay'],sum(slip['total_pay'] for slip in batch['payslips']))
        self.assertTrue(all('随行时长' in row and '交通工资' not in row for row in batch['rows']))
        self.assertEqual(self.f.http.get('/payroll/calendar/export.xlsx?period_start=2026-09-01').status_code,200)


    def test_settlement_excel_name(self):
        from urllib.parse import unquote
        order={'order_number':'SO2607003','client_order_number':'SHPG202608018942'}
        with patch.object(self.m,'require_customer_reimbursement',return_value=({},order)), patch.object(self.m,'customer_reimbursement_items',return_value=[]):
            response=self.f.http.get('/customer-reimbursements/1/download.xlsx')
        self.assertEqual(response.status_code,200)
        self.assertIn('SO2607003_SHPG202608018942_工单结算.xlsx',unquote(response.headers['Content-Disposition']))

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
