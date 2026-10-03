"""员工付款中心「创建日期」筛选（v0.1.360）。

背景（2026-10-03 用户报障）：/employee-payments 只有状态 / 员工两个筛选，
页脚「应付合计 / 实付合计」因此是**全库累计** —— 拿它跟任何期间报表相减
必然对不上（用户拿全库 21 万去减 8–10 月的结算收入，算出「利润只有 1 万」）。

这里按渲染结果断言（真跑路由），既验证后端接了参数，也验证合计确实跟着
筛选变化，避免「模板写了输入框但后端没接」这种只改一半的回归。

本测试连 PostgreSQL invoice_test（见 conftest.py / tests_pg.py）。
"""

import importlib.util
import re
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
NOW = "2026-09-01T00:00:00"

# 四张付款单分布在窗口内外
PAYMENTS = (
    ("PMT-JUL", "2026-07-15T09:00:00", 100.00),
    ("PMT-AUG", "2026-08-10T09:00:00", 200.00),
    ("PMT-SEP", "2026-09-05T09:00:00", 300.00),
    ("PMT-OCT", "2026-10-20T09:00:00", 400.00),
)


def _payment_numbers(html):
    start = html.index('class="list-table erp-grid"')
    body = html[start:html.index("</table>", start)]
    return set(re.findall(r"<a [^>]*>(PMT-[A-Z0-9]+)</a>", body))


def _net_total(html):
    match = re.search(r"实付合计</span><strong>([^<]+)</strong>", html)
    if not match:
        return None
    return float(re.sub(r"[^0-9.]", "", match.group(1)))


class EmployeePaymentDateFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_payment_date_filter_app", module_path
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
            db = self.module.db()
            db.execute("delete from employee_payment_orders")
            db.execute("delete from users")
            self.admin_id = db.execute(
                "insert into users (name, email, password_hash, role, created_at, "
                "is_active, region_code, country_code, address, default_language, "
                "phone, phone_verified, preferred_communication_language, "
                "communication_languages) values "
                "('Pay Admin','pay-admin@example.invalid','x','admin',?,1,'US','US',"
                "'','zh','',0,'zh','zh') returning id",
                (NOW,),
            ).fetchone()["id"]
            worker_id = db.execute(
                "insert into users (name, email, password_hash, role, created_at, "
                "is_active, region_code, country_code, address, default_language, "
                "phone, phone_verified, preferred_communication_language, "
                "communication_languages) values "
                "('Pay Worker','pay-worker@example.invalid','x','worker',?,1,'US','US',"
                "'','zh','',0,'zh','zh') returning id",
                (NOW,),
            ).fetchone()["id"]
            for number, created_at, amount in PAYMENTS:
                db.execute(
                    "insert into employee_payment_orders (payment_number, employee_id, "
                    "payment_type, status, currency, gross_amount, advance_offset, "
                    "other_adjustment, net_amount, source_type, created_by, created_at, "
                    "updated_at) values (?,?,'salary','draft','USD',?,0,0,?,'manual',?,?,?)",
                    (number, worker_id, amount, amount, self.admin_id, created_at, created_at),
                )
            db.commit()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin_id

    def get(self, query=""):
        response = self.client.get(f"/employee-payments{query}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    # ------------------------------------------------------------------ 用例

    def test_without_filter_returns_everything(self):
        """不给日期 → 全库累计（旧行为保持不变）。"""
        html = self.get()
        self.assertEqual(_payment_numbers(html), {"PMT-JUL", "PMT-AUG", "PMT-SEP", "PMT-OCT"})
        self.assertEqual(_net_total(html), 1000.00)

    def test_date_range_limits_rows_and_total(self):
        """2026-08-01 ~ 2026-09-30 → 只剩 8、9 月两张，合计 500。"""
        html = self.get("?date_from=2026-08-01&date_to=2026-09-30")
        self.assertEqual(_payment_numbers(html), {"PMT-AUG", "PMT-SEP"})
        self.assertEqual(_net_total(html), 500.00)

    def test_open_ended_from(self):
        """只给开始日期 → 该日期之后全部。"""
        html = self.get("?date_from=2026-09-01")
        self.assertEqual(_payment_numbers(html), {"PMT-SEP", "PMT-OCT"})
        self.assertEqual(_net_total(html), 700.00)

    def test_open_ended_to(self):
        """只给结束日期 → 该日期之前全部。"""
        html = self.get("?date_to=2026-08-31")
        self.assertEqual(_payment_numbers(html), {"PMT-JUL", "PMT-AUG"})
        self.assertEqual(_net_total(html), 300.00)

    def test_invalid_date_is_ignored(self):
        """非法日期直接忽略（不报错、不变成空列表）。"""
        html = self.get("?date_from=not-a-date&date_to=2026-13-45")
        self.assertEqual(_payment_numbers(html), {"PMT-JUL", "PMT-AUG", "PMT-SEP", "PMT-OCT"})

    def test_reversed_range_is_swapped(self):
        """起止写反 → 自动交换，等价于 2026-08-01 ~ 2026-09-30。"""
        html = self.get("?date_from=2026-09-30&date_to=2026-08-01")
        self.assertEqual(_payment_numbers(html), {"PMT-AUG", "PMT-SEP"})

    def test_filter_inputs_are_rendered(self):
        """模板必须真的有日期输入框，且回填当前值。"""
        html = self.get("?date_from=2026-08-01&date_to=2026-09-30")
        self.assertIn('name="date_from"', html)
        self.assertIn('name="date_to"', html)
        self.assertIn('value="2026-08-01"', html)
        self.assertIn('value="2026-09-30"', html)


if __name__ == "__main__":
    unittest.main()
