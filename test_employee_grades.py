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
        self.assertIn('data-grade-panel=', page)
        self.assertIn('创建新版本', page)
        self.assertIn('v1', page)
        self.assertIn('2026-09-01', page)
        # 未选等级时自动选中第一个
        self.assertIn('P1', self.page())
        # 旧独立费率页重定向到工作台
        old = self.http.get(f'/employee-grades/{self.grade_id}/rates')
        self.assertEqual(old.status_code, 302)
        self.assertIn(f'grade_id={self.grade_id}', old.headers['Location'])

    # ------------------------------------------- ERP 化：页面内切换不得开新标签
    def test_grade_switching_is_in_page_and_opens_no_tab(self):
        """等级切换必须是页面内行为。

        工作区外壳（static/workspace.js）会把 iframe 里任何 <a href> 点击拦成「打开新标签」，
        所以等级条目只能是按钮 + data-grade-switch，页面里也不能再出现带 grade_id 的链接。
        """
        self.assign_grade(self.fixture.people['Submitter'])
        with self.m.app.app_context():
            db = self.m.db()
            db.execute("""insert into employee_grades (grade_name, description, is_active, created_at)
                          values ('P2','二档',1,?)""", (self.m.now(),))
            db.commit()
        page = self.page(self.grade_id)
        self.assertIn('class="erp-app"', page)
        self.assertEqual(page.count('data-grade-switch='), 2)
        self.assertEqual(page.count('data-grade-panel='), 2)
        self.assertIn('data-grade-url="/employee-grades?grade_id=', page)
        # 没有任何指向本页的 <a href>，否则每次点等级都会新开一个标签
        self.assertNotIn('href="/employee-grades?grade_id=', page)
        # 左侧等级树里的链接数为 0
        self.assertIn('<nav class="erp-nav', page)
        nav = page.split('<nav class="erp-nav', 1)[1].split('</nav>', 1)[0]
        self.assertNotIn('<a ', nav)
        self.assertIn('加入员工', page)
        self.assertIn('移出该等级', page)

    def test_selected_grade_query_param_is_honoured(self):
        """?grade_id=N 必须真的选中那个等级。

        等级切换是 history.replaceState 写地址栏（不导航），所以「刷新页面」和
        erp-report.js 的「局部刷新」（fetch(location.href)）都只能靠这个参数还原选中项；
        加入/移出成员后的 redirect(..., grade_id=...) 也依赖它回到原等级。
        早期 GET 分支是无参调用 render_employee_grades_page()，参数被静默丢掉 →
        刷新后总回到第一个等级，汇总栏与用户看到的等级还对不上。
        """
        with self.m.app.app_context():
            db = self.m.db()
            second = db.execute("""insert into employee_grades
                (grade_name, description, is_active, created_at) values ('P2','二档',1,?)""",
                (self.m.now(),)).lastrowid
            db.commit()

        def active_grade_id(html):
            tail = html.split('class="grade-panel active"', 1)[1]
            return tail.split('data-grade-panel="', 1)[1].split('"', 1)[0]

        self.assertEqual(active_grade_id(self.page(second)), str(second))
        self.assertIn('<strong data-grade-field="name">P2</strong>', self.page(second))
        # 不带参数时回退到第一个等级（保持既有行为）
        self.assertEqual(
            active_grade_id(self.http.get('/employee-grades').get_data(as_text=True)),
            str(self.grade_id),
        )
        # 加入成员后的跳转要带上刚才操作的等级，否则会「跳回第一个等级」
        response = self.http.post(
            f'/employee-grades/{second}/members',
            data={'action': 'add', 'user_id': str(self.fixture.people['Unrelated'])},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith(f'grade_id={second}'),
                        response.headers['Location'])

    # ------------------------------------------------------ 等级下的员工分配
    def test_add_and_remove_grade_members(self):
        submitter = self.fixture.people['Submitter']
        unused = self.fixture.people['Unrelated']
        page = self.page(self.grade_id)
        self.assertIn('未分配等级', page)

        added = self.http.post(
            f'/employee-grades/{self.grade_id}/members',
            data={'action': 'add', 'user_id': str(unused)},
        )
        self.assertEqual(added.status_code, 302)
        self.assertEqual(self.query('select employee_grade_id from users where id = ?', (unused,))[0]['employee_grade_id'],
                         self.grade_id)
        self.assertIn('已将 Unrelated 加入', self.page(self.grade_id))
        # 加入后不再出现在「可加入」下拉里
        self.assertNotIn(f'value="{unused}">Unrelated', self.page(self.grade_id))

        removed = self.http.post(
            f'/employee-grades/{self.grade_id}/members',
            data={'action': 'remove', 'user_id': str(unused)},
        )
        self.assertEqual(removed.status_code, 302)
        self.assertIsNone(self.query('select employee_grade_id from users where id = ?', (unused,))[0]['employee_grade_id'])
        self.assertIn('已将 Unrelated 移出', self.page(self.grade_id))

        # 重复加入 / 移出非本等级员工都只提示，不写库
        self.http.post(f'/employee-grades/{self.grade_id}/members', data={'action': 'remove', 'user_id': str(submitter)})
        self.assertIn('当前不属于', self.page(self.grade_id))
        self.http.post(f'/employee-grades/{self.grade_id}/members', data={'action': 'add', 'user_id': 'not-a-number'})
        self.assertIn('请先选择一名员工', self.page(self.grade_id))

    def test_members_update_requires_edit_permission(self):
        self.fixture.login('Submitter')  # employee 角色没有 employee_grades 权限
        response = self.http.post(
            f'/employee-grades/{self.grade_id}/members',
            data={'action': 'add', 'user_id': str(self.fixture.people['Unrelated'])},
        )
        self.assertEqual(response.status_code, 403)

    # -------------------------------------- 费率展示：版本优先 + 静态回退都要显示
    def test_rates_block_shows_static_fallback_then_version_rates(self):
        """没有版本时也必须显示费率（工资实际取的就是它），有版本时改显示版本费率。"""
        page = self.page(self.grade_id)
        self.assertIn('当前生效费率', page)
        self.assertIn('等级静态费率', page)
        self.assertIn('$10.00', page)          # 种子等级 standard_hourly_rate = 10
        self.assertIn('没有生效的费率版本', page)
        self.assertIn('还没有费率版本', page)

        self.add_version('2026-09-01', None, {'regular_hours': 25, 'mileage': 0.9})
        page = self.page(self.grade_id)
        self.assertIn('版本费率', page)
        self.assertIn('$25.00', page)
        self.assertIn('$0.90', page)
        self.assertIn('生效版本', page)
        # 版本里没给的条目仍回退静态费率，不能显示成 0
        self.assertIn('$20.00', page)          # overtime_hourly_rate 静态值


if __name__ == '__main__':
    unittest.main()
