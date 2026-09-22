"""Sidebar "实用工具" menu: groups AI report review, travel tools, DB console.

The three entries used to be scattered (two as top-level links next to the
AI assistant, one inside 系统配置). They now live under a single collapsible
"实用工具" group, in both the rendered sidebar and the menu permission
configuration (MENU_PERMISSION_GROUPS).
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


def _menu_keys_by_group(module):
    return {
        group["label"]: [item["key"] for item in group["items"]]
        for group in module.MENU_PERMISSION_GROUPS
    }


class UtilityMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp_dir.name)
        # Only app.py is copied: the packages it imports (ai_daily_report,
        # travel_tools, ...) must keep resolving from the repo. Copying them
        # here cached the temp copy in sys.modules, and later suites failed
        # with ModuleNotFoundError once this directory was cleaned up.
        shutil.copyfile(REPO / "app.py", tmp / "app.py")
        cls._inserted_paths = [str(tmp)]
        sys.path.insert(0, str(tmp))
        # pytest normally puts the repo root on sys.path already (conftest.py
        # lives there); only add it when missing, and never remove it again -
        # other suites in this repo resolve their imports through it too.
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_utility_menu_test", tmp / "app.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO / "templates")
        cls.module.app.static_folder = str(REPO / "static")
        with cls.module.app.app_context():
            cls.module.init_db()
            row = cls.module.db().execute(
                "select id from users where role = 'admin' order by id limit 1"
            ).fetchone()
            cls.admin_id = row["id"]

    @classmethod
    def tearDownClass(cls):
        for path in getattr(cls, "_inserted_paths", []):
            if path in sys.path:
                sys.path.remove(path)
        cls.temp_dir.cleanup()

    def _sidebar(self):
        client = self.module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.admin_id
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def _group_block(self, body, label):
        start = body.index(">%s<" % label)
        return body[start:body.index("</details>", start)]

    # ─── Menu permission configuration ─────────────────────────────────
    def test_utility_group_exists_with_the_three_entries(self):
        groups = _menu_keys_by_group(self.module)
        self.assertIn("实用工具", groups)
        self.assertEqual(
            groups["实用工具"], ["ai_daily_report", "travel_tools", "database_console"]
        )

    def test_entries_removed_from_their_old_groups(self):
        groups = _menu_keys_by_group(self.module)
        self.assertNotIn("ai_daily_report", groups["主菜单"])
        self.assertNotIn("travel_tools", groups["主菜单"])
        self.assertNotIn("database_console", groups["系统配置"])

    def test_every_menu_key_still_configured_exactly_once(self):
        keys = [
            item["key"]
            for group in self.module.MENU_PERMISSION_GROUPS
            for item in group["items"]
        ]
        self.assertEqual(len(keys), len(set(keys)), "duplicate menu keys: %s" % keys)
        for key in ("ai_daily_report", "travel_tools", "database_console"):
            self.assertIn(key, self.module.DEFAULT_MENU_ROLES)
            self.assertEqual(keys.count(key), 1)

    def test_default_roles_preserved_after_the_move(self):
        defaults = self.module.DEFAULT_MENU_ROLES
        self.assertEqual(
            defaults["ai_daily_report"], {"admin", "manager", "finance", "employee"}
        )
        self.assertEqual(
            defaults["travel_tools"], {"admin", "manager", "finance", "employee"}
        )
        self.assertEqual(defaults["database_console"], {"admin"})

    # ─── Rendered sidebar ──────────────────────────────────────────────
    def test_sidebar_renders_the_utility_group(self):
        body = self._sidebar()
        self.assertIn(">实用工具<", body)
        self.assertIn('class="nav-group', body)

    def test_all_three_links_sit_inside_the_utility_group(self):
        body = self._sidebar()
        block = self._group_block(body, "实用工具")
        for label in ("AI 日报审查", "出行工具", "数据库工具"):
            self.assertIn(label, block, "%s must be inside the 实用工具 group" % label)

    def test_database_console_left_the_system_config_group(self):
        body = self._sidebar()
        block = self._group_block(body, "系统配置")
        self.assertNotIn("数据库工具", block)
        self.assertIn("系统设置", block)

    def test_group_auto_opens_on_a_utility_page(self):
        # Visiting the travel tools page must leave the group expanded, which
        # requires travel_tools_page to be listed in the group's endpoints.
        template = (REPO / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("utility_endpoints", template)
        self.assertIn("travel_tools_page", template)
        self.assertIn("database_console", template)

    # ─── Translations ──────────────────────────────────────────────────
    def test_utility_menu_translated_for_every_language(self):
        script = (REPO / "static" / "ui-i18n.js").read_text(encoding="utf-8")
        for translation in ("Utilities", "Hulpmiddelen", "Dienstprogramme", "Utilidades"):
            self.assertIn('"实用工具": "%s"' % translation, script)


if __name__ == "__main__":
    unittest.main()
