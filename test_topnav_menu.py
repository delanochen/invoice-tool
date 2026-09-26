"""顶栏菜单的两条契约（v0.1.307）：

1. 二/三级子菜单项前面要有小图标。形状集中放在 base.html 的 sprite 里（<symbol> + <use>），
   各调用点只传图标名。漏传一个是**静默**的 —— 那项只是没图标，页面照常渲染，
   只有肉眼看才发现，所以这里把「每个 nav_link 都带 icon」和「引用的图标都在 sprite 里」钉住。

2. 「消息」从主菜单下拉里剥出来，成为菜单栏上、系统配置之后的一级入口，并显示未读数徽标。
   注意 messages.js 原来是把 `消息 (2)` 整段写进 [data-message-nav] 的 textContent；
   那个钩子现在挂在徽标 <span> 上（兄弟节点是图标和「消息」二字），再那么写会把图标抹掉。
"""
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
os.environ.setdefault("ADMIN_EMAIL", "pytest-admin@example.invalid")
os.environ.setdefault("ADMIN_PASSWORD", "pytest-password")


def read(*parts):
    return (REPO.joinpath(*parts)).read_text(encoding="utf-8")


class TopNavMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp_dir.name)
        # 只复制 app.py：它 import 的包（ai_daily_report / travel_tools ...）必须继续从仓库解析，
        # 否则这些临时副本会被缓存进 sys.modules，本套件清理后别的套件会 ModuleNotFoundError。
        shutil.copyfile(REPO / "app.py", tmp / "app.py")
        cls._inserted_paths = [str(tmp)]
        sys.path.insert(0, str(tmp))
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        spec = importlib.util.spec_from_file_location("invoice_tool_topnav_test", tmp / "app.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        # 模板/静态目录指回仓库本尊（临时目录里没有 templates/）
        cls.module.app.template_folder = str(REPO / "templates")
        cls.module.app.static_folder = str(REPO / "static")
        with cls.module.app.app_context():
            cls.module.init_db()
            cls.admin_id = cls.module.db().execute(
                "select id from users where role = 'admin' and is_active = 1 order by id limit 1"
            ).fetchone()["id"]
        cls.template = read("templates", "base.html")
        cls.css = read("static", "erp-topnav.css")
        cls.messages_js = read("static", "messages.js")

    @classmethod
    def tearDownClass(cls):
        for path in getattr(cls, "_inserted_paths", []):
            if path in sys.path:
                sys.path.remove(path)
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            self.module.db().execute("delete from messages")
            self.module.db().commit()

    def rendered(self):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.admin_id
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def set_unread(self, count):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("delete from messages")
            for index in range(count):
                db.execute(
                    "insert into messages (user_id, title, body, created_at) values (?, ?, ?, ?)",
                    (self.admin_id, f"测试消息 {index + 1}", "正文", self.module.now()),
                )
            db.commit()

    # ─── 子菜单图标 ────────────────────────────────────────────────────
    def test_every_nav_link_call_passes_an_icon(self):
        calls = re.findall(r"\{\{\s*nav_link\((.+?)\)\s*\}\}", self.template, re.S)
        self.assertGreater(len(calls), 20, "调用点数量骤减，正则或模板结构变了？")
        missing = [call.strip() for call in calls if "icon=" not in call]
        self.assertEqual(missing, [], "这些菜单项没传图标名（会静默地少一个图标）")

    def test_sprite_defines_exactly_the_referenced_icons(self):
        # 引用图标有三种写法：nav_link(..., icon="x")、nav_icon("x")（三级 summary 用）、
        # 以及宏定义里统一的 <use href="#ni-{{ name }}">（那行带 {{ }}，下面的正则不会误捕）。
        referenced = set(re.findall(r'icon="([a-z0-9-]+)"', self.template))
        referenced |= set(re.findall(r'nav_icon\("([a-z0-9-]+)"\)', self.template))
        defined = set(re.findall(r'<symbol id="ni-([a-z0-9-]+)"', self.template))
        self.assertGreater(len(referenced), 20)
        self.assertEqual(referenced - defined, set(), "调用了 sprite 里没有的图标（名字拼错？）")
        self.assertEqual(defined - referenced, set(), "sprite 里有没人引用的图标")

    def test_rendered_submenu_items_all_carry_an_icon_element(self):
        body = self.rendered()
        links = re.findall(
            r'<a href="[^"]+" class="translatable-text[^"]*"[^>]*>(.*?)</a>', body, re.S
        )
        self.assertGreater(len(links), 20, "渲染出的子菜单项数量不对")
        missing = [inner.strip() for inner in links if '<svg class="nav-item-icon"' not in inner]
        self.assertEqual(missing, [], "这些渲染出来的子菜单项没有图标 svg")

    def test_rendered_icon_names_all_exist_in_the_sprite(self):
        body = self.rendered()
        used = set(re.findall(r'<use href="#ni-([a-z0-9-]+)"></use>', body))
        self.assertGreater(len(used), 20)
        sprite = set(re.findall(r'<symbol id="ni-([a-z0-9-]+)"', body))
        self.assertEqual(sprite, used)

    def test_subgroup_summary_also_carries_an_icon(self):
        # 三级菜单（薪酬参数）的 summary 也要有
        body = self.rendered()
        self.assertRegex(body, r'<summary><svg class="nav-item-icon".*?薪酬参数</summary>')

    def test_icon_css_gives_shape_attributes(self):
        # 形状写在 symbol 里，fill/stroke 靠 <use> 宿主 svg 继承 —— 所以这几个属性必须在 CSS 里
        rule = re.search(r"#topnav \.nav-item-icon\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule, "erp-topnav.css 里没有 .nav-item-icon 规则")
        block = rule.group(1)
        for prop in ("fill: none", "stroke: currentColor", "stroke-width: 1.7"):
            self.assertIn(prop, block)

    def test_sprite_container_is_positioned_not_display_none(self):
        rule = re.search(r"\.nav-icon-sprite\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule)
        self.assertIn("position: absolute", rule.group(1))
        # display:none 的容器里的 <symbol> 在部分浏览器无法被 <use> 引用
        self.assertNotIn("display: none", rule.group(1))

    # ─── 消息提升为一级入口 ────────────────────────────────────────────
    def test_message_left_the_main_menu_dropdown(self):
        body = self.rendered()
        block = body[body.index(">主菜单<") :]
        block = block[: block.index("</details>")]
        self.assertNotIn("/messages", block, "主菜单下拉里还留着消息入口")
        self.assertNotIn("data-message-nav", block)
        # 原来消息项前面有一条分隔线，一并带走
        self.assertNotIn("<hr>", block)

    def test_message_is_a_top_level_entry_after_system_config(self):
        body = self.rendered()
        nav = body[body.index('<nav id="topnav">') : body.index("</nav>")]
        entry = re.search(r'<a class="nav-entry nav-entry-message[^"]*"\s+href="([^"]+)"', nav)
        self.assertIsNotNone(entry, "菜单栏上没有找到消息一级入口")
        self.assertEqual(entry.group(1), "/messages")
        position = nav.index("nav-entry-message")
        self.assertIn("系统配置", nav[:position], "消息应在系统配置之后")
        self.assertNotIn("<details", nav[position:], "消息之后不该还有菜单组")

    def test_message_entry_paints_as_active_on_the_message_page(self):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.admin_id
        response = client.get("/messages")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertRegex(body, r'nav-entry-message active[^>]*aria-current="page"')
        # 主菜单组此时不该再当作「当前位置」（messages 已从 main_endpoints 移除）
        main = body[body.index('class="nav-group main-group') :]
        self.assertNotIn(" active", main[: main.index(">")])

    def test_unread_badge_shows_the_count(self):
        self.set_unread(3)
        body = self.rendered()
        self.assertRegex(body, r'data-unread-count="3"[^>]*>3</span>')
        self.assertNotIn('data-unread-count="0" hidden', body)

    def test_unread_badge_is_hidden_when_zero(self):
        self.set_unread(0)
        body = self.rendered()
        # 0 条时模板直接给 hidden，且不给数字（避免 JS 未执行时留一个空心红点）
        self.assertRegex(body, r'data-unread-count="0"[^>]* hidden[^>]*></span>')

    def test_unread_badge_caps_at_99_plus(self):
        self.set_unread(120)
        body = self.rendered()
        self.assertRegex(body, r'data-unread-count="120"[^>]*>99\+</span>')

    def test_css_keeps_the_hidden_badge_hidden(self):
        rule = re.search(r"#topnav \.nav-badge\[hidden\]\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule, "缺少 .nav-badge[hidden] 规则")
        # .nav-badge 自己设了 display:inline-flex，与 UA 的 [hidden]{display:none} 同特异性，
        # 作者样式会赢 —— 不显式写这条，0 条未读时会留一个空心红点
        self.assertIn("display: none", rule.group(1))

    def test_messages_js_writes_only_the_number_into_the_badge(self):
        self.assertIn("function paintMessageNav(", self.messages_js)
        self.assertNotIn("messageNavText", self.messages_js)
        # 钩子现在在徽标 <span> 上，旁边就是图标：整段文字写 textContent 会把图标抹掉
        self.assertNotIn("navigation.textContent", self.messages_js)
        self.assertIn("badge.hidden = !safe", self.messages_js)


if __name__ == "__main__":
    unittest.main()
