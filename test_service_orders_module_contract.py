"""Behavioral checks for the bounded work-order CRUD extraction."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import url_for
import test_service_order_manufacturer as fixtures


class ServiceOrdersModuleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ServiceOrderManufacturerTest.setUpClass()
        cls.module = fixtures.ServiceOrderManufacturerTest.module

    @classmethod
    def tearDownClass(cls):
        fixtures.ServiceOrderManufacturerTest.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.ServiceOrderManufacturerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.http = self.fixture.http
        self.order_id = self.fixture.order_id
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = patch.object(self.module, 'SHARED_PHOTOS_DIR', self.temp.name)
        root.start()
        self.addCleanup(root.stop)

    def form(self, **extra):
        data = dict(client_id=str(self.fixture.client_id), buyer_id=str(self.fixture.buyer_id),
                    manufacturer_id=str(self.fixture.second_manufacturer_id), site_address='New site address',
                    client_order_number='ORDER-MODULE-TEST', country_code='US',
                    work_order_type_id=str(self.fixture.work_order_type_id), status='open', start_date='2026-10-01')
        data.update(extra)
        return data

    def order(self):
        with self.module.app.app_context():
            return dict(self.module.db().execute('select * from service_orders where id = ?', (self.order_id,)).fetchone())

    def login_external(self, role, client_id=None):
        with self.module.app.app_context():
            connection = self.module.db()
            identifier = connection.execute(
                "insert into users (name,email,password_hash,role,client_id,is_active,created_at) values ('External user',?,'unused',?,?,1,?)",
                (role+'@example.test', role, client_id, self.module.now()),
            ).lastrowid
            connection.commit()
        http = self.module.app.test_client()
        with http.session_transaction() as session:
            session['user_id'] = identifier
        return http, identifier

    def test_endpoint_contract_and_legacy_exports(self):
        expected = {
            'service_orders': ('/service-orders', {'GET'}, {}),
            'new_service_order': ('/service-orders/new', {'GET','POST'}, {}),
            'edit_service_order': ('/service-orders/17/edit', {'GET','POST'}, {'order_id':17}),
            'service_order_detail': ('/service-orders/17', {'GET'}, {'order_id':17}),
            'delete_service_order': ('/service-orders/17/delete', {'POST'}, {'order_id':17}),
        }
        for name, (url, methods, values) in expected.items():
            rules = list(self.module.app.url_map.iter_rules(name))
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].methods - {'HEAD','OPTIONS'}, methods)
            view = getattr(self.module, name)
            self.assertIs(view, self.module.app.view_functions[name])
            self.assertEqual(view.__module__, 'invoice_tool.service_orders.routes')
            with self.module.app.test_request_context():
                self.assertEqual(url_for(name, **values), url)
        # Calendar/map endpoints are now registered by their dedicated module.
        self.assertEqual(self.module.service_order_calendar.__module__, 'invoice_tool.service_orders.calendar_map_routes')
        self.assertEqual(self.module.service_order_map.__module__, 'invoice_tool.service_orders.calendar_map_routes')

    def test_create_uses_current_number_helper_and_creates_photo_folder(self):
        with self.module.app.app_context():
            self.module.db().execute("update users set role='manager' where id=?", (self.fixture.user_id,))
            self.module.db().commit()
        with patch.object(self.module, 'next_service_order_number', return_value='SO-MODULE-NEW') as numbering:
            response = self.http.post('/service-orders/new', data=self.form())
        self.assertEqual(response.status_code, 302)
        numbering.assert_called_once_with()
        with self.module.app.app_context():
            created = self.module.db().execute("select * from service_orders where order_number='SO-MODULE-NEW'").fetchone()
            self.assertIsNotNone(created)
            self.assertEqual(created['manufacturer_id'], self.fixture.second_manufacturer_id)
            self.assertEqual(created['site_address'], 'New site address')
        self.assertTrue((Path(self.temp.name) / 'SO-MODULE-NEW' / 'pictures').is_dir())

    def test_external_manager_edits_only_start_date_and_cannot_edit_closed_order(self):
        http, _ = self.login_external('external_manager', self.fixture.client_id)
        original = self.order()
        response = http.post(f'/service-orders/{self.order_id}/edit', data=self.form(start_date='2026-10-12', status='closed'))
        self.assertEqual(response.status_code, 302)
        changed = self.order()
        self.assertEqual(changed['start_date'], '2026-10-12')
        for key in ('status', 'site_address', 'manufacturer_id', 'client_order_number'):
            self.assertEqual(changed[key], original[key])
        with self.module.app.app_context():
            self.module.db().execute("update service_orders set status='closed' where id=?", (self.order_id,))
            self.module.db().commit()
        self.assertEqual(http.post(f'/service-orders/{self.order_id}/edit', data={'start_date':'2026-11-01'}).status_code, 302)
        self.assertEqual(self.order()['start_date'], '2026-10-12')

    def test_external_employee_sees_only_assigned_orders(self):
        http, uid = self.login_external('external_employee', self.fixture.client_id)
        self.assertNotIn('SO2699999', http.get('/service-orders').get_data(as_text=True))
        self.assertEqual(http.get(f'/service-orders/{self.order_id}').status_code, 403)
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute('insert into user_service_orders (user_id,service_order_id,assigned_by,assigned_at) values (?,?,?,?)', (uid,self.order_id,self.fixture.user_id,self.module.now()))
            connection.commit()
        response = http.get('/service-orders')
        self.assertEqual(response.status_code, 200)
        self.assertIn('SO2699999', response.get_data(as_text=True))
        self.assertEqual(http.get(f'/service-orders/{self.order_id}').status_code, 200)

    def test_delete_resolves_current_dependency_helper_and_preserves_order(self):
        counts = dict(reports=0, invoices=0, customer_reimbursements=0, expenses=1)
        with patch.object(self.module, 'service_order_dependency_counts', return_value=counts) as dependency:
            response = self.http.post(f'/service-orders/{self.order_id}/delete')
        self.assertEqual(response.status_code, 302)
        dependency.assert_called_once_with(self.order_id)
        self.assertEqual(self.order()['id'], self.order_id)

    def test_detail_resolves_settlement_expense_helper_inside_comprehension(self):
        with patch.object(self.module, 'latest_customer_reimbursement', return_value={'id':123}):
            with patch.object(self.module, 'customer_reimbursement_items', return_value=[{'id':7}]):
                with patch.object(self.module, 'customer_reimbursement_item_expense_amount', return_value=12) as amount:
                    with patch('invoice_tool.service_orders.routes.render_template', return_value='captured') as render:
                        response = self.http.get(f'/service-orders/{self.order_id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(amount.call_count, 8)
        self.assertEqual(set(render.call_args.kwargs['settlement_items'][0]['expense_amounts'].values()), {12})

    def test_external_manager_cannot_view_other_client_orders(self):
        with self.module.app.app_context():
            connection = self.module.db()
            other_client = connection.execute("insert into clients (client_number,name,short_name,created_at) values ('OTHER-CT','Other client','Other',?)", (self.module.now(),)).lastrowid
            connection.commit()
        http, _ = self.login_external('external_manager', other_client)
        response = http.get('/service-orders')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('SO2699999', response.get_data(as_text=True))
        self.assertEqual(http.get(f'/service-orders/{self.order_id}').status_code, 403)

    def test_missing_order_and_unauthenticated_request_keep_status(self):
        self.assertEqual(self.http.get('/service-orders/999999999').status_code, 404)
        guest = self.module.app.test_client()
        self.assertEqual(guest.get(f'/service-orders/{self.order_id}').status_code, 302)
        self.assertEqual(guest.post(f'/service-orders/{self.order_id}/delete').status_code, 302)
