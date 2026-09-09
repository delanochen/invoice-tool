import secrets
import unittest
from datetime import date
from flask import template_rendered
from test_expense_on_behalf import ExpenseOnBehalfTest


class ReportCopyTest(unittest.TestCase):
    def setUp(self):
        self.f = ExpenseOnBehalfTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.m, self.http = self.f.app, self.f.http
        self.url = f'/service-orders/{self.f.order}/reports/new'
        self.data = dict(save_token=secrets.token_urlsafe(24), report_date='2026-09-01',
            actual_work_date='2026-09-01', arrival_time_hour='08', arrival_time_minute='00',
            departure_time_hour='16', departure_time_minute='00',
            has_customer_report='no', worker_user_id=str(self.f.people['Submitter']),
            worker_travel_mode='self_drive', worker_driving_miles='12', worker_travel_hours='1',
            worker_public_transport_hours='0', worker_work_description='Original worker task',
            service_description='Original service', cabinet_number='CAB-1', departure_address='Hotel',
            site_address='Site address', mileage_billing_method='per_person',
            saved_part_number='PART-A', saved_part_name='Stored part', saved_quantity='2', saved_status='good',
            replaced_part_number='PART-B', replaced_part_name='Replaced part', replaced_quantity='1',
            replaced_old_serial_number='OLD', replaced_new_serial_number='NEW')
        response = self.http.post(self.url, data=self.data)
        self.assertEqual(response.status_code, 302)
        with self.m.app.app_context():
            row = self.m.db().execute('select * from service_reports order by id desc').fetchone()
            self.assertIsNotNone(row, response.location)
            self.source = dict(row)

    def test_copy_prefills_and_save_creates_independent_report(self):
        contexts=[]
        def capture(sender, template, context, **kwargs):
            contexts.append(context)
        template_rendered.connect(capture, self.m.app)
        try:
            response=self.http.get(self.url+f'?copy_from={self.source["id"]}')
        finally:
            template_rendered.disconnect(capture, self.m.app)
        self.assertEqual(response.status_code,200)
        context=contexts[-1]
        self.assertFalse(context['is_edit'])
        self.assertNotIn('id',context['report'])
        self.assertEqual(context['report']['report_date'],date.today().isoformat())
        self.assertEqual(context['report']['cabinet_number'],'CAB-1')
        self.assertEqual(context['worker_rows'][0]['work_description'],'Original worker task')
        self.assertEqual(context['saved_parts'][0]['part_number'],'PART-A')
        self.assertEqual(context['replaced_parts'][0]['new_serial_number'],'NEW')
        self.assertNotEqual(context['save_token'],self.data['save_token'])
        self.assertIn(f'action="{self.url}"',response.text)
        with self.m.app.app_context():
            self.assertEqual(self.m.db().execute('select count(*) from service_reports').fetchone()[0],1)
        self.f.login('Manager')
        data=dict(self.data,save_token=context['save_token'],report_date=date.today().isoformat(),
                  actual_work_date=date.today().isoformat(),service_description='Copied and edited')
        response=self.http.post(self.url,data=data)
        self.assertEqual(response.status_code,302)
        self.http.post(self.url,data=data)  # Retrying the save must not create another copy.
        with self.m.app.app_context():
            rows=self.m.db().execute('select * from service_reports order by id').fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual(dict(rows[0]),self.source)
            self.assertEqual(rows[1]['created_by'],self.f.people['Manager'])
            self.assertEqual(rows[1]['service_description'],'Copied and edited')
            for table in ['service_report_workers','service_report_saved_parts','service_report_replaced_parts']:
                self.assertEqual(self.m.db().execute(f'select count(*) from {table} where report_id=?',(rows[1]['id'],)).fetchone()[0],1)

    def test_copy_rejects_missing_malformed_and_inaccessible_sources(self):
        self.assertEqual(self.http.get(self.url+'?copy_from=abc').status_code,400)
        self.assertEqual(self.http.get(self.url+'?copy_from=99999999').status_code,404)
        self.f.login('External')
        self.assertEqual(self.http.get(self.url+f'?copy_from={self.source["id"]}').status_code,403)
        self.f.login('Manager')
        with self.m.app.app_context():
            other=self.m.db().execute("insert into service_orders (order_number,client_name,site_address,client_order_number,start_date,created_by,created_at) values ('COPY-OTHER','Other','Address','C','2026-09-01',?,?)",(self.f.people['Manager'],self.m.now())).lastrowid
            self.m.db().commit()
        self.assertEqual(self.http.get(f'/service-orders/{other}/reports/new?copy_from={self.source["id"]}').status_code,404)

    def test_copy_buttons_and_normal_new_report(self):
        self.assertIn('data-row-action="copyUrl"',self.http.get(f'/service-orders/{self.f.order}').text)
        self.assertIn('复制日报',self.http.get(f'/service-reports/{self.source["id"]}/edit').text)
        self.assertEqual(self.http.get(self.url).status_code,200)


if __name__ == '__main__':
    unittest.main()
