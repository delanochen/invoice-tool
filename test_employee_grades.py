"""员工等级工作台：删除/停用校验 + 工资按等级费率版本计算。

规则：
- 等级被员工使用时不能删除（员工停用与否均不可删）；未分配时可删，费率版本一并清理。
- 仍有在职员工使用时不能停用；员工全部停用后可以停用等级。
- 工资计算按工作日落在的有效期费率版本取价，没有版本时回退等级静态费率列。
- 费率只在「费率版本」页签按生效日期维护：「编辑等级」弹窗不再提交费率字段，
  因此保存等级必须保留库里的静态费率现值（不能按「缺省即 0」清零）；
  「按当前费率建版本」把当前生效费率原样固化成一条版本。
"""
import importlib.util
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import test_expense_on_behalf as fixture

ROOT = Path(__file__).resolve().parent

RATE_FIELDS = (
    'standard_hourly_rate',
    'transport_hourly_rate',
    'overtime_hourly_rate',
    'holiday_hourly_rate',
    'car_mileage_rate',
    'rental_driving_hourly_rate',
)
# 费率版本表单里的 name 用的是费率条目名（rate_type），不是等级表的静态费率列名。
EMPLOYEE_RATE_FIELDS = (
    'regular_hours',
    'overtime_hours',
    'holiday_hours',
    'travel_hours',
    'public_transport_hours',
    'mileage',
    'rental_drive_hours',
)


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
            next_no = db.execute(
                'select coalesce(max(version_no), 0) + 1 as value from employee_rate_versions where employee_grade_id = ?',
                (self.grade_id,),
            ).fetchone()[0]
            version_id = db.execute(
                """insert into employee_rate_versions
                   (employee_grade_id, version_no, effective_from, effective_to, status, notes, created_by, created_at, updated_at)
                   values (?, ?, ?, ?, 'active', '', ?, ?, ?)""",
                (self.grade_id, next_no, effective_from, effective_to, self.fixture.people['Manager'],
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
        # v0.1.324 起 ERP 风格不再渲染成功类 flash（状态变化在页面上直接可见）
        self.assertNotIn('员工等级已停用', self.page(self.grade_id))
        self.assertEqual(self.query(
            'select is_active from employee_grades where id = ?', (self.grade_id,))[0]['is_active'], 0)

    def test_reactivate_grade(self):
        self.http.post(f'/employee-grades/{self.grade_id}/state', data={'is_active': '0'})
        self.http.post(f'/employee-grades/{self.grade_id}/state', data={'is_active': '1'})
        self.assertNotIn('员工等级已启用', self.page(self.grade_id))
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
        # v0.1.324 起 ERP 风格不再渲染成功类 flash（删除结果由列表状态直接可见）
        self.assertNotIn('员工等级已删除', self.page())
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
        # 汇总工资条跨两个费率时显示实际加权费率：60 / 3 小时 = 20，
        # 不再错误显示 employee_grades 静态列里的 10。
        self.assertAlmostEqual(row['standard_rate'], 20)
        # 09-01 里程 10 英里 × 版本 0.9（不是静态 0.5）
        self.assertAlmostEqual(row['self_drive_allowance'], 9)
        self.assertAlmostEqual(row['car_mileage_rate'], 0.9)
        self.assertAlmostEqual(row['total_pay'], 100 + 60 + 9)

    def test_payslip_displays_effective_overtime_rate_used_for_amount(self):
        self.assign_grade(self.fixture.people['Submitter'])
        self.add_version('2026-09-01', None, {
            'regular_hours': 35, 'overtime_hours': 52.5, 'holiday_hours': 52.5,
            'travel_hours': 7, 'public_transport_hours': 7, 'mileage': 0.5,
            'rental_drive_hours': 15,
        })
        rows = [dict(
            report_id=1, worker_id=self.fixture.people['Submitter'], worker_name='Submitter',
            grade_name='P1', base_salary=0, meal_daily_amount=0, car_allowance_method='mileage',
            car_mileage_rate=0.5, rental_driving_hourly_rate=15, standard_hourly_rate=35,
            transport_hourly_rate=7, overtime_hourly_rate=42.5, holiday_hourly_rate=42.5,
            attendance_date='2026-09-01', service_order_id=self.fixture.order,
            order_number='SO-DELEGATE', client_name='Site', worker_travel_mode='self_drive',
            worker_travel_hours=0, worker_driving_miles=0,
            standard_hours=38, transport_hours=0, overtime_hours=4, holiday_hours=0,
        )]
        with patch.object(self.m, 'labor_report_entries', return_value=rows):
            with self.m.app.app_context():
                batch = self.m.payroll_rows_for_range(
                    date(2026, 9, 1), date(2026, 9, 14), date(2026, 9, 28), ''
                )
                row = batch['rows'][0]
                payslip = self.m.payroll_payslip_payload(row)

        self.assertAlmostEqual(row['overtime_pay'], 210)
        self.assertAlmostEqual(row['overtime_rate'], 52.5)
        self.assertAlmostEqual(row['holiday_rate'], 0)
        overtime_line = next(line for line in payslip['lines'] if line['label'] == '加班工资')
        holiday_line = next(line for line in payslip['lines'] if line['label'] == '假期工资')
        self.assertEqual(overtime_line['rate'], 52.5)
        self.assertEqual(overtime_line['amount'], 210)
        self.assertEqual(holiday_line['rate'], 0)

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
        self.assertIn('新增版本', page)
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
        # 数元素、不数全文：页内脚本里也有 [data-grade-switch="…"] / [data-grade-panel="…"]
        # 这类选择器字面量，直接 count 会把脚本算进来（脚本里多写一个选择器就假失败）。
        markup = re.sub(r'<script\b.*?</script>', '', page, flags=re.S)
        self.assertEqual(markup.count('data-grade-switch='), 2)
        self.assertEqual(markup.count('data-grade-panel='), 2)
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
        # v0.1.324 起 ERP 风格不再渲染成功类 flash（操作结果在页面上直接可见）
        self.assertNotIn('已将 Unrelated 加入', self.page(self.grade_id))
        # 加入后不再出现在「可加入」下拉里
        self.assertNotIn(f'value="{unused}">Unrelated', self.page(self.grade_id))

        removed = self.http.post(
            f'/employee-grades/{self.grade_id}/members',
            data={'action': 'remove', 'user_id': str(unused)},
        )
        self.assertEqual(removed.status_code, 302)
        self.assertIsNone(self.query('select employee_grade_id from users where id = ?', (unused,))[0]['employee_grade_id'])
        self.assertNotIn('已将 Unrelated 移出', self.page(self.grade_id))

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

    # --------------------------------- 员工分配：局部刷新（不整页跳转 / 不整页白一下）
    def test_member_add_ajax_returns_only_fragments(self):
        """「加入该等级」的 XHR 提交只回片段：成员行 / 候选下拉 / 计数，不带外壳。

        整页 302 会让工作区 iframe 白一下重画，用户看到的就是「整个页面都刷新」。
        所以页面上的加入/移出改走 XHR；普通表单提交仍必须是 302（上一条用例钉着）。
        """
        unused = self.fixture.people['Unrelated']
        response = self.http.post(
            f'/employee-grades/{self.grade_id}/members',
            data={'action': 'add', 'user_id': str(unused)},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['ok'])
        self.assertIn('已将 Unrelated 加入', payload['message'])
        self.assertEqual([panel['id'] for panel in payload['panels']], [self.grade_id])
        panel = payload['panels'][0]
        self.assertEqual(panel['total'], 1)
        self.assertEqual(panel['headcount'], '1 名（在职 1）')
        self.assertEqual(panel['headline'], '1 名员工（在职 1）')
        # 只回「被替换的那一小块」：带 <table>/外壳就变成变相整页替换，网格会被重建
        self.assertNotIn('<table', panel['rows_html'])
        self.assertNotIn('erp-toolbar', panel['rows_html'])
        self.assertNotIn('<script', panel['rows_html'])
        # 新成员进了成员表，且从候选里消失 —— 否则下拉里还能再把同一个人选一次
        self.assertIn('<td>Unrelated</td>', panel['rows_html'])
        self.assertNotIn(f'value="{unused}"', panel['options_html'])
        self.assertIn('<option value="">请选择员工…</option>', panel['options_html'])
        # 未分配数：加入后少一个，前端状态栏/左侧树直接用它
        self.assertEqual(payload['unassigned'], self.query(
            'select count(*) as total from users where employee_grade_id is null')[0]['total'])
        self.assertIn('名员工未分配等级', payload['unassigned_text'])
        # XHR 不写 flash：写了会积在 session 里，直到下一次整页刷新才莫名其妙冒出来
        self.assertNotIn('已将 Unrelated 加入', self.page(self.grade_id))

    def test_member_transfer_returns_fragments_for_both_grades(self):
        """把员工从等级 A 改挂到等级 B：A 与 B 的片段都要回。

        页面里所有等级同处一页，只刷目标等级的话，用户切回 A 会看到
        「成员表里还挂着他、候选下拉里又能把他加进来」的旧数据。
        """
        submitter = self.fixture.people['Submitter']
        self.assign_grade(submitter)
        with self.m.app.app_context():
            db = self.m.db()
            second = db.execute(
                """insert into employee_grades (grade_name, description, is_active, created_at)
                   values ('P2','二档',1,?)""", (self.m.now(),)).lastrowid
            db.commit()

        payload = self.http.post(
            f'/employee-grades/{second}/members',
            data={'action': 'add', 'user_id': str(submitter)},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        ).get_json()
        panels = {panel['id']: panel for panel in payload['panels']}
        self.assertEqual(set(panels), {self.grade_id, second})
        # 新等级：人进了成员表、候选里没有他
        self.assertIn('<td>Submitter</td>', panels[second]['rows_html'])
        self.assertNotIn(f'value="{submitter}"', panels[second]['options_html'])
        # 原等级：人出了成员表（回退到空态行）、候选里能把他加回来
        self.assertNotIn('<td>Submitter</td>', panels[self.grade_id]['rows_html'])
        self.assertIn('该等级下还没有员工', panels[self.grade_id]['rows_html'])
        self.assertIn(f'value="{submitter}"', panels[self.grade_id]['options_html'])
        self.assertEqual(panels[self.grade_id]['total'], 0)
        self.assertEqual(panels[second]['total'], 1)

    def test_member_remove_ajax_returns_fragments(self):
        """「移出该等级」同样只回片段（这一条同时钉住移除路径的文案与空态）。"""
        submitter = self.fixture.people['Submitter']
        self.assign_grade(submitter)
        payload = self.http.post(
            f'/employee-grades/{self.grade_id}/members',
            data={'action': 'remove', 'user_id': str(submitter)},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        ).get_json()
        self.assertTrue(payload['ok'])
        self.assertIn('已将 Submitter 移出', payload['message'])
        panel = payload['panels'][0]
        self.assertEqual(panel['total'], 0)
        self.assertIn('该等级下还没有员工', panel['rows_html'])
        self.assertIn(f'value="{submitter}"', panel['options_html'])
        self.assertIsNone(self.query(
            'select employee_grade_id from users where id = ?', (submitter,))[0]['employee_grade_id'])

    def test_member_ajax_error_only_returns_message(self):
        """XHR 出错只回消息、不回片段：库里什么都没变，前端没理由去改 DOM。"""
        for data, expected in (
            ({'action': 'add', 'user_id': 'not-a-number'}, '请先选择一名员工'),
            ({'action': 'remove', 'user_id': str(self.fixture.people['Submitter'])}, '当前不属于'),  # 移出非本等级员工
        ):
            with self.subTest(expected=expected):
                response = self.http.post(
                    f'/employee-grades/{self.grade_id}/members', data=data,
                    headers={'X-Requested-With': 'XMLHttpRequest'},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertFalse(payload['ok'])
                self.assertIn(expected, payload['message'])
                self.assertNotIn('panels', payload)
                self.assertNotIn('已将', self.page(self.grade_id))   # 出错不写 flash

    def test_member_ajax_endpoint_is_read_from_attribute(self):
        """XHR 端点必须用 getAttribute('action') 读，**不能**用 form.action。

        表单里有 <input name="action" value="add">（还有 name="user_id"），表单控件的命名
        访问会遮蔽 HTMLFormElement.action —— form.action 拿到的是那个 <input> 元素，
        fetch 会把它当相对 URL（实际请求 /[object HTMLInputElement]），必然失败并落进
        「退回整页提交」的兜底分支，症状与改造前一模一样：点一下整个页面刷新。
        兜底提交同理不能用 form.submit()（会被 name="submit" 的控件遮蔽）。
        """
        page = self.page(self.grade_id)
        self.assertIn('name="action"', page)              # 遮蔽的来源就在页面上
        # 断言只作用于「员工分配局部刷新」那一段脚本：整页里 base.html 的语言切换器本来就有
        # onchange="this.form.submit()"，对整份 HTML 做字符串否定会被这段无关代码误伤
        # （踩过：断言报 form.submit() unexpectedly found，实际找到的是语言切换器）。
        blocks = [body for body in re.findall(r'<script\b[^>]*>(.*?)</script>', page, flags=re.S)
                  if 'submitMemberForm' in body]
        self.assertEqual(len(blocks), 1, '应当恰好有一段负责员工分配局部刷新的脚本')
        # 剥掉行首 // 注释再断言：注释里就是拿这两个写法当反面教材的（本仓约定注释即契约）。
        code = '\n'.join(line for line in blocks[0].splitlines()
                         if not line.lstrip().startswith('//'))
        self.assertIn("form.getAttribute('action')", code)
        self.assertIn('HTMLFormElement.prototype.submit.call(form)', code)
        self.assertNotIn('fetch(form.action', code)
        self.assertNotIn('form.submit()', code)

    # -------------------------------------- 费率展示：直接管理版本
    def test_rates_block_shows_static_fallback_then_version_rates(self):
        """费率页不再展示独立当前费率块，只展示版本和新增入口。"""
        page = self.page(self.grade_id)
        self.assertNotIn('当前生效费率（', page)
        self.assertIn('新增版本', page)
        self.assertIn('还没有费率版本', page)

        self.add_version('2026-09-01', None, {'regular_hours': 25, 'mileage': 0.9})
        page = self.page(self.grade_id)
        self.assertIn('$25.00', page)
        self.assertIn('$0.90', page)
        self.assertIn('当前生效', page)
        self.assertIn('复制', page)
        self.assertIn('删除', page)

    # --------------------------- 费率只在「费率版本」里维护（弹窗不再有费率字段）
    def edit_dialog_html(self):
        page = self.page(self.grade_id)
        return page.split(f'id="editGradeDialog{self.grade_id}"', 1)[1].split('</dialog>', 1)[0]

    def test_edit_grade_dialog_no_longer_carries_rate_fields(self):
        """「编辑等级」弹窗只维护非费率资料，费率统一在「费率版本」页签维护。

        同一口径留两个可写入口时，在这里改费率既不生成版本、工资取的仍是
        「版本优先 / 静态兜底」那一套，用户会以为改了没生效。
        """
        dialog = self.edit_dialog_html()
        # 弹窗里既不能再有静态费率列，也不能出现任何费率条目输入
        for field in (*RATE_FIELDS, *EMPLOYEE_RATE_FIELDS):
            self.assertNotIn(f'name="{field}"', dialog, field)
        for label in ('标准时薪', '交通时薪', '加班时薪', '假期时薪', '车补里程单价', '租车驾驶补贴/小时'):
            self.assertNotIn(label, dialog, label)
        self.assertIn('name="base_salary"', dialog)
        self.assertIn('name="meal_daily_amount"', dialog)
        self.assertIn('name="car_allowance_method"', dialog)

        # 费率入口统一在「费率版本」页签的新增弹窗，不再另放当前费率/一键固化板块
        page = self.page(self.grade_id)
        for field in EMPLOYEE_RATE_FIELDS:
            self.assertIn(f'name="{field}"', page, field)
        self.assertIn(f'id="newRateVersionDialog{self.grade_id}"', page)
        self.assertIn('创建版本', page)
        self.assertNotIn('name="rate_source" value="current"', page)
        self.assertNotIn('按当前费率建版本', page)

    def test_saving_grade_without_rate_fields_keeps_static_rates(self):
        """保存等级必须保留库里已有的静态费率。

        弹窗不再提交费率字段后，若沿用 to_float(None) == 0 的「缺省即 0」写法，
        一次保存就会把等级静态费率全部清零 —— 而这正是所有「没有费率版本」的等级
        （含全部历史数据）计算工资的兜底值，等于静默把工资算成 0。
        """
        before = self.query('select * from employee_grades where id = ?', (self.grade_id,))[0]
        response = self.http.post('/employee-grades', data={
            'grade_id': str(self.grade_id),
            'grade_name': 'P1 改',
            'description': '改了描述',
            'base_salary': '120',
            'meal_daily_amount': '8',
            'car_allowance_method': 'mileage',
        })
        self.assertEqual(response.status_code, 302)
        after = self.query('select * from employee_grades where id = ?', (self.grade_id,))[0]
        self.assertEqual(after['grade_name'], 'P1 改')
        self.assertEqual(after['base_salary'], 120)
        self.assertEqual(after['meal_daily_amount'], 8)
        self.assertEqual(after['car_allowance_method'], 'mileage')
        for column in RATE_FIELDS:
            self.assertEqual(after[column], before[column], column)

    def test_new_grade_rate_columns_default_to_null(self):
        """新建等级时不填费率 → 静态费率列落 NULL，不是 0。

        以前缺省写 0（里程单价还默认 0.5、租车驾驶默认 15），于是「没填」和
        「明确填 0」在库里长得一模一样，下游只能靠 rate <= 0 猜有没有维护，
        结果把明确维护成 0 的费率（例：W1 外籍员工不拿交通补贴）也当成缺费率。
        """
        response = self.http.post('/employee-grades', data={
            'grade_name': 'NEW-NULL',
            'description': '',
            'base_salary': '0',
            'meal_daily_amount': '0',
            'car_allowance_method': 'mileage',
        })
        self.assertEqual(response.status_code, 302)
        row = self.query("select * from employee_grades where grade_name = 'NEW-NULL'")[0]
        for column in RATE_FIELDS:
            self.assertIsNone(row[column], column)
        # 基本工资 / 餐补不是费率，仍然按 0 存
        self.assertEqual(row['base_salary'], 0)
        self.assertEqual(row['meal_daily_amount'], 0)

    def test_explicit_zero_rate_is_stored_as_zero(self):
        """表单里明确填 0 的费率必须存成 0（不是 NULL）—— 0 是「维护成 0」。"""
        response = self.http.post('/employee-grades', data={
            'grade_name': 'NEW-ZERO',
            'description': '',
            'base_salary': '0',
            'meal_daily_amount': '0',
            'car_allowance_method': 'mileage',
            'standard_hourly_rate': '0',
            'transport_hourly_rate': '0',
            'overtime_hourly_rate': '0',
            'holiday_hourly_rate': '0',
            'car_mileage_rate': '0',
            'rental_driving_hourly_rate': '0',
        })
        self.assertEqual(response.status_code, 302)
        row = self.query("select * from employee_grades where grade_name = 'NEW-ZERO'")[0]
        for column in RATE_FIELDS:
            self.assertEqual(row[column], 0, column)

    def version_items(self, version_id):
        return {row['rate_type']: row['rate'] for row in self.query(
            'select * from employee_rate_items where version_id = ?', (version_id,))}

    def test_one_click_version_freezes_current_static_rates(self):
        """没有版本时，「按当前费率建版本」把等级静态费率原样固化成 v1。"""
        today = date.today().isoformat()
        response = self.http.post(
            f'/employee-grades/{self.grade_id}/rates',
            data={'rate_source': 'current', 'effective_from': today},
        )
        self.assertEqual(response.status_code, 302)
        versions = self.query(
            'select * from employee_rate_versions where employee_grade_id = ?', (self.grade_id,))
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]['version_no'], 1)
        self.assertEqual(versions[0]['effective_from'], today)
        self.assertIn('沿用', versions[0]['notes'])
        items = self.version_items(versions[0]['id'])
        self.assertEqual(items['regular_hours'], 10)
        self.assertEqual(items['overtime_hours'], 20)
        self.assertEqual(items['holiday_hours'], 30)
        self.assertEqual(items['travel_hours'], 5)
        self.assertEqual(items['public_transport_hours'], 5)
        self.assertEqual(items['mileage'], 0.5)
        self.assertEqual(items['rental_drive_hours'], 15)
        # 固化后费率来源就该是版本了
        self.assertIn('生效版本', self.page(self.grade_id))

    def test_one_click_version_copies_live_rates_before_closing_previous(self):
        """一键固化取的必须是页面上那份「当前生效费率」，不能是被收尾后的静态兜底值。

        _prepare_new_version_range 会先把上一个版本的结束日期收到新版本生效日前一天；
        若快照在那之后才取，按「今天」就查不到生效版本，会静默退化成静态费率 ——
        固化的就不是用户屏幕上看到的那份费率。
        """
        start = (date.today() - timedelta(days=30)).isoformat()
        self.add_version(start, None, {'regular_hours': 25})
        self.http.post(
            f'/employee-grades/{self.grade_id}/rates',
            data={'rate_source': 'current', 'effective_from': date.today().isoformat()},
        )
        versions = self.query(
            'select * from employee_rate_versions where employee_grade_id = ? order by version_no',
            (self.grade_id,))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]['effective_to'], (date.today() - timedelta(days=1)).isoformat())
        items = self.version_items(versions[1]['id'])
        self.assertEqual(items['regular_hours'], 25)     # 版本费率，不是静态的 10
        self.assertEqual(items['overtime_hours'], 20)    # 版本里没给 → 回退静态兜底

    def _refuse_one_click(self, grade_id):
        """按当前费率建版本 → 期望被拒绝，返回 (版本数, 页面 HTML)。"""
        self.http.post(
            f'/employee-grades/{grade_id}/rates',
            data={'rate_source': 'current', 'effective_from': date.today().isoformat()},
        )
        count = self.query(
            'select count(*) as n from employee_rate_versions where employee_grade_id = ?',
            (grade_id,))[0]['n']
        return count, self.page(grade_id)

    def test_one_click_version_refused_when_rates_not_maintained(self):
        """7 项费率一项都没维护（静态列为 NULL）时拒绝固化。

        v0.1.341 起静态费率列默认 NULL 而不是 0：NULL = 从没维护过，所以这里的提示
        是「既没有生效版本也没有等级静态值」，不是原来那句「全是 0」。
        """
        with self.m.app.app_context():
            db = self.m.db()
            blank = db.execute(
                """insert into employee_grades (grade_name, description, is_active, created_at)
                   values ('Blank','',1,?)""",
                (self.m.now(),),
            ).lastrowid
            db.commit()
        count, page = self._refuse_one_click(blank)
        self.assertEqual(count, 0)
        self.assertIn('既没有生效版本也没有等级静态值', page)
        # 等级资料里静态费率显示成「未设置」，而不是 $0.00
        self.assertIn('未设置', page)
        self.assertNotIn('全是 0', page)

    def test_one_click_version_refused_when_rates_all_zero(self):
        """7 项费率被**明确维护成 0** 时同样拒绝固化 —— 与「没维护」是两回事。

        固化出一条全 0 的版本比「没有版本」更危险：没有版本时页面会明确提示
        「未设置」，而全 0 版本看起来是「已配置」，会静默把工资算成 0。
        """
        with self.m.app.app_context():
            db = self.m.db()
            blank = db.execute(
                """insert into employee_grades
                   (grade_name, description, car_mileage_rate, rental_driving_hourly_rate,
                    standard_hourly_rate, transport_hourly_rate, overtime_hourly_rate,
                    holiday_hourly_rate, car_hourly_rate, is_active, created_at)
                   values ('Blank','',0,0,0,0,0,0,0,1,?)""",
                (self.m.now(),),
            ).lastrowid
            db.commit()
        count, page = self._refuse_one_click(blank)
        self.assertEqual(count, 0)
        self.assertIn('全是 0', page)

    def test_manual_version_creation_still_works(self):
        """手动填 7 项费率的路径不受一键固化改造影响。"""
        response = self.http.post(f'/employee-grades/{self.grade_id}/rates', data={
            'effective_from': '2026-09-01',
            'regular_hours': '31', 'overtime_hours': '0', 'holiday_hours': '0',
            'travel_hours': '0', 'public_transport_hours': '0', 'mileage': '1.2',
            'rental_drive_hours': '0', 'notes': '手填',
        })
        self.assertEqual(response.status_code, 302)
        versions = self.query(
            'select * from employee_rate_versions where employee_grade_id = ?', (self.grade_id,))
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]['notes'], '手填')
        items = self.version_items(versions[0]['id'])
        self.assertEqual(items['regular_hours'], 31)
        self.assertEqual(items['mileage'], 1.2)
        self.assertEqual(items['overtime_hours'], 0)

    def test_version_can_be_copied_with_new_effective_dates(self):
        source_id = self.add_version('2026-01-01', '2026-06-30', {
            'regular_hours': 37, 'overtime_hours': 51, 'mileage': 1.15,
        })
        response = self.http.post(f'/employee-grades/{self.grade_id}/rates', data={
            'copy_version_id': str(source_id),
            'effective_from': '2026-07-01',
            'effective_to': '',
            'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        versions = self.query(
            'select * from employee_rate_versions where employee_grade_id=? order by version_no',
            (self.grade_id,),
        )
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[1]['notes'], '复制自 v1')
        copied = self.version_items(versions[1]['id'])
        self.assertEqual(copied['regular_hours'], 37)
        self.assertEqual(copied['overtime_hours'], 51)
        self.assertEqual(copied['mileage'], 1.15)

    def test_version_can_be_deleted_with_its_items(self):
        version_id = self.add_version('2026-01-01', '2026-01-31', {'regular_hours': 25})
        response = self.http.post(
            f'/employee-grades/{self.grade_id}/rates/{version_id}/delete'
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.query(
            'select count(*) as n from employee_rate_versions where id=?', (version_id,)
        )[0]['n'], 0)
        self.assertEqual(self.query(
            'select count(*) as n from employee_rate_items where version_id=?', (version_id,)
        )[0]['n'], 0)

    def test_rate_tab_is_version_first(self):
        version_id = self.add_version('2026-01-01', '2026-01-31', {'regular_hours': 25})
        page = self.page(self.grade_id)
        self.assertNotIn('当前生效费率（', page)
        self.assertNotIn('按当前费率建版本', page)
        self.assertNotIn('手动指定费率（新建版本）', page)
        self.assertIn(f'copyRateVersionDialog{version_id}', page)
        self.assertIn(f'/rates/{version_id}/delete', page)

    # ------------------------------------------- 费率版本可编辑（历史版本除外）
    def edit_version(self, version_id, **overrides):
        data = {
            'effective_from': '2026-10-01', 'effective_to': '',
            'regular_hours': '26', 'overtime_hours': '41', 'holiday_hours': '61',
            'travel_hours': '8', 'public_transport_hours': '6', 'mileage': '0.95',
            'rental_drive_hours': '19', 'notes': '修正',
        }
        data.update(overrides)
        return self.http.post(f'/employee-grades/{self.grade_id}/rates/{version_id}/edit', data=data)

    def test_edit_version_updates_dates_rates_and_notes(self):
        version_id = self.add_version('2026-10-01', None, {'regular_hours': 25, 'mileage': 0.9})
        response = self.edit_version(version_id, effective_from='2026-10-05', effective_to='2027-09-30')
        self.assertEqual(response.status_code, 302)
        version = self.query('select * from employee_rate_versions where id = ?', (version_id,))[0]
        self.assertEqual(version['effective_from'], '2026-10-05')
        self.assertEqual(version['effective_to'], '2027-09-30')
        self.assertEqual(version['notes'], '修正')
        items = self.version_items(version_id)
        self.assertEqual(items['regular_hours'], 26)
        self.assertEqual(items['mileage'], 0.95)
        # 页面上能看到「编辑」按钮与对应弹窗
        page = self.page(self.grade_id)
        self.assertIn(f'data-dialog-open="editRateVersionDialog{version_id}"', page)

    def test_edit_version_payroll_lookup_follows_new_values(self):
        """编辑版本后，工资取价要按修正后的费率/有效期走。"""
        version_id = self.add_version('2026-09-01', None, {'regular_hours': 25, 'mileage': 0.9})
        # 默认编辑表单会把生效期改到 10-01（未来），这里明确保持 09-01 起生效
        self.edit_version(version_id, effective_from='2026-09-01', regular_hours='40')
        with self.m.app.app_context():
            snapshot = self.m.employee_grade_rate_snapshot(self.m.db(), self.grade_id, date.today().isoformat())
        self.assertEqual(snapshot['version']['id'], version_id)
        self.assertEqual(snapshot['rates']['regular_hours']['rate'], 40)

    def test_edit_rejects_overlapping_range_and_keeps_old_values(self):
        first = self.add_version('2026-01-01', '2026-06-30', {'regular_hours': 25})
        second = self.add_version('2026-07-01', None, {'regular_hours': 30})
        # 把 v1 改成与 v2 重叠 → 拒绝，数据不动
        self.edit_version(first, effective_to='2026-12-31')
        self.assertIn('重叠', self.page(self.grade_id))
        version = self.query('select * from employee_rate_versions where id = ?', (first,))[0]
        self.assertEqual(version['effective_to'], '2026-06-30')
        self.assertEqual(self.version_items(first)['regular_hours'], 25)
        self.assertEqual(self.version_items(second)['regular_hours'], 30)

    def test_historical_version_is_read_only(self):
        """有效期已整体结束的版本不允许编辑/停用，页面上也不出按钮。"""
        past = self.add_version('2026-01-01', '2026-01-31', {'regular_hours': 25})
        before = self.query('select * from employee_rate_versions where id = ?', (past,))[0]
        self.edit_version(past)
        self.assertIn('历史版本，不允许修改', self.page(self.grade_id))
        self.http.post(f'/employee-grades/{self.grade_id}/rates/{past}/state', data={'target': 'inactive'})
        self.assertIn('历史版本，不允许修改', self.page(self.grade_id))
        after = self.query('select * from employee_rate_versions where id = ?', (past,))[0]
        for column in ('effective_from', 'effective_to', 'notes', 'status'):
            self.assertEqual(after[column], before[column], column)
        # 页面上历史版本没有「编辑」按钮，当前版本有
        current = self.add_version(date.today().isoformat(), None, {'regular_hours': 30})
        page = self.page(self.grade_id)
        self.assertNotIn(f'editRateVersionDialog{past}"', page)
        self.assertIn(f'editRateVersionDialog{current}"', page)

    def test_deactivate_then_reactivate_version(self):
        version_id = self.add_version(date.today().isoformat(), None, {'regular_hours': 25})
        self.http.post(f'/employee-grades/{self.grade_id}/rates/{version_id}/state', data={'target': 'inactive'})
        version = self.query('select * from employee_rate_versions where id = ?', (version_id,))[0]
        self.assertEqual(version['status'], 'inactive')
        # 停用后取价退回静态费率
        with self.m.app.app_context():
            snapshot = self.m.employee_grade_rate_snapshot(self.m.db(), self.grade_id, date.today().isoformat())
        self.assertIsNone(snapshot['version'])
        # 重新启用成功；再插一条重叠版本后，重新启用被拦
        self.http.post(f'/employee-grades/{self.grade_id}/rates/{version_id}/state', data={'target': 'active'})
        self.assertEqual(self.query('select status from employee_rate_versions where id = ?', (version_id,))[0]['status'], 'active')
        self.http.post(f'/employee-grades/{self.grade_id}/rates/{version_id}/state', data={'target': 'inactive'})
        self.add_version('2026-01-01', None, {'regular_hours': 99})
        self.http.post(f'/employee-grades/{self.grade_id}/rates/{version_id}/state', data={'target': 'active'})
        self.assertIn('重叠', self.page(self.grade_id))
        self.assertEqual(self.query('select status from employee_rate_versions where id = ?', (version_id,))[0]['status'], 'inactive')

    def test_page_warns_about_existing_overlapping_versions(self):
        """重叠校验上线前的存量重叠版本要在页面上亮出来。"""
        self.add_version('2026-01-01', None, {'regular_hours': 25})
        self.add_version('2026-03-01', None, {'regular_hours': 30})
        page = self.page(self.grade_id)
        self.assertIn('有效期互相重叠', page)
        self.assertIn('请点对应版本的「编辑」修正', page)


if __name__ == '__main__':
    unittest.main()
