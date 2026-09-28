"""工单列表页「报销」列。

需求：列位置紧跟「日报」；值是该工单所有报销单的明细条目数之和（和「日报」同为条数口径）。
这里按渲染结果断言（真跑 /service-orders），而不是只断言模板源码字符串，
避免「模板写了但后端没给出 expense_item_count」这类只改一半的回归。
"""

import importlib.util
import re
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


def _strip_tags(html):
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ").strip()


def _table(html):
    """取出原生表格的 thead 标题与 tbody 各行文本。"""
    start = html.index('class="list-table erp-grid service-orders-table"')
    body = html[start:]
    body = body[: body.index("</table>")]
    thead = body[body.index("<thead>") : body.index("</thead>")]
    tbody = body[body.index("<tbody>") : body.index("</tbody>")]
    headers = [_strip_tags(cell) for cell in re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)]
    rows = [
        [_strip_tags(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.S)
    ]
    return headers, rows


def _row_with(rows, needle):
    for row in rows:
        if any(needle in cell for cell in row):
            return row
    raise AssertionError(f"没找到包含 {needle} 的行")


def _summary_value(html, label):
    """从 SummaryBar 里取某个统计项的值。"""
    for block in re.findall(r'<div class="erp-summary-item[^"]*">(.*?)</div>', html, re.S):
        spans = re.findall(r"<span>(.*?)</span>", block, re.S)
        strongs = re.findall(r"<strong>(.*?)</strong>", block, re.S)
        if spans and strongs and spans[0].strip() == label:
            return strongs[0].strip()
    raise AssertionError(f"SummaryBar 里没有 {label}")


class ServiceOrdersExpenseColumnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_expense_column_test_app", module_path
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            for table in (
                "expense_items",
                "expenses",
                "service_orders",
                "clients",
                "projects",
                "audit_logs",
                "users",
            ):
                connection.execute(f"delete from {table}")
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, created_at)
                values ('99998', 'Expense Column Client', 'EC Client', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.admin_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', 1, '2026-08-13T00:00:00')
                """
            ).lastrowid
            # expenses.view 的默认角色不含外部角色 → 用来验证列按权限收口。
            # 外部经理只能看自己客户下的工单，所以要绑 client_id，否则后端直接 1 = 0、一行都没有。
            self.external_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, client_id, is_active, created_at)
                values ('External', 'ext@example.com', 'unused', 'external_manager', ?, 1, '2026-08-13T00:00:00')
                """,
                (self.client_id,),
            ).lastrowid
            self.project_id = connection.execute(
                """
                insert into projects (name, created_at)
                values ('Expense Column Project', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.rich_order_id = self._insert_order(connection, "SO-EXPENSE")
            self.poor_order_id = self._insert_order(connection, "SO-NONE")
            # 同一工单两张报销单：3 条明细 + 1 条明细 = 4
            self._insert_expense(connection, self.rich_order_id, "EXP-1", 3)
            self._insert_expense(connection, self.rich_order_id, "EXP-2", 1)
            connection.commit()
        self.admin = self._client(self.admin_id)
        self.external = self._client(self.external_id)

    def _client(self, user_id):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
        return client

    def _insert_order(self, connection, number):
        return connection.execute(
            """
            insert into service_orders (
                order_number, client_id, client_name, site_address, client_order_number,
                status, created_by, created_at
            ) values (?, ?, 'Site', 'Test Site', ?, 'open', ?, '2026-08-13T00:00:00')
            """,
            (number, self.client_id, number, self.admin_id),
        ).lastrowid

    def _insert_expense(self, connection, order_id, number, item_count):
        expense_id = connection.execute(
            """
            insert into expenses (
                service_order_id, expense_number, project, expense_date, amount,
                status, created_by, created_at, updated_at
            ) values (?, ?, 'Expense Column Project', '2026-08-13', 100, 'draft', ?,
                      '2026-08-13T00:00:00', '2026-08-13T00:00:00')
            """,
            (order_id, number, self.admin_id),
        ).lastrowid
        for index in range(item_count):
            connection.execute(
                """
                insert into expense_items (expense_id, project_id, project, amount, description, sort_order)
                values (?, ?, 'Expense Column Project', 10, ?, ?)
                """,
                (expense_id, self.project_id, f"{number}-item-{index}", index),
            )
        return expense_id

    def _page(self, client):
        response = client.get("/service-orders")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_column_sits_right_after_the_report_column(self):
        headers, _ = _table(self._page(self.admin))
        self.assertIn("日报", headers)
        self.assertIn("报销", headers)
        self.assertEqual(headers[headers.index("日报") + 1], "报销")

    def test_counts_expense_line_items_across_every_claim(self):
        headers, rows = _table(self._page(self.admin))
        index = headers.index("报销")
        # 两张报销单 3 + 1 条明细
        self.assertEqual(_row_with(rows, "SO-EXPENSE")[index], "4")
        self.assertEqual(_row_with(rows, "SO-NONE")[index], "0")

    def test_summary_bar_totals_the_same_number(self):
        self.assertEqual(_summary_value(self._page(self.admin), "报销明细合计"), "4")

    def test_every_row_matches_the_header_width(self):
        headers, rows = _table(self._page(self.admin))
        for row in rows:
            self.assertEqual(len(row), len(headers), row)

    def test_column_is_hidden_without_expense_view_permission(self):
        page = self._page(self.external)
        headers, rows = _table(page)
        self.assertNotIn("报销", headers)
        self.assertNotIn("报销明细合计", page)
        for row in rows:
            self.assertEqual(len(row), len(headers), row)


if __name__ == "__main__":
    unittest.main()
