"""HTTP business acceptance on a disposable PostgreSQL clone, never production.

Run only with DATABASE_URL naming invoice_acceptance_rehearsal and independent
INVOICE_DATA_DIR under /scratch. Fixtures intentionally remain for inspection.
"""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier
from urllib.parse import urlsplit


@unittest.skipUnless(urlsplit(os.environ.get('DATABASE_URL', '')).path == '/invoice_acceptance_rehearsal',
                     'dedicated acceptance database required')
class PostgreSQLBusinessAcceptance(unittest.TestCase):
    def setUp(self):
        import app as m
        self.m = m
        self.assertTrue(Path(m.DATA_DIR).resolve().is_relative_to('/scratch'))
        m.app.config.update(TESTING=True)
        self.key = 'accept-' + uuid.uuid4().hex[:12]
        self.http = m.app.test_client()
        with m.app.app_context():
            c = m.db()
            self.users = {}
            for role in ('employee', 'manager', 'admin'):
                self.users[role] = c.execute(
                    'insert into users(name,email,password_hash,role,is_active,created_at) values(?,?,?,?,1,?)',
                    (self.key + role, self.key + role + '@test.invalid', 'unused', role, m.now())).lastrowid
            self.order = c.execute('''insert into service_orders
                (order_number,client_name,site_address,client_order_number,start_date,created_by,created_at)
                values(?,?,?,?,?,?,?)''',
                (self.key, 'Acceptance site', 'Isolated address', self.key, '2026-09-01', self.users['employee'], m.now())).lastrowid
            self.project = c.execute("insert into projects(name,project_type,is_active,created_at) values('Accommodation/Lodging','expense',1,?)", (m.now(),)).lastrowid
            c.commit()
        self.login('employee')

    def login(self, role, client=None):
        with (client or self.http).session_transaction() as session:
            session['user_id'] = self.users[role]

    def row(self, sql, args=()):
        with self.m.app.app_context():
            row = self.m.db().execute(sql, args).fetchone()
            return dict(row) if row else None

    def test_expense_attachment_approval_and_settlement_edit(self):
        from PIL import Image
        image = BytesIO()
        Image.new('RGB', (80, 60), 'blue').save(image, 'JPEG')
        content = image.getvalue()
        url = f'/service-orders/{self.order}/expenses/new'
        def form():
            return {'save_token': self.key, 'action': 'submit', 'project_id': str(self.project),
                    'item_amount': '50.25', 'item_description': self.key, 'item_line_key': 'receipt',
                    'expense_date': '2026-09-11', 'beneficiary_id': str(self.users['employee']),
                    'item_attachments_receipt': (BytesIO(content), 'receipt.jpg')}
        self.assertEqual(self.http.post(url, data=form()).status_code, 302)
        expense = self.row('select * from expenses where service_order_id=?', (self.order,))
        self.assertEqual(expense['status'], 'submitted')
        self.assertEqual(expense['amount'], 50.25)
        self.assertEqual(self.http.post(url, data=form()).status_code, 302)
        self.assertEqual(self.row('select count(*) as n from expenses where service_order_id=?', (self.order,))['n'], 1)
        attachment = self.row('select * from expense_attachments where expense_id=?', (expense['id'],))
        preview = self.http.get(f"/expense-attachments/{attachment['id']}")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, 'image/jpeg')
        self.assertGreater(len(preview.data), 0)
        self.assertEqual(self.http.post(f"/expenses/{expense['id']}/approve").status_code, 403)
        self.login('manager')
        self.assertEqual(self.http.post(f"/expenses/{expense['id']}/approve").status_code, 302)
        self.assertEqual(self.row('select status from expenses where id=?', (expense['id'],))['status'], 'approved')
        item = self.row('select * from expense_items where expense_id=?', (expense['id'],))
        self.login('admin')
        self.assertEqual(self.http.post(f'/service-orders/{self.order}/reports/new', data={
            'save_token': self.key+'-report', 'report_date': '2026-09-11', 'actual_work_date': '2026-09-11',
            'arrival_time_hour': '08', 'arrival_time_minute': '00', 'departure_time_hour': '16',
            'departure_time_minute': '00', 'worker_user_id': str(self.users['employee']),
            'worker_travel_mode': 'rental_drive', 'worker_driving_miles': '120',
            'worker_travel_hours': '3', 'worker_public_transport_hours': '', 'worker_work_description': self.key,
            'site_address': 'Isolated site', 'mileage_billing_method': 'per_person'}).status_code, 302)
        with self.http.session_transaction() as session:
            report_messages = session.get('_flashes', [])
        self.assertIsNotNone(self.row('select id from service_reports where service_order_id=?', (self.order,)), report_messages)
        review_url = f'/service-orders/{self.order}/customer-reimbursement/review'
        response = self.http.post(review_url, data={'expense_item_id': str(item['id']), 'lodging_person_nights': '1'})
        with self.http.session_transaction() as session:
            messages = session.get('_flashes', [])
        if response.status_code != 302:
            from html.parser import HTMLParser
            class VisibleText(HTMLParser):
                def handle_data(self, data):
                    if data.strip(): messages.append(data.strip()[:300])
            VisibleText().feed(response.text)
        self.assertEqual(response.status_code, 302, messages[:70])
        settlement = self.row('select * from customer_reimbursements where service_order_id=?', (self.order,))
        self.assertIsNotNone(settlement)
        self.assertEqual(self.row('select count(*) as n from customer_reimbursement_expense_links where customer_reimbursement_id=?', (settlement['id'],))['n'], 1)
        edit_url = f'/service-orders/{self.order}/customer-reimbursement'
        self.assertEqual(self.http.get(edit_url).status_code, 200)
        posted = {'action': 'save', 'worker_name': self.key+'employee', 'project_date': '2026-09-11',
                  'lodging': '40.00', 'source_worker_user_id': str(self.users['employee'])}
        self.assertEqual(self.http.post(edit_url, data=posted).status_code, 302)
        line = self.row('select * from customer_reimbursement_items where customer_reimbursement_id=?', (settlement['id'],))
        self.assertEqual(line['lodging'], 40)
        self.assertEqual(self.http.get(edit_url).status_code, 200)
        reloaded = self.row('select * from customer_reimbursement_items where customer_reimbursement_id=?', (settlement['id'],))
        self.assertEqual(reloaded['lodging'], 40)
        self.assertEqual(self.row('select count(*) as n from customer_reimbursement_expense_links where customer_reimbursement_id=?', (settlement['id'],))['n'], 1)
        self.assertEqual(self.http.get(f"/customer-reimbursements/{settlement['id']}/download.xlsx").status_code, 200)
        with self.m.app.app_context():
            self.m.sync_expense_attachments_to_settlement(self.order)
            self.m.db().commit()
            linked = self.m.db().execute('select * from customer_reimbursement_attachments where customer_reimbursement_id=?', (settlement['id'],)).fetchall()
            self.assertTrue(linked)
            self.assertTrue(all(Path(self.m.customer_reimbursement_attachment_path(row)).is_file() for row in linked))

    def test_daily_report_concurrent_retry_and_hourly_payroll(self):
        with self.m.app.app_context():
            c = self.m.db()
            grade = c.execute('''insert into employee_grades
                (grade_name,car_allowance_method,car_mileage_rate,car_hourly_rate,
                 rental_driving_hourly_rate,transport_hourly_rate,created_at)
                values(?,'mileage',0.5,0,15,10,?)''', (self.key, self.m.now())).lastrowid
            c.execute('update users set employee_grade_id=? where id=?', (grade, self.users['employee']))
            c.commit()
        self.login('admin')
        form = {'save_token': self.key, 'report_date': '2026-09-11', 'actual_work_date': '2026-09-11',
                'arrival_time_hour': '08', 'arrival_time_minute': '00', 'departure_time_hour': '16',
                'departure_time_minute': '00', 'mileage_billing_method': 'per_person',
                'worker_user_id': str(self.users['employee']), 'worker_travel_mode': 'rental_drive',
                'worker_driving_miles': '120', 'worker_travel_hours': '3',
                'worker_public_transport_hours': '', 'worker_work_description': self.key, 'site_address': 'Isolated site'}
        ready = Barrier(2)
        def save(_):
            client = self.m.app.test_client()
            self.login('admin', client)
            ready.wait(timeout=10)
            return client.post(f'/service-orders/{self.order}/reports/new', data=form).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(save, range(2))), [302, 302])
        self.assertEqual(self.row('select count(*) as n from service_reports where service_order_id=?', (self.order,))['n'], 1)
        report = self.row('select * from service_reports where service_order_id=?', (self.order,))
        self.assertEqual(report['driving_miles'], 0)
        with self.m.app.app_context():
            payroll = self.m.payroll_rows_for_range(self.m.date(2026,9,1), self.m.date(2026,9,30),
                                                  self.m.date(2026,10,1), str(self.users['employee']))
        row = payroll['rows'][0]
        self.assertEqual(row['rental_driving_hours'], 3)
        self.assertEqual(row['rental_driving_allowance'], 45)
        self.assertEqual(row['transport_pay'], 45)
        self.assertEqual(row['car_allowance'], 0)


if __name__ == '__main__':
    unittest.main()
