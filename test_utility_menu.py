"""Sidebar "实用工具" menu: groups AI report review, travel tools, KB, assistant, DB console.

The entries used to be scattered (AI report review / travel tools as top-level
links next to the AI assistant, "知识库" and "智能助手" also top-level, and the
DB console inside 系统配置). They now all live under a single collapsible
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
    def test_utility_group_exists_with_all_entries(self):
        groups = _menu_keys_by_group(self.module)
        self.assertIn("实用工具", groups)
        self.assertEqual(
            groups["实用工具"],
            [
                "ai_daily_report",
                "travel_tools",
                "knowledge_base",
                "ai_assistant",
                "database_console",
            ],
        )

    def test_entries_removed_from_their_old_groups(self):
        groups = _menu_keys_by_group(self.module)
        self.assertNotIn("ai_daily_report", groups["主菜单"])
        self.assertNotIn("travel_tools", groups["主菜单"])
        self.assertNotIn("database_console", groups["系统配置"])
        # 知识库 / 智能助手 were top-level entries of 主菜单 before the move.
        self.assertNotIn("knowledge_base", groups["主菜单"])
        self.assertNotIn("ai_assistant", groups["主菜单"])
        self.assertNotIn("knowledge_base", groups["基础数据"])
        self.assertNotIn("ai_assistant", groups["系统配置"])

    def test_every_menu_key_still_configured_exactly_once(self):
        keys = [
            item["key"]
            for group in self.module.MENU_PERMISSION_GROUPS
            for item in group["items"]
        ]
        self.assertEqual(len(keys), len(set(keys)), "duplicate menu keys: %s" % keys)
        for key in (
            "ai_daily_report",
            "travel_tools",
            "knowledge_base",
            "ai_assistant",
            "database_console",
        ):
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
        # Roles must not change just because the entries moved groups.
        self.assertEqual(
            defaults["knowledge_base"], {"admin", "manager", "finance", "employee"}
        )
        self.assertEqual(
            defaults["ai_assistant"], {"admin", "manager", "finance", "employee"}
        )
        self.assertEqual(defaults["database_console"], {"admin"})

    # ─── Rendered sidebar ──────────────────────────────────────────────
    def test_sidebar_renders_the_utility_group(self):
        body = self._sidebar()
        self.assertIn(">实用工具<", body)
        self.assertIn('class="nav-group', body)

    def test_all_links_sit_inside_the_utility_group(self):
        body = self._sidebar()
        block = self._group_block(body, "实用工具")
        for label in ("AI 日报审查", "出行工具", "知识库", "智能助手", "数据库工具"):
            self.assertIn(label, block, "%s must be inside the 实用工具 group" % label)

    def test_knowledge_base_and_assistant_left_the_top_level(self):
        body = self._sidebar()
        # _group_block stops right before the group's own </details>, so resume
        # the scan just after the utility group's closing tag.
        after = body[body.index(">实用工具<") :]
        close = after.index("</details>") + len("</details>")
        # Everything after the utility group must no longer list them at top level.
        self.assertNotIn(">知识库<", after[close:])
        self.assertNotIn(">智能助手<", after[close:])

    def test_database_console_left_the_system_config_group(self):
        body = self._sidebar()
        block = self._group_block(body, "系统配置")
        self.assertNotIn("数据库工具", block)
        self.assertIn("系统设置", block)

    def test_group_auto_opens_on_a_utility_page(self):
        # Visiting a utility page must leave the group expanded, which requires
        # the page's endpoint to be listed in the group's endpoint set.
        template = (REPO / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("utility_endpoints", template)
        for endpoint in (
            "travel_tools_page",
            "database_console",
            "knowledge_base",
            "ai_assistant",
        ):
            self.assertIn(endpoint, template)

    def test_group_auto_opens_when_visiting_kb_and_assistant_pages(self):
        # The rendered page itself must mark 实用工具 as active/open.
        for endpoint in ("knowledge_base", "ai_assistant"):
            client = self.module.app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = self.admin_id
            resp = client.get("/" + endpoint.replace("_", "-"))
            self.assertEqual(resp.status_code, 200, endpoint)
            body = resp.get_data(as_text=True)
            self.assertIn(">实用工具<", body)

    # ─── Translations ──────────────────────────────────────────────────
    def test_utility_menu_translated_for_every_language(self):
        script = (REPO / "static" / "ui-i18n.js").read_text(encoding="utf-8")
        for translation in ("Utilities", "Hulpmiddelen", "Dienstprogramme", "Utilidades"):
            self.assertIn('"实用工具": "%s"' % translation, script)

    def test_moved_entries_translated_for_every_language(self):
        script = (REPO / "static" / "ui-i18n.js").read_text(encoding="utf-8")
        for label, translations in (
            ("知识库", ("Knowledge base", "Kennisbank", "Wissensdatenbank", "Base de conocimiento")),
            ("智能助手", ("AI assistant", "AI-assistent", "KI-Assistent", "Asistente de IA")),
        ):
            for translation in translations:
                self.assertIn('"%s": "%s"' % (label, translation), script)

    def test_moved_labels_are_defined_exactly_once_per_object(self):
        # A re-added key silently shadows the earlier one in JS, so this checks
        # that no single dictionary object defines 知识库 / 智能助手 twice.
        #
        # Each language is built from two separate objects - a base literal
        # (const nl = {...}) and an Object.assign patch block - so the same key
        # appearing once in each is expected and fine; twice inside the *same*
        # object is the shadowing bug this guards against.
        #
        # Note 知识库 previously only existed in the en base literal, while
        # 智能助手 already existed in both objects for en/nl/de/es.
        import re

        script = (REPO / "static" / "ui-i18n.js").read_text(encoding="utf-8")
        lines = script.split("\n")
        # (start, end, label) of each dictionary object.
        objects = [
            (2, 788, "en base"),
            (790, 1403, "nl base"),
            (1405, 2005, "de base"),
            (2007, 2607, "es base"),
            (2609, 2661, "en patch"),
            (2662, 2713, "nl patch"),
            (2714, 2765, "de patch"),
            (2766, 2831, "es patch"),
        ]
        for start, end, name in objects:
            block = "\n".join(lines[start - 1 : end])
            for label in ("知识库", "智能助手"):
                found = re.findall(r'"%s"\s*:' % label, block)
                self.assertLessEqual(
                    len(found),
                    1,
                    "%s defines %s %d times (shadowing duplicate)"
                    % (name, label, len(found)),
                )


if __name__ == "__main__":
    unittest.main()
