"""员工等级工作台：删除/停用校验 + 工资按等级费率版本计算。

规则：
- 等级被员工使用时不能删除（员工停用与否均不可删）；未分配时可删，费率版本一并清理。
- 仍有在职员工使用时不能停用；员工全部停用后可以停用等级。
- 工资计算按工作日落在的有效期费率版本取价，没有版本时回退等级静态费率列。
"""
import importlib.util
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import test_expense_on_behalf as fixture

ROOT = Path(__file__).resolve().parent


class EmployeeGradesWorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.m = self.fixture.app
        self.http = self.fixture.http
        self.fixture.login('Manager')
        with self.m.app.app_context():
            db = self.m.db()
            self.grade_id = db.execute(
                """insert into employee_grades (grade_name, description, base_salary, meal_daily_amount,
                   car_allowance_method, car_mileage_rate, car_hourly_rate, rental_driving_hourly_rate,
                   standard_hourly_rate, transport_hourly_rate, overtime_hourly_rate, holiday_hourly_rate,
                   is_active, created_at)
                   values ('P1','初级',100,0,'mileage',0.5,0,15,10,5,20,30,1,?)""",
                (self.m.now(),),
            ).lastrowid
            db.commit()

    # ------------------------------------------------------------------ 工具
    def query(self, sql, params=()):
        with self.m.app.app_context():
            return [dict(row) for row in self.m.db().execute(sql, params).fetchall()]

    def assign_grade(self, user_id, grade_id=None):
        with self.m.app.app_context():
            db = self.m.db()
            db.execute('update users set employee_grade_id = ? where id = ?',
                       (grade_id if grade_id is not None else self.grade_id, user_id))
            db.commit()

    def set_user_active(self, user_id, active):
        with self.m.app.app_context():
            db = self.m.db()
            db.execute('update users set is_active = ? where id = ?', (active, user_id))
            db.commit()

    def add_version(self, effective_from, effective_to, rates):
        with self.m.app.app_context():
            db = self.m.db()
            version_id = db.execute(
                """insert into employee_rate_versions
                   (employee_grade_id, version_no, effective_from, effective_to, status, notes, created_by, created_at, updated_at)
                   values (?, 1, ?, ?, 'active', '', ?, ?, ?)""",
                (self.grade_id, effective_from, effective_to, self.fixture.people['Manager'],
                 self.m.now(), self.m.now()),
            ).lastrowid
            for key, value in rates.items():
                db.execute('insert into employee_rate_items (version_id, rate_type, unit, rate) values (?, ?, ?, ?)',
                           (version_id, key, 'hour', value))
            db.commit()
        return version_id

    def page(self, grade_id=None):
        url = '/employee-grades' + (f'?grade_id={grade_id}' if grade_id else '')
        return self.http.get(url).get_data(as_text=True)

    # ------------------------------------------------------- 停用/删除校验
    def test_deactivate_blocked_until_all_assigned_employees_disabled(self):
        self.assign_grade(self.fixture.people['Submitter'])
        self.http.post(f'/employee-grades/{self.grade_id}/state', data={'is_active': '0'})
        self.assertIn('不能停用', self.page(self.grade_id))
        self.assertEqual(self.query(
            'select is_active from employee_grades where id = ?', (self.grade_id,))[0]['is_active'], 1)
        self.set_user_active(self.fixture.people['Submitter'], 0)
        self.http.post(f'/employee-grades/{self.grade_id}/state', data={'is_active': '0'})
        self.assertIn('员工等级已停用', self.page(self.grade_id))
        self.assertEqual(self.query(
            'select is_active from employee_grades where id = ?', (self.grade_id,))[0]['is_active'], 0)

    def test_reactivate_grade(self):
        self.http.post(f'/employee-grades/{self.grade_id}/state', data={'is_active': '0'})
        self.http.post(f'/employee-grades/{self.grade_id}/state', data={'is_active': '1'})
        self.assertIn('员工等级已启用', self.page(self.grade_id))
        self.assertEqual(self.query(
            'select is_active from employee_grades where id = ?', (self.grade_id,))[0]['is_active'], 1)

    def test_delete_blocked_while_assigned_even_if_employee_disabled(self):
        self.assign_grade(self.fixture.people['Submitter'])
        self.set_user_active(self.fixture.people['Submitter'], 0)
        self.http.post(f'/employee-grades/{self.grade_id}/delete')
        self.assertIn('不能删除', self.page())
        self.assertEqual(self.query(
            'select count(*) as n from employee_grades where id = ?', (self.grade_id,))[0]['n'], 1)

    def test_delete_allowed_when_unassigned_and_removes_versions(self):
        self.add_version('2026-09-01', None, {'regular_hours': 25, 'mileage': 0.9})
        self.http.post(f'/employee-grades/{self.grade_id}/delete')
        self.assertIn('员工等级已删除', self.page())
        self.assertEqual(self.query(
            'select count(*) as n from employee_grades where id = ?', (self.grade_id,))[0]['n'], 0)
        self.assertEqual(self.query(
            'select count(*) as n from employee_rate_versions where employee_grade_id = ?', (self.grade_id,))[0]['n'], 0)
        self.assertEqual(self.query(
            """select count(*) as n from employee_rate_items where version_id not in
               (select id from employee_rate_versions)""")[0]['n'], 0)

    # --------------------------------------------------- 工资按版本费率计算
    def test_payroll_uses_effective_rate_version_per_day_with_static_fallback(self):
        self.assign_grade(self.fixture.people['Submitter'])
        self.add_version('2026-09-01', '2026-09-02', {
            'regular_hours': 25, 'overtime_hours': 40, 'holiday_hours': 60,
            'travel_hours': 7, 'public_transport_hours': 7, 'mileage': 0.9, 'rental_drive_hours': 18,
        })

        def entry(attendance_date, standard_hours, miles=0):
            return dict(
                report_id=1, worker_id=self.fixture.people['Submitter'], worker_name='Submitter',
                grade_name='P1', base_salary=100, meal_daily_amount=0, car_allowance_method='mileage',
                car_mileage_rate=0.5, rental_driving_hourly_rate=15, standard_hourly_rate=10,
                transport_hourly_rate=5, overtime_hourly_rate=20, holiday_hourly_rate=30,
                attendance_date=attendance_date, service_order_id=self.fixture.order,
                order_number='SO-DELEGATE', client_name='Site', worker_travel_mode='self_drive',
                worker_travel_hours=0, worker_driving_miles=miles,
                standard_hours=standard_hours, transport_hours=0, overtime_hours=0, holiday_hours=0,
            )

        rows = [entry('2026-09-01', 2, miles=10), entry('2026-09-03', 1)]
        with patch.object(self.m, 'labor_report_entries', return_value=rows):
            with self.m.app.app_context():
                batch = self.m.payroll_rows_for_range(date(2026, 9, 1), date(2026, 9, 14), date(2026, 9, 28), '')
        self.assertEqual(len(batch['rows']), 1)
        row = batch['rows'][0]
        # 09-01 在版本有效期内：2 小时 × 25；09-03 无版本：1 小时 × 静态 10
        self.assertAlmostEqual(row['standard_pay'], 60)
        # 09-01 里程 10 英里 × 版本 0.9（不是静态 0.5）
        self.assertAlmostEqual(row['self_drive_allowance'], 9)
        self.assertAlmostEqual(row['total_pay'], 100 + 60 + 9)

    def test_payroll_falls_back_to_static_rates_without_versions(self):
        self.assign_grade(self.fixture.people['Submitter'])
        rows = [dict(
            report_id=1, worker_id=self.fixture.people['Submitter'], worker_name='Submitter',
            grade_name='P1', base_salary=100, meal_daily_amount=0, car_allowance_method='mileage',
            car_mileage_rate=0.5, rental_driving_hourly_rate=15, standard_hourly_rate=10,
            transport_hourly_rate=5, overtime_hourly_rate=20, holiday_hourly_rate=30,
            attendance_date='2026-09-01', service_order_id=self.fixture.order,
            order_number='SO-DELEGATE', client_name='Site', worker_travel_mode='self_drive',
            worker_travel_hours=0, worker_driving_miles=8,
            standard_hours=3, transport_hours=0, overtime_hours=0, holiday_hours=0,
        )]
        with patch.object(self.m, 'labor_report_entries', return_value=rows):
            with self.m.app.app_context():
                batch = self.m.payroll_rows_for_range(date(2026, 9, 1), date(2026, 9, 14), date(2026, 9, 28), '')
        row = batch['rows'][0]
        self.assertAlmostEqual(row['standard_pay'], 30)
        self.assertAlmostEqual(row['self_drive_allowance'], 4)

    # --------------------------------------------------- 双板块页面与兼容路由
    def test_workbench_renders_and_old_rates_route_redirects(self):
        self.add_version('2026-09-01', None, {'regular_hours': 25})
        page = self.page(self.grade_id)
        self.assertIn('grade-workbench', page)
        self.assertIn('新增结算版本', page)
        self.assertIn('v1', page)
        self.assertIn('2026-09-01', page)
        # 未选等级时自动选中第一个
        self.assertIn('P1', self.page())
        # 旧独立费率页重定向到工作台
        old = self.http.get(f'/employee-grades/{self.grade_id}/rates')
        self.assertEqual(old.status_code, 302)
        self.assertIn(f'grade_id={self.grade_id}', old.headers['Location'])


if __name__ == '__main__':
    unittest.main()
