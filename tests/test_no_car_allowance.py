"""No-allowance grade policy: no app import and no database connection."""
from datetime import date
import inspect
import unittest
from unittest.mock import Mock

from flask import Flask
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

from invoice_tool.employees.grade_routes import register_employee_grade_routes
from invoice_tool.payroll.calculations import aggregate_payroll_rows, payroll_component_rows
from rate_engine import (EMPLOYEE_TRANSPORT_RATE_TYPES, EMPLOYEE_STATIC_RATE_COLUMNS,
                         employee_rate, employee_grade_rate_snapshot, current_rate_values)


class NoCarAllowanceTest(unittest.TestCase):
    def grade(self, method):
        return dict(id=1, employee_grade_id=1, car_allowance_method=method,
                    **{column: 20 for column in EMPLOYEE_STATIC_RATE_COLUMNS.values()})

    def test_policy_overrides_saved_version_and_static_transport_rates(self):
        for rate_type in EMPLOYEE_TRANSPORT_RATE_TYPES:
            with self.subTest(rate_type=rate_type):
                connection = Mock()
                connection.execute.return_value.fetchone.return_value = self.grade('none')
                result = employee_rate(connection, 1, '2026-10-31', rate_type)
                self.assertEqual(result['rate'], 0)
                self.assertEqual(result['source'], 'no_car_allowance')
                self.assertEqual(connection.execute.call_count, 1)

    def test_other_methods_and_work_wages_still_use_rate_versions(self):
        for method, rate_type in [('mileage', 'mileage'), ('hourly', 'travel_hours'),
                                  ('none', 'regular_hours'), ('none', 'overtime_hours'), ('none', 'holiday_hours')]:
            connection = Mock()
            connection.execute.return_value.fetchone.side_effect = [self.grade(method),
                {'id': 2, 'version_no': 1}, {'rate': 30, 'unit': 'hour'}]
            self.assertEqual(employee_rate(connection, 1, '2026-10-31', rate_type)['rate'], 30)

    def test_snapshot_is_zero_but_copying_version_preserves_saved_rates(self):
        def connection():
            con = Mock()
            con.execute.return_value.fetchone.side_effect = [self.grade('none'), None]
            return con
        snapshot = employee_grade_rate_snapshot(connection(), 1, '2026-10-31')
        for key in EMPLOYEE_TRANSPORT_RATE_TYPES:
            self.assertEqual(snapshot['rates'][key]['rate'], 0)
            self.assertNotEqual(snapshot['rates'][key]['source'], 'missing')
        saved = current_rate_values(connection(), 1, '2026-10-31')
        self.assertTrue(all(saved[key] == 20 for key in EMPLOYEE_TRANSPORT_RATE_TYPES))

    def entry(self, mode, method='none'):
        return dict(worker_id=1, worker_name='Synthetic', grade_name='Test',
                    car_allowance_method=method, base_salary=100, meal_daily_amount=10,
                    car_mileage_rate=5, rental_driving_hourly_rate=30, standard_hourly_rate=20,
                    transport_hourly_rate=30, overtime_hourly_rate=30, holiday_hourly_rate=40,
                    attendance_date='2026-10-30', service_order_id=1, order_number='TEST-1',
                    client_name='Synthetic', report_id=1, worker_travel_mode=mode,
                    worker_driving_miles=50, worker_travel_hours=3,
                    worker_public_transport_hours=4, public_transport_hours=4,
                    standard_hours=8, transport_hours=7, overtime_hours=1, holiday_hours=0)

    def test_aggregate_detail_and_payment_components_exclude_transport(self):
        entries = [self.entry(mode) for mode in ['self_drive', 'following', 'rental_drive', 'flight', 'legacy']]
        settings = {'report_writing_fee': 0}
        resolver = lambda *args: 20  # Even nonzero custom rates cannot override this grade policy.
        for detail in [False, True]:
            rows, totals = aggregate_payroll_rows(entries, [], settings, date(2026, 10, 30),
                                                   detail=detail, rate_resolver=resolver)
            for row in rows:
                for key in ['transport_pay', 'car_allowance', 'self_drive_allowance',
                            'following_allowance', 'rental_driving_allowance']:
                    self.assertEqual(row[key], 0, key)
                self.assertEqual(row['car_allowance_method'], 'none')
            self.assertEqual(sum(row['driving_miles'] for row in rows), 100)
            self.assertEqual(sum(row['following_travel_hours'] for row in rows), 3)
            self.assertEqual(totals['total_pay'], 1010)
        components = payroll_component_rows(entries, [], settings, date(2026, 10, 30), resolver)
        self.assertFalse({'self_drive_allowance', 'following_allowance', 'rental_driving_allowance'}
                         & {row['component_code'] for row in components})
        self.assertEqual(sum(row['amount'] for row in components), 1010)
        for method in ['mileage', 'hourly']:
            rows, _ = aggregate_payroll_rows([self.entry('following', method)], [], settings,
                date(2026, 10, 30), rate_resolver=resolver)
            self.assertEqual(rows[0]['transport_pay'], 60)

    def test_create_and_edit_save_none_without_clearing_saved_rates(self):
        for editing in [False, True]:
            app = Flask(__name__)
            app.secret_key = 'synthetic'
            con = Mock()
            existing = dict(self.grade('hourly'), grade_name='Test', base_salary=100,
                            meal_daily_amount=10, car_hourly_rate=20)
            con.execute.return_value.fetchone.return_value = existing
            dependencies = {key: Mock() for key in inspect.signature(register_employee_grade_routes).parameters if key != 'app'}
            dependencies.update(db=lambda: con, now=lambda: '2026-10-31',
                                login_required=lambda view: view, can_manage_employee_grades=lambda: True,
                                to_float=lambda value, default=0: float(value or default))
            register_employee_grade_routes(app, **dependencies)
            form = dict(grade_name='Test', car_allowance_method='none')
            if editing: form['grade_id'] = '1'
            response = app.test_client().post('/employee-grades', data=form)
            self.assertEqual(response.status_code, 302)
            mutation = next(call for call in con.execute.call_args_list
                            if 'update employee_grades' in call.args[0] or 'insert into employee_grades' in call.args[0])
            self.assertEqual(mutation.args[1][4], 'none')
            if editing:
                self.assertEqual(mutation.args[1][5], 20)
            con.commit.assert_called_once()

    def test_edit_dialog_selects_none_as_the_only_selected_option(self):
        env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/'templates'))
        source = env.loader.get_source(env, 'employee_grades.html')[0]
        macro = source[source.index('{% macro grade_form'):source.index('{% endmacro %}', source.index('{% macro grade_form'))+len('{% endmacro %}')]
        rendered = env.from_string(macro + '{{ grade_form(grade) }}').render(
            grade=dict(self.grade('none'), grade_name='Test', base_salary=0, meal_daily_amount=0), url_for=lambda *args: '#')
        from html.parser import HTMLParser
        class Options(HTMLParser):
            selected = []
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == 'option' and 'selected' in values:
                    self.selected.append(values['value'])
        parser = Options()
        parser.feed(rendered)
        self.assertEqual(parser.selected, ['none'])


if __name__ == '__main__':
    unittest.main()
