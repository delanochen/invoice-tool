"""员工往来账筛选契约（v0.1.344 / v0.1.345）。

用户诉求：「员工往来账的表格增加筛选，员工、类型，更像 ERP 风格」；
v0.1.345 追加：「不要左边的类型选择，类型和员工和状态一样在筛选栏里选；表格里加上说明这一列」。

页面从「先选员工、select 带 required」改成 ERP 明细账范式：
- 默认列出全部员工（受 can_view_all_payments 权限约束）；
- 员工 / 类型 / 状态三个筛选并列放在筛选栏，全部走服务端精确匹配（各有「全部」选项）；
- 无「查看全部」权限时强制锁定本人 —— 改 URL 查询串也读不到别人的记录；
- 说明列：报销类付款单（source_type='expense'）的说明直接链到对应报销单。

回归要点（改这几处必须同步本文件）：
- employee_finance.employee_ledger 的 clauses 拼装与 payment_type / status 白名单；
- 计数口径：total / salary / expense 只套「员工+状态」，**不套类型**（v0.1.345：
  侧栏时代选中「工资」会让报销数变 0，看着像数据没了）；
- templates/employee_ledger.html 的筛选栏（employee_id / payment_type / status）与表格列；
- PAYMENT_TYPE_LABELS 的键集合（筛选栏选项就是它）。
"""
import re
import unittest

import tests_pg
from flask import Flask, g

from database import PostgreSQLConnection
from employee_finance import PAYMENT_TYPE_LABELS, register_employee_finance_routes


def _as_dicts(rows, columns):
    """把行统一成 dict，便于按列名断言。

    三种形态都要吃得下：
    - database.Row：生产与 PostgreSQLConnection 返回的类型，支持按键取值（dict(row) 即可）；
    - psycopg 元组行：直连 invoice_test 时的返回，需要按列名 zip；
    - 空结果：直接返回 []（不能按 rows[0] 判断形态，会 IndexError）。
    """
    rows = list(rows or [])
    if not rows:
        return []
    first = rows[0]
    if hasattr(first, "keys") or isinstance(first, dict):
        return [dict(row) for row in rows]
    return [dict(zip(columns, row)) for row in rows]


PAYMENT_COLUMNS = (
    "id", "payment_number", "employee_id", "payment_type", "source_type", "status",
    "currency", "gross_amount", "advance_offset", "other_adjustment", "net_amount",
    "created_at", "updated_at", "created_by", "employee_name",
)


def _app_connection():
    """包一层函数，让每个调用都拿到新连接（与 app.py 的 db() 同构）。

    必须用 database.PostgreSQLConnection 而不是裸 psycopg 连接：
    视图里的 SQL 写的是 SQLite 风格 `?` 占位符，translate_sql() 会把它
    转成 `%s` 并处理 `%` 转义。直接喂裸连接会报
    「the query has 0 placeholders but 1 parameters were passed」。
    注意它收的是连接串（不是连接对象），传 psycopg 连接会在
    conninfo 解析时报 'Connection' object has no attribute 'encode'。
    """
    tests_pg.activate()          # 确保 DATABASE_URL 指向 invoice_test
    return PostgreSQLConnection(tests_pg.database_url())


def _seed(conn):
    """建两个员工 + 各一张工资/报销付款单，供筛选断言使用。"""
    conn.execute(
        "insert into users(id,name,email,role,password_hash,is_active,created_at) "
        "values(1,'筛选甲','a@example.invalid','admin','x',1,'2026-01-01T00:00:00-06:00')"
    )
    conn.execute(
        "insert into users(id,name,email,role,password_hash,is_active,created_at) "
        "values(2,'筛选乙','b@example.invalid','employee','x',1,'2026-01-01T00:00:00-06:00')"
    )
    # 第 2 行是报销桥接过来的付款单（source_type='expense' + source_id），
    # 说明列要能点进报销单 —— 这里带上 description 供渲染断言。
    rows = [
        (1, 1, "salary", "SL-2609-0001", "1000.00", "manual", None, ""),
        (2, 1, "expense", "ER-2609-0002", "200.00", "expense", 7, "报销单 EX-2609-0007"),
        (3, 2, "salary", "SL-2609-0003", "3000.00", "manual", None, ""),
    ]
    for pid, employee_id, kind, number, amount, source_type, source_id, description in rows:
        conn.execute(
            "insert into employee_payment_orders"
            "(id,payment_number,employee_id,payment_type,source_type,source_id,description,status,currency,"
            " gross_amount,advance_offset,other_adjustment,net_amount,created_at,updated_at,created_by)"
            " values(%s,%s,%s,%s,%s,%s,%s,'draft','USD',%s,'0','0',%s,"
            "        '2026-09-20T10:00:00-05:00','2026-09-20T10:00:00-05:00',1)",
            (pid, number, employee_id, kind, source_type, source_id, description, amount, amount),
        )


class EmployeeLedgerFilterContractTest(unittest.TestCase):
    """页面渲染契约：筛选项、侧栏入口、表格列都要在。"""

    @classmethod
    def setUpClass(cls):
        cls.conn = tests_pg.connection()
        _seed(cls.conn)

    def setUp(self):
        self.app = Flask(__name__)
        # 模板要用到 app.py 注册的自定义过滤器（money / local_datetime 等），
        # 测试用的裸 Flask 实例没有它们，缺一个就 TemplateAssertionError。
        # 这里只补本页真正用到的两个；不动 app.py 的注册（那是生产侧的事）。
        self.app.jinja_env.filters["money"] = lambda value: "" if value is None else str(value)
        self.app.jinja_env.filters["local_datetime"] = lambda value: "" if value is None else str(value)
        self.app.jinja_env.globals.update(
            has_action_permission=lambda *a, **k: True,
            has_menu_permission=lambda *a, **k: True,
            app_version="test",
        )
        api = {
            "login_required": lambda fn: fn,
            "db": _app_connection,
            "now": lambda: "2026-09-30T10:00:00-05:00",
            "log_action": lambda *a, **k: None,
            "normalized_role": lambda: g.user["role"],
            "has_menu_permission": lambda *a, **k: True,
            "has_action_permission": lambda *a, **k: True,
            "can_manage_user_record": lambda *a, **k: True,
            "render_template": None,
        }
        register_employee_finance_routes(self.app, api)
        self.api = api
        self.client = self.app.test_client()

    def _get(self, role="admin", **params):
        with self.app.test_request_context():
            g.user = {"id": 1, "name": "筛选甲", "email": "a@example.invalid", "role": role}
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = "/finance/employee-ledger" + (f"?{query}" if query else "")
            return self.client.get(url)

    def _render(self, role="admin", **params):
        """直接调视图函数取上下文，绕开 Flask 模板渲染（测试环境没有真实模板引擎全局）。

        必须把参数拼进 request context 的路径里 —— test_request_context() 不带查询串时
        request.args 是空的，视图里的 employee_id / payment_type 一律读成 ""，
        筛选项看起来「没生效」其实是根本没传进去。
        """
        captured = {}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = "/finance/employee-ledger" + (f"?{query}" if query else "")
        # 替换 render_template：既要拿到上下文，又不想让 Jinja 因缺少自定义过滤器而失败
        import employee_finance
        original = employee_finance.render_template
        employee_finance.render_template = lambda name, **ctx: captured.update({"name": name, **ctx}) or ""
        try:
            with self.app.test_request_context(url):
                g.user = {"id": 1, "name": "筛选甲", "email": "a@example.invalid", "role": role}
                self.app.view_functions["employee_ledger"]()
        finally:
            employee_finance.render_template = original
        captured["payments"] = _as_dicts(captured.get("payments"), PAYMENT_COLUMNS)
        captured["advances"] = _as_dicts(captured.get("advances"), ())
        return captured

    def _render_page(self, role="admin", **params):
        """真渲染整页。

        整页渲染会走到 base.html，而 base.html 通过 url_for 引用 app_manifest 等
        只在 app.py 里注册的端点 —— 用临时 Flask 实例必然 BuildError。
        所以这里临时 import app 模块，借用它完整的 URL map 与 Jinja 环境，
        渲染完把 view_functions 还原，避免污染其它测试。
        """
        import app as real_app_module
        flask_app = real_app_module.app
        original = flask_app.view_functions.get("employee_ledger")
        flask_app.view_functions["employee_ledger"] = self.app.view_functions["employee_ledger"]
        try:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = "/finance/employee-ledger" + (f"?{query}" if query else "")
            with flask_app.test_request_context(url):
                g.user = {"id": 1, "name": "筛选甲", "email": "a@example.invalid", "role": role}
                return flask_app.view_functions["employee_ledger"]()
        finally:
            if original is not None:
                flask_app.view_functions["employee_ledger"] = original
            else:
                flask_app.view_functions.pop("employee_ledger", None)

    def test_default_lists_all_employees(self):
        ctx = self._render()
        self.assertEqual(ctx["name"], "employee_ledger.html")
        self.assertEqual(len(ctx["payments"]), 3)
        self.assertEqual(ctx["employee_id"], "")
        self.assertEqual(ctx["payment_type"], "")

    def test_employee_filter_narrows_to_one_employee(self):
        ctx = self._render(employee_id="2")
        self.assertEqual([row["payment_number"] for row in ctx["payments"]], ["SL-2609-0003"])

    def test_type_filter_accepts_each_business_type(self):
        for kind in PAYMENT_TYPE_LABELS:
            ctx = self._render(payment_type=kind)
            self.assertTrue(ctx["payments"], f"{kind} 应有记录")
            self.assertTrue(all(row["payment_type"] == kind for row in ctx["payments"]))

    def test_type_filter_combines_with_employee_filter(self):
        ctx = self._render(employee_id="1", payment_type="expense")
        self.assertEqual([row["payment_number"] for row in ctx["payments"]], ["ER-2609-0002"])

    def test_unknown_type_falls_back_to_all_instead_of_empty(self):
        """非法类型当作「全部」——值虽参数化，但筛空只会让人以为是没数据。"""
        ctx = self._render(payment_type="not_a_type")
        self.assertEqual(ctx["payment_type"], "")
        self.assertEqual(len(ctx["payments"]), 3)

    def test_employee_without_permission_is_locked_to_self(self):
        """无查看全部权限时，URL 里塞别人的 employee_id 必须被忽略（越权防护）。

        g.user id = 1（筛选甲），URL 传 employee_id=2（筛选乙）→ 必须仍只看到自己的单据。
        """
        ctx = self._render(role="employee", employee_id="2")
        self.assertEqual(ctx["employee_id"], "1")
        self.assertTrue(ctx["payments"])
        self.assertTrue(all(row["employee_id"] == 1 for row in ctx["payments"]))
        # 反向确认：筛选乙（id=2）的单据确实存在，只是不该被这个角色看到
        conn = tests_pg.connection()
        other = conn.execute(
            "select count(*) from employee_payment_orders where employee_id=2"
        ).fetchone()[0]
        self.assertGreater(other, 0)

    def test_salary_and_expense_counts_are_separated(self):
        ctx = self._render()
        salary = [row for row in ctx["payments"] if row["payment_type"] == "salary"]
        expense = [row for row in ctx["payments"] if row["payment_type"] == "expense"]
        self.assertEqual(len(salary), 2)
        self.assertEqual(len(expense), 1)

    def test_page_renders_filter_bar_and_table_headers(self):
        """真渲染一次，确认筛选栏三个下拉 / 表格列都在 HTML 里。"""
        body = self._render_page()
        self.assertIn("员工往来账", body)
        # v0.1.345：员工 / 类型 / 状态并列在筛选栏（类型不再走左侧 SidebarTree）
        for field in ("employee_id", "payment_type", "status"):
            self.assertIn(f'name="{field}"', body)
        self.assertIn("全部员工", body)
        for kind in PAYMENT_TYPE_LABELS:
            self.assertIn(kind, body)
        # v0.1.344 新增列：员工 / 其他调整 / 创建时间；v0.1.345 再加「说明」
        for header in ("付款单", "员工", "类型", "说明", "应付", "抵扣", "其他调整", "实付", "状态", "创建时间"):
            self.assertIn(header, body)
        self.assertNotIn("erp-nav-tree", body, "单据类型入口已挪进筛选栏，不应再渲染侧栏树")

    def test_status_filter_narrows_rows(self):
        """状态筛选与员工 / 类型同口径：服务端精确匹配，非法值回落「全部」。"""
        self.assertEqual(len(self._render(status="draft")["payments"]), 3)
        paid = self._render(status="paid")
        self.assertEqual(paid["payments"], [])
        self.assertEqual(paid["status"], "paid")
        unknown = self._render(status="not_a_status")
        self.assertEqual(unknown["status"], "")
        self.assertEqual(len(unknown["payments"]), 3)

    def test_filter_bar_keeps_selected_values(self):
        """筛选栏要回填当前条件，否则点「查询」后看起来什么都没选。"""
        body = self._render_page(employee_id="2", payment_type="salary", status="draft")
        self.assertIn('value="2" selected', body)
        self.assertIn('value="salary" selected', body)
        self.assertIn('value="draft" selected', body)

    def test_counts_ignore_type_filter(self):
        """工资 / 报销计数按「员工+状态」口径，选中某个类型时数字不能跟着变。

        v0.1.345：侧栏时代三个数字都从已过滤的 payments 里算，选中「工资」后
        「全部单据」= 工资数、「员工报销」= 0，用户以为数据没了。
        """
        base = self._render(employee_id="1")
        self.assertEqual(base["total_count"], 2)
        self.assertEqual(base["salary_count"], 1)
        self.assertEqual(base["expense_count"], 1)
        for kind in PAYMENT_TYPE_LABELS:
            ctx = self._render(employee_id="1", payment_type=kind)
            self.assertEqual(ctx["total_count"], 2, f"{kind} 视图下总数不应变")
            self.assertEqual(ctx["salary_count"], 1)
            self.assertEqual(ctx["expense_count"], 1)

    def test_expense_description_links_to_expense(self):
        """报销类付款单的说明直接链到对应报销单。"""
        body = self._render_page(payment_type="expense")
        self.assertRegex(
            body,
            r'<a href="[^"]*/expenses/7[^"]*">\s*报销单 EX-2609-0007\s*</a>',
        )
        # 工资单没有来源报销单，说明就是纯文本
        salary_body = self._render_page(payment_type="salary")
        self.assertNotIn("/expenses/7", salary_body)


if __name__ == "__main__":
    unittest.main()
