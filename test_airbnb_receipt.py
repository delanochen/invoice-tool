"""Airbnb 收据生成工具契约（v0.1.341）。

背景：这个工具曾经只在生产机上手工落地——两个文件在磁盘上，但 app.py 里没有
注册路由，于是页面 404、菜单里也看不到，看起来像「功能凭空消失」；而且它是
服务器上的未跟踪文件，一次 `git reset --hard` 就把注册代码抹掉了。

所以这里不只检查文件在不在，而是钉住三件会静默失效的事：

1. 权限目录里有 `airbnb_receipt`，默认角色是管理员/财务/经理；
2. 路由真的注册到了 app 上——管理员能打开页面；
3. 没有该菜单权限的角色（如 employee）被拒，生成接口返回的是真 PDF。
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
os.environ.setdefault("ADMIN_EMAIL", "pytest-admin@example.invalid")
os.environ.setdefault("ADMIN_PASSWORD", "pytest-password")

MENU_KEY = "airbnb_receipt"
ADMIN_EMAIL = "airbnb-admin@example.invalid"
EMPLOYEE_EMAIL = "airbnb-employee@example.invalid"


def read(*parts):
    return (REPO.joinpath(*parts)).read_text(encoding="utf-8")


class AirbnbReceiptContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp_dir.name)
        # 只复制 app.py：它 import 的包必须继续从仓库解析，否则临时副本会被缓存进
        # sys.modules，本套件清理后别的套件会 ModuleNotFoundError。
        shutil.copyfile(REPO / "app.py", tmp / "app.py")
        cls._inserted_paths = [str(tmp)]
        sys.path.insert(0, str(tmp))
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_airbnb_receipt_test", tmp / "app.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO / "templates")
        cls.module.app.static_folder = str(REPO / "static")

    def setUp(self):
        # 种子放在 setUp 而不是 setUpClass：conftest 在每个测试前 TRUNCATE 全库恢复基线，
        # 若在 setUpClass 里留着未提交的连接，那把锁会和 TRUNCATE 死锁（实测 DeadlockDetected）。
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Airbnb Admin', ?, 'unused', 'admin', 1, '2026-10-05T00:00:00')
                """,
                (ADMIN_EMAIL,),
            )
            connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Airbnb Employee', ?, 'unused', 'employee', 1, '2026-10-05T00:00:00')
                """,
                (EMPLOYEE_EMAIL,),
            )
            connection.commit()
            self.admin_id = connection.execute(
                "select id from users where email = ?", (ADMIN_EMAIL,)
            ).fetchone()["id"]
            self.employee_id = connection.execute(
                "select id from users where email = ?", (EMPLOYEE_EMAIL,)
            ).fetchone()["id"]

    @classmethod
    def tearDownClass(cls):
        for path in getattr(cls, "_inserted_paths", []):
            if path in sys.path:
                sys.path.remove(path)
        cls.temp_dir.cleanup()

    def _client(self, user_id):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
        return client

    # ─── 权限目录 ──────────────────────────────────────────────────────
    def test_menu_key_defaults_to_admin_finance_manager(self):
        self.assertEqual(
            self.module.DEFAULT_MENU_ROLES[MENU_KEY],
            {"admin", "manager", "finance"},
        )
        keys = [
            item["key"]
            for group in self.module.MENU_PERMISSION_GROUPS
            for item in group["items"]
        ]
        self.assertEqual(keys.count(MENU_KEY), 1)

    # ─── 路由真的注册了 ────────────────────────────────────────────────
    def test_admin_can_open_the_page(self):
        resp = self._client(self.admin_id).get("/airbnb-receipt")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/airbnb-receipt/generate", resp.get_data(as_text=True))

    def test_employee_without_permission_is_rejected(self):
        # 清掉可能存在的覆盖行，断言的是默认权限而不是历史配置。
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                "delete from role_menu_permissions where role = ? and menu_key = ?",
                ("employee", MENU_KEY),
            )
            connection.commit()
        resp = self._client(self.employee_id).get("/airbnb-receipt")
        self.assertEqual(resp.status_code, 403)

    # ─── 生成收据 ──────────────────────────────────────────────────────
    def test_generate_returns_a_pdf(self):
        resp = self._client(self.admin_id).post(
            "/airbnb-receipt/generate",
            data={
                "location": "Goodyear",
                "guest": "David Huang",
                "checkin": "2026-08-25",
                "nights": "6",
                "beds": "6",
                "guests": "4",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("pdf", resp.headers["Content-Type"])
        self.assertTrue(resp.get_data().startswith(b"%PDF"))

    # ─── 侧边栏入口 ────────────────────────────────────────────────────
    def test_utility_menu_links_the_page(self):
        base = read("templates", "base.html")
        self.assertIn('nav_link("airbnb_receipt_page"', base)
        self.assertIn("airbnb_receipt_page", base)


if __name__ == "__main__":
    unittest.main()
