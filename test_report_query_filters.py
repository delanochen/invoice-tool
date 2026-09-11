import unittest
from unittest.mock import patch
from test_expense_on_behalf import ExpenseOnBehalfTest


class ReportFiltersTest(unittest.TestCase):
    def setUp(self):
        self.f = ExpenseOnBehalfTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.login('Manager')
        self.m = self.f.app
        with self.m.app.app_context():
            db = self.m.db()
            self.second = db.execute("insert into service_orders (order_number,client_name,site_address,client_order_number,created_by,created_at) values ('SO-SECOND','Site B','','',?,?)", (self.f.people['Manager'], self.m.now())).lastrowid
            self.reports = []
            for order, workers in [(self.f.order, ['Submitter', 'Beneficiary']), (self.second, ['Unrelated'])]:
                report = db.execute("insert into service_reports (service_order_id,report_date,actual_work_date,created_by,created_at,updated_at) values (?,'2026-09-10','2026-09-10',?,?,?)", (order, self.f.people['Manager'], self.m.now(), self.m.now())).lastrowid
                self.reports.append(report)
                for worker in workers:
                    db.execute('insert into service_report_workers (report_id,user_id) values (?,?)', (report, self.f.people[worker]))
            db.commit()

    def query(self, **args):
        with patch.object(self.m, 'render_template', return_value='ok') as render:
            response = self.f.http.get('/reports/service-reports', query_string=args)
        self.assertEqual(response.status_code, 200)
        return render.call_args.kwargs['rows']

    def test_any_worker_preserves_full_roster(self):
        rows = self.query(worker_id=[str(self.f.people['Beneficiary'])])
        self.assertEqual([r['id'] for r in rows], self.reports[:1])
        self.assertIn('Submitter', rows[0]['worker_names'])
        self.assertIn('Beneficiary', rows[0]['worker_names'])
        self.assertEqual(len(self.query(worker_id=[str(self.f.people['Beneficiary']), str(self.f.people['Unrelated'])])), 2)

    def test_intersection_multi_order_sites_and_removed_location(self):
        self.assertEqual(len(self.query(order_id=[str(self.f.order), str(self.second)], site=['Site', 'Site B'])), 2)
        self.assertEqual(len(self.query(site=['Site B'], worker_id=[str(self.f.people['Beneficiary'])])), 0)
        self.assertEqual(len(self.query(region_code='invalid', country_code='invalid')), 2)

    def test_form_selection_and_removed_controls(self):
        page = self.f.http.get('/reports/service-reports', query_string={'worker_id': str(self.f.people['Beneficiary'])}).text
        self.assertIn('data-report-multi', page)
        self.assertIn(f'value="{self.f.people["Beneficiary"]}" checked', page)
        self.assertNotIn('name="region_code"', page)
        self.assertNotIn('name="country_code"', page)

    def test_settlement_filters_and_totals(self):
        with self.m.app.app_context():
            for order, amount in [(self.f.order, 100), (self.second, 200)]:
                self.m.db().execute("insert into customer_reimbursements (service_order_id,file_name,stored_filename,total_amount,created_by,created_at) values (?,'test.pdf','test.pdf',?,?,'2026-09-10')", (order, amount, self.f.people['Manager']))
            self.m.db().commit()
        with patch.object(self.m, 'render_template', return_value='ok') as render:
            response = self.f.http.get('/reports/customer-reimbursements', query_string={'order_id': [str(self.f.order), str(self.second)], 'site': ['Site B'], 'country_code': 'invalid'})
        self.assertEqual(response.status_code, 200)
        result = render.call_args.kwargs
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['totals']['total_amount'], 200)
        page = self.f.http.get('/reports/customer-reimbursements').text
        self.assertIn('data-report-multi', page)
        self.assertNotIn('name="country_code"', page)

    def test_invoice_multiselect_and_removed_filters(self):
        with self.m.app.app_context():
            db = self.m.db()
            client = db.execute("insert into clients (client_number,name,short_name,created_at) values ('TEST','Client','Client','now')").lastrowid
            for index, order in enumerate([self.f.order, self.second]):
                invoice = db.execute("insert into invoices (invoice_number,client_id,service_order_id,issue_date,due_date,created_by,created_at) values (?,?,?,'2026-09-10','2026-10-10',?,'now')", (f'TEST-{index}', client, order, self.f.people['Manager'])).lastrowid
                db.execute("insert into invoice_items (invoice_id,project_id,description,amount,tax_rate) values (?,?,'test',?,0)", (invoice, self.f.project, 100 * (index + 1)))
            db.commit()
        with patch.object(self.m, 'render_template', return_value='ok') as render:
            response = self.f.http.get('/invoices', query_string={'order_id': [str(self.f.order), str(self.second)], 'site': ['Site B'], 'q': 'no-match', 'status': 'invalid', 'created_by': 'invalid'})
        self.assertEqual(response.status_code, 200)
        result = render.call_args.kwargs
        self.assertEqual(len(result['invoices']), 1)
        self.assertEqual(result['summary_totals']['USD'], 200)
        page = self.f.http.get('/invoices').text
        self.assertIn('data-report-multi', page)
        for name in ['q', 'status', 'created_by']:
            self.assertNotIn(f'name="{name}"', page)
        for name in ['paid_status', 'work_order_status', 'date_from', 'date_to']:
            self.assertIn(f'name="{name}"', page)

    def test_invoice_detail_report_uses_service_order_number_and_no_customer_column(self):
        with self.m.app.app_context():
            db = self.m.db()
            client = db.execute("insert into clients (client_number,name,short_name,created_at) values ('RPT','Report Client','RC','now')").lastrowid
            invoice = db.execute("insert into invoices (invoice_number,client_id,service_order_id,issue_date,due_date,created_by,created_at) values ('RPT-1',?,?,'2026-09-10','2026-10-10',?,'now')", (client, self.f.order, self.f.people['Manager'])).lastrowid
            db.execute("insert into invoice_items (invoice_id,project_id,description,amount,tax_rate) values (?,?,'test',10,0)", (invoice, self.f.project))
            db.commit()
        for path in ['/reports/invoices', '/invoices']:
            page = self.f.http.get(path).text
            self.assertIn('服务订单号码', page)
            self.assertIn('ORDER', page)
            self.assertNotIn('<th>客户</th>', page)


if __name__ == '__main__':
    unittest.main()
