"""List deletion responses use fake dependencies, with no database connection."""
import inspect
import unittest
from unittest.mock import Mock

from flask import Flask, g, get_flashed_messages

from invoice_tool.customers.routes import register_customer_routes
from invoice_tool.catalog.routes import register_catalog_routes
from invoice_tool.employees.user_routes import register_employee_user_routes


class ListDeleteResponsesTest(unittest.TestCase):
    def make_app(self, register, records):
        app = Flask(__name__)
        app.secret_key = 'synthetic-test-only'
        app.testing = True
        database = Mock()
        database.execute.return_value.fetchone.side_effect = records
        deps = {key: Mock() for key in inspect.signature(register).parameters if key != 'app'}
        deps.update(db=lambda: database, login_required=lambda fn: fn)
        if 'has_action_permission' in deps:
            deps['has_action_permission'] = lambda *args: True
        for key, value in [('normalized_role', lambda *args: 'admin'),
                           ('is_external_user', lambda: False),
                           ('can_access_buyer', lambda buyer: True)]:
            if key in deps:
                deps[key] = value
        register(app, **deps)

        @app.before_request
        def current_user():
            g.user = {'id': 100}

        return app, database

    def test_each_list_success_and_dependency_rejection(self):
        cases = [
            (register_customer_routes, '/clients/1/delete', [{'count': 0}, {'count': 0}], [{'count': 1}, {'count': 0}]),
            (register_customer_routes, '/owners/1/delete', [{'name': 'synthetic'}, {'count': 0}], [{'name': 'synthetic'}, {'count': 1}]),
            (register_customer_routes, '/manufacturers/1/delete', [{'id': 1}, {'count': 0}], [{'id': 1}, {'count': 1}]),
            (register_customer_routes, '/buyers/1/delete', [{'id': 1}, {'count': 0}], [{'id': 1}, {'count': 1}]),
            (register_catalog_routes, '/work-order-types/1/delete', [{'count': 0}], [{'count': 1}]),
            (register_catalog_routes, '/projects/1/delete', [{'count': 0}, {'count': 0}], [{'count': 0}, {'count': 1}]),
            (register_employee_user_routes, '/users/1/delete', [{'role': 'employee'}, {'count': 2}, {'count': 0}], [{'role': 'employee'}, {'count': 2}, {'count': 1}]),
        ]
        for register, url, allowed, blocked in cases:
            for records, success in [(allowed, True), (blocked, False)]:
                with self.subTest(url=url, success=success):
                    app, database = self.make_app(register, records)
                    response = app.test_client().post(url, headers={'X-Requested-With': 'XMLHttpRequest'})
                    self.assertEqual(response.status_code, 200 if success else 409)
                    self.assertEqual(response.json['ok'], success)
                    self.assertTrue(response.json['message'])
                    self.assertEqual(database.commit.call_count, int(success))
                    if not success:
                        self.assertFalse(any('delete from' in call.args[0].lower() for call in database.execute.call_args_list))

    def test_ordinary_form_still_redirects_and_flashes(self):
        app, _ = self.make_app(register_customer_routes, [{'count': 0}, {'count': 0}])
        with app.test_client() as client:
            response = client.post('/clients/1/delete')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, '/clients')
            self.assertEqual(get_flashed_messages(with_categories=True), [('success', '客户已删除。')])

    def test_protected_records_are_not_deleted(self):
        for register, url, records in [
            (register_customer_routes, '/owners/1/delete', [{'name': '未知'}]),
            (register_employee_user_routes, '/users/100/delete', []),
            (register_employee_user_routes, '/users/1/delete', [{'role': 'admin'}, {'count': 1}]),
        ]:
            with self.subTest(url=url):
                app, database = self.make_app(register, records)
                response = app.test_client().post(url, headers={'X-Requested-With': 'XMLHttpRequest'})
                self.assertEqual(response.status_code, 409)
                self.assertFalse(response.json['ok'])
                database.commit.assert_not_called()

    def test_missing_record_does_not_report_success(self):
        app, database = self.make_app(register_customer_routes, [None])
        response = app.test_client().post('/owners/1/delete', headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 404)
        database.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
