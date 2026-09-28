"""工单结算报表「工单状态」筛选。

需求：筛选区增加工单状态（进行中 / 已关闭 / 全部），默认「进行中」。
这里按渲染结果断言（真跑 /reports/customer-reimbursements），
而不是只断言模板源码字符串，避免「模板写了但后端没接参数」这类只改一半的回归。
"""

import importlib.util
import re
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


def _order_numbers(html):
    """取出结果表格里出现的工单号（结算单链接的文案）。"""
    start = html.index('class="list-table erp-grid"')
    body = html[start : html.index("</table>", start)]
    # 工单列的单元格是 <a href="...customer-reimbursement...">SO-XXXX</a>
    return re.findall(r"<a [^>]*>(SO-[A-Z0-9-]+)</a>", body)


class CustomerReimbursementOrderStatusFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_order_status_filter_test_app", module_path
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
                "customer_reimbursement_expense_links",
                "customer_reimbursements",
                "service_orders",
                "clients",
                "users",
            ):
                connection.execute(f"delete from {table}")
            self.admin_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', 1, '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, created_at)
                values ('99997', 'Order Status Client', 'OS Client', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.open_order = self._insert_order(connection, "SO-OPEN", "open")
            self.closed_order = self._insert_order(connection, "SO-CLOSED", "closed")
            self._insert_settlement(connection, self.open_order, "settle-open.pdf")
            self._insert_settlement(connection, self.closed_order, "settle-closed.pdf")
            connection.commit()
        self.admin = self._client(self.admin_id)

    def _client(self, user_id):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
        return client

    def _insert_order(self, connection, number, status):
        return connection.execute(
            """
            insert into service_orders (
                order_number, client_id, client_name, site_address, client_order_number,
                status, created_by, created_at
            ) values (?, ?, 'Site', 'Test Site', ?, ?, ?, '2026-08-13T00:00:00')
            """,
            (number, self.client_id, number, status, self.admin_id),
        ).lastrowid

    def _insert_settlement(self, connection, order_id, file_name):
        return connection.execute(
            """
            insert into customer_reimbursements (
                service_order_id, file_name, stored_filename, status, created_by, created_at
            ) values (?, ?, ?, 'sent', ?, '2026-08-13T00:00:00')
            """,
            (order_id, file_name, file_name, self.admin_id),
        ).lastrowid

    def _page(self, query=""):
        response = self.admin.get(f"/reports/customer-reimbursements{query}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_defaults_to_open_orders(self):
        """不传参数时只显示进行中工单的结算。"""
        html = self._page()
        numbers = _order_numbers(html)
        self.assertIn("SO-OPEN", numbers)
        self.assertNotIn("SO-CLOSED", numbers)

    def test_closed_shows_only_closed_orders(self):
        numbers = _order_numbers(self._page("?order_status=closed"))
        self.assertIn("SO-CLOSED", numbers)
        self.assertNotIn("SO-OPEN", numbers)

    def test_all_shows_both(self):
        numbers = _order_numbers(self._page("?order_status=all"))
        self.assertIn("SO-OPEN", numbers)
        self.assertIn("SO-CLOSED", numbers)

    def test_invalid_value_falls_back_to_open(self):
        """乱填参数不应报错，也不应变成「不限」——回落到默认的进行中。"""
        numbers = _order_numbers(self._page("?order_status=bogus"))
        self.assertIn("SO-OPEN", numbers)
        self.assertNotIn("SO-CLOSED", numbers)

    def test_filter_is_rendered_and_survives_reload(self):
        html = self._page("?order_status=closed")
        self.assertIn('name="order_status"', html)
        # 回显：选中的是「已关闭」
        self.assertRegex(html, r'value="closed"\s+selected')
        self.assertIn("工单状态", html)
        # 与结算状态是两个独立筛选，都还在
        self.assertIn('name="status"', html)
        self.assertIn("结算状态", html)


if __name__ == "__main__":
    unittest.main()
