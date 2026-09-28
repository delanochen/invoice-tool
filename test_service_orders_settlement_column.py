"""工单列表页「工单结算」列（v0.1.308）。

需求：列位置在「日报」之后；该工单有工单结算单则显示对勾，没有则留空。
这里按渲染结果断言（真跑 /service-orders），而不是只断言模板源码字符串，
避免「模板写了但后端没给出 settlement_count」这类只改一半的回归。
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


class ServiceOrdersSettlementColumnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_settlement_column_test_app", module_path
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
                "customer_reimbursement_items",
                "customer_reimbursements",
                "invoices",
                "service_orders",
                "clients",
                "audit_logs",
                "users",
            ):
                connection.execute(f"delete from {table}")
            self.admin_id, self.employee_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', 1, '2026-08-13T00:00:00')
                """
            ).lastrowid, connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Technician', 'tech@example.com', 'unused', 'employee', 1, '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, created_at)
                values ('99999', 'Settlement Column Client', 'SC Client', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.settled_order_id = self._insert_order(connection, "SO-SETTLED")
            self.plain_order_id = self._insert_order(connection, "SO-PLAIN")
            connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, status, created_by, created_at
                ) values (?, 'settlement.pdf', 'settlement.pdf', 'draft', ?, '2026-08-13T00:00:00')
                """,
                (self.settled_order_id, self.admin_id),
            )
            connection.commit()
        self.admin = self._client(self.admin_id)
        self.employee = self._client(self.employee_id)

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

    def _page(self, client):
        response = client.get("/service-orders")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_column_sits_right_after_the_report_group(self):
        headers, _ = _table(self._page(self.admin))
        self.assertIn("日报", headers)
        self.assertIn("工单结算", headers)
        # 「日报」列后面还有「报销」列（明细条目数），「工单结算」顺延一位
        self.assertEqual(headers[headers.index("日报") + 1], "报销")
        self.assertEqual(headers.index("工单结算"), headers.index("报销") + 1)
        self.assertEqual(headers.index("配套机厂家"), headers.index("工单结算") + 1)

    def test_tick_only_on_orders_that_have_a_settlement(self):
        headers, rows = _table(self._page(self.admin))
        index = headers.index("工单结算")
        self.assertEqual(_row_with(rows, "SO-SETTLED")[index], "✓")
        # 用户明确要求：没有工单结算时留空，不写「-」之类的占位符
        self.assertEqual(_row_with(rows, "SO-PLAIN")[index], "")

    def test_every_row_matches_the_header_width(self):
        headers, rows = _table(self._page(self.admin))
        for row in rows:
            self.assertEqual(len(row), len(headers), row)

    def test_column_is_hidden_without_settlement_view_permission(self):
        page = self._page(self.employee)
        headers, rows = _table(page)
        self.assertNotIn("工单结算", headers)
        self.assertNotIn("settlement-tick", page)
        for row in rows:
            self.assertEqual(len(row), len(headers), row)

    def test_empty_state_colspan_follows_the_header_count(self):
        source = (REPO_DIR / "templates" / "service_orders.html").read_text(encoding="utf-8")
        self.assertIn('colspan="{{ grid_columns }}"', source)
        self.assertNotIn('colspan="11"', source)
        self.assertNotIn('colspan="13"', source)


if __name__ == "__main__":
    unittest.main()
