"""Versioned base pay and daily meal allowances, without a database connection."""
from datetime import date
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from flask import Flask, g
from jinja2 import Environment, FileSystemLoader

import rate_engine
from invoice_tool.payroll.services import build_payroll_services
from invoice_tool.payroll.calculations import payroll_component_rows
from tests import test_no_car_allowance as no_car_tests


class GradeCompensationVersionsTest(unittest.TestCase):
    def test_meal_uses_service_day_and_base_uses_period_start(self):
        entries = []
        for report_id, day, order in [(1, '2026-10-30', 1), (2, '2026-10-30', 2), (3, '2026-10-31', 2)]:
            row = no_car_tests.NoCarAllowanceTest().entry('self_drive')
            row.update(report_id=report_id, report_date=day, actual_work_date=day,
                       attendance_date=day, service_order_id=order, worker_count=1)
            entries.append(row)
        connection = Mock()
        def execute(sql, params=()):
            return Mock(fetchall=lambda: [] if 'service_order_employee_settlements' in sql
                        or 'count(service_reports.id)' in sql else entries)
        connection.execute.side_effect = execute
        api = dict(db=lambda: connection, payroll_subsidy_settings=lambda: {'report_writing_fee': 0},
                   split_report_labor_hours=lambda row, count: dict(standard_hours=8,
                        overtime_hours=0, holiday_hours=0, transport_hours=3))
        split_hours = api['split_report_labor_hours']
        services = build_payroll_services(api)
        api.update(services)
        api['split_report_labor_hours'] = split_hours
        rate_calls = []
        def employee_rate(connection, worker, day, key):
            rate_calls.append((day, key))
            if key == 'meal_daily_amount':
                return {'rate': 10 if day < '2026-10-31' else 20}
            if key == 'base_salary':
                return {'rate': 100 if day < '2026-10-31' else 200}
            return {'rate': 20}
        with patch('invoice_tool.payroll.services.employee_rate', side_effect=employee_rate):
            summary = services['payroll_rows_for_range'](date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1))
            detail = services['payroll_rows_for_range'](date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), detail=True)
            resolved = services['labor_report_entries']()
        self.assertEqual(summary['totals']['meal_allowance'], 30)
        self.assertEqual(summary['totals']['base_salary'], 100)
        self.assertEqual(summary['totals']['total_pay'], 610)
        for key in ['base_salary', 'meal_allowance', 'total_pay']:
            self.assertEqual(summary['totals'][key], detail['totals'][key], key)
        self.assertEqual(summary['rows'][0]['meal_daily_amount'], 15)
        self.assertIn(('2026-10-30', 'base_salary'), rate_calls)
        self.assertNotIn(('2026-10-31', 'base_salary'), rate_calls)
        components = payroll_component_rows(resolved, [], {'report_writing_fee': 0}, date(2026, 10, 30), lambda *args: 20)
        meals = [row for row in components if row['component_code'] == 'meal_allowance']
        self.assertEqual([row['amount'] for row in meals], [10, 20])
        self.assertEqual(sum(row['amount'] for row in components), 610)

    def test_old_versions_fall_back_without_losing_supplements(self):
        for key, static_rate in [('base_salary', 100), ('meal_daily_amount', 10)]:
            connection = Mock()
            connection.execute.return_value.fetchone.side_effect = [
                dict(employee_grade_id=1, car_allowance_method='none', base_salary=100, meal_daily_amount=10),
                dict(id=2, version_no=1), None]
            self.assertEqual(rate_engine.employee_rate(connection, 1, '2026-10-30', key)['rate'], static_rate)

    def test_edit_old_version_inserts_new_supplement_items(self):
        app = Flask(__name__)
        app.secret_key = 'synthetic'
        grade = dict(id=1, grade_name='Test', base_salary=100, meal_daily_amount=10)
        version = dict(id=2, employee_grade_id=1, version_no=1, effective_from='2026-10-01', effective_to=None)
        connection = Mock()
        connection.execute.return_value.fetchone.side_effect = [grade, version]
        connection.execute.return_value.fetchall.return_value = [dict(rate_type='regular_hours', rate=20)]
        api = dict(db=lambda: connection, login_required=lambda view: view,
                   has_action_permission=lambda *args: True, now=lambda: '2026-10-31', log_action=Mock())
        rate_engine.register_rate_routes(app, api)
        # This endpoint redirects to the management route registered elsewhere.
        app.add_url_rule('/employee-grades', endpoint='employee_grades', view_func=lambda: '')
        with patch('rate_engine._validate_range_against_versions', return_value=None):
            response = app.test_client().post('/employee-grades/1/rates/2/edit', data=dict(
                effective_from='2026-10-01', regular_hours='25', base_salary='300', meal_daily_amount='40'))
        self.assertEqual(response.status_code, 302)
        inserts = [call.args[1] for call in connection.execute.call_args_list if 'insert into employee_rate_items' in call.args[0]]
        self.assertIn((2, 'meal_daily_amount', 'day', 40), inserts)
        self.assertIn((2, 'base_salary', 'period', 300), inserts)
        connection.commit.assert_called_once()

    def test_copy_old_version_keeps_static_base_and_meal(self):
        app = Flask(__name__)
        app.secret_key = 'synthetic'
        connection = Mock()
        connection.execute.return_value.fetchone.side_effect = [
            dict(id=1, grade_name='Test', base_salary=100, meal_daily_amount=10),
            dict(id=2, version_no=1), dict(value=2)]
        connection.execute.return_value.fetchall.return_value = [dict(rate_type='regular_hours', rate=20)]
        connection.execute.return_value.lastrowid = 3
        api = dict(db=lambda: connection, login_required=lambda view: view,
                   has_action_permission=lambda *args: True, now=lambda: '2026-10-31', log_action=Mock())
        rate_engine.register_rate_routes(app, api)
        app.add_url_rule('/employee-grades', endpoint='employee_grades', view_func=lambda: '')
        @app.before_request
        def user():
            g.user = {'id': 1}
        with patch('rate_engine._prepare_new_version_range', return_value=None):
            response = app.test_client().post('/employee-grades/1/rates', data=dict(
                effective_from='2026-11-01', copy_version_id='2'))
        self.assertEqual(response.status_code, 302)
        inserts = [call.args[1] for call in connection.execute.call_args_list if 'insert into employee_rate_items' in call.args[0]]
        self.assertIn((3, 'meal_daily_amount', 'day', 10), inserts)
        self.assertIn((3, 'base_salary', 'period', 100), inserts)
        connection.commit.assert_called_once()

    def test_grade_dialog_has_only_profile_and_allowance_switch(self):
        env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/'templates'))
        source = env.loader.get_source(env, 'employee_grades.html')[0]
        macro = source[source.index('{% macro grade_form'):source.index('{% endmacro %}', source.index('{% macro grade_form'))+len('{% endmacro %}')]
        html = env.from_string(macro+'{{ grade_form(grade) }}').render(
            grade=dict(id=1, grade_name='Test', description='', car_allowance_method='hourly'), url_for=lambda *args: '#')
        self.assertNotIn('name="base_salary"', html)
        self.assertNotIn('name="meal_daily_amount"', html)
        self.assertNotIn('value="hourly"', html)
        self.assertIn('value="none"', html)
        self.assertIn('发放', html)


if __name__ == '__main__':
    unittest.main()
