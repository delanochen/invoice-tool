"""消息页「已读 / 未读」标记功能（v0.1.329）。

用户需求（原话）：
  已读 未读状态列放在第一列
  增加按钮：未读全为已读
  已读的可以选中设为未读
  未读的可以选中设为已读

三条契约：
1. 状态列是表格第一列（勾选列之后），标题/内容/时间的相对顺序不变。
2. 「全部标为已读」把自己名下所有未读一次性置 1。
3. 「标为已读 / 标为未读」按勾选的 id 双向切换，且**只能在本人消息里生效** ——
   消息是每人一份的私有数据，`where user_id = ?` 就是权限边界，伪造别人的 id
   必须被静默丢弃（不报错、不改数据）。
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


class MessagesMarkReadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp_dir.name)
        shutil.copyfile(REPO / "app.py", tmp / "app.py")
        cls._inserted_paths = [str(tmp)]
        sys.path.insert(0, str(tmp))
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        spec = importlib.util.spec_from_file_location("invoice_tool_messages_test", tmp / "app.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO / "templates")
        cls.module.app.static_folder = str(REPO / "static")
        with cls.module.app.app_context():
            cls.module.init_db()
            db = cls.module.db()
            cls.admin_id = db.execute(
                "select id from users where role = 'admin' and is_active = 1 order by id limit 1"
            ).fetchone()["id"]
            other = db.execute(
                "select id from users where id != ? and is_active = 1 order by id limit 1",
                (cls.admin_id,),
            ).fetchone()
            if other is None:
                db.execute(
                    "insert into users (email, password_hash, name, role, is_active, created_at)"
                    " values (?, ?, ?, 'employee', 1, ?)",
                    ("other-message-user@example.invalid", "x", "别人", cls.module.now()),
                )
                db.commit()
                other = db.execute(
                    "select id from users where email = ?",
                    ("other-message-user@example.invalid",),
                ).fetchone()
            cls.other_id = other["id"]

    @classmethod
    def tearDownClass(cls):
        for path in getattr(cls, "_inserted_paths", []):
            if path in sys.path:
                sys.path.remove(path)
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("delete from messages")
            db.commit()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin_id

    # ─── helpers ──────────────────────────────────────────────────────
    def seed(self, count, is_read=0):
        with self.module.app.app_context():
            db = self.module.db()
            for index in range(count):
                db.execute(
                    "insert into messages (user_id, title, body, is_read, created_at)"
                    " values (?, ?, ?, ?, ?)",
                    (self.admin_id, f"我的消息 {index + 1}", "正文", is_read, self.module.now()),
                )
            db.commit()

    def seed_other(self, count, is_read=0):
        with self.module.app.app_context():
            db = self.module.db()
            ids = []
            for index in range(count):
                cursor = db.execute(
                    "insert into messages (user_id, title, body, is_read, created_at)"
                    " values (?, ?, ?, ?, ?)",
                    (self.other_id, f"别人的消息 {index + 1}", "正文", is_read, self.module.now()),
                )
                ids.append(cursor.lastrowid)
            db.commit()
            return ids

    def my_ids(self):
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select id from messages where user_id = ? order by id", (self.admin_id,)
            ).fetchall()
            return [row["id"] for row in rows]

    def read_states(self, user_id=None):
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select id, is_read from messages where user_id = ? order by id",
                (user_id or self.admin_id,),
            ).fetchall()
            return {row["id"]: row["is_read"] for row in rows}

    def page(self):
        response = self.client.get("/messages")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    # ─── 契约 1：状态列在第一列 ────────────────────────────────────────
    def test_status_column_is_first_data_column(self):
        self.seed(2)
        body = self.page()
        header = re.search(r"<thead><tr>(.*?)</tr></thead>", body, re.S)
        self.assertIsNotNone(header, "找不到表头")
        columns = re.findall(r"<th[^>]*>(.*?)</th>", header.group(1), re.S)
        labels = [re.sub(r"<[^>]+>", "", column).strip() for column in columns]
        # 第一列是勾选列（空的），第二列必须是状态
        self.assertEqual(labels[1], "状态", f"状态列不在第一位，实际列序：{labels}")
        self.assertEqual(labels[2], "标题")
        self.assertLess(labels.index("标题"), labels.index("内容"))
        self.assertLess(labels.index("内容"), labels.index("时间"))

    def test_every_row_has_select_checkbox_and_status_cell_first(self):
        self.seed(3)
        body = self.page()
        rows = re.findall(r'<tr class="[^"]*" data-message-row(.*?)</tr>', body, re.S)
        self.assertEqual(len(rows), 3, f"应有 3 行，实际 {len(rows)}")
        for row in rows:
            # 注意要连 <td ...> 的开标签一起取：data-message-status 是属性，
            # 只在标签内部，只抓 (.*?)</td> 的内容会漏掉它。
            cells = re.findall(r"<td[^>]*>.*?</td>", row, re.S)
            self.assertEqual(len(cells), 5, f"应有 5 列，实际 {len(cells)}：{row[:200]}")
            self.assertIn('data-message-select', cells[0], "第一列不是勾选框")
            self.assertIn("data-message-status", cells[1], "第二列不是状态列")

    def test_body_text_is_not_scraped_into_select_column(self):
        # 勾选列在 system-grid 的「表内搜索」里不该被算作正文（列宽很窄，填字会撑破布局）
        body = self.page()
        self.assertIn("message-select-col", body)
        self.assertIn(".erp-grid .message-select-col", body)

    # ─── 契约 1b：三个按钮都在工具栏里 ──────────────────────────────────
    def test_toolbar_has_all_three_buttons(self):
        self.seed(1, is_read=0)
        body = self.page()
        toolbar = re.search(r'<header class="erp-toolbar[^"]*">(.*?)</header>', body, re.S)
        self.assertIsNotNone(toolbar)
        buttons = toolbar.group(1)
        self.assertRegex(buttons, r'data-message-mark-selected[^>]*data-mark-read="1"')
        self.assertRegex(buttons, r'data-message-mark-selected[^>]*data-mark-read="0"')
        self.assertIn("data-message-mark-all", buttons)
        self.assertIn("全部标为已读", buttons)
        self.assertIn("标为已读", buttons)
        self.assertIn("标为未读", buttons)

    def test_mark_all_button_disabled_when_nothing_unread(self):
        self.seed(2, is_read=1)
        body = self.page()
        button = re.search(r'<button[^>]*data-message-mark-all[^>]*>', body).group(0)
        self.assertIn("disabled", button, "没有未读时「全部标为已读」应当置灰")

    def test_mark_all_button_enabled_when_unread_exists(self):
        self.seed(2, is_read=0)
        body = self.page()
        button = re.search(r'<button[^>]*data-message-mark-all[^>]*>', body).group(0)
        self.assertNotIn("disabled", button)

    # ─── 契约 2：全部标为已读 ─────────────────────────────────────────
    def test_mark_all_read_marks_every_unread_message(self):
        self.seed(4, is_read=0)
        response = self.client.post("/messages/mark-all-read", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        states = self.read_states()
        self.assertEqual(len(states), 4)
        self.assertTrue(all(value == 1 for value in states.values()), f"仍有未读：{states}")

    def test_mark_all_read_does_not_touch_other_users(self):
        self.seed(2, is_read=0)
        other_ids = self.seed_other(3, is_read=0)
        self.client.post("/messages/mark-all-read")
        self.assertTrue(all(value == 1 for value in self.read_states().values()))
        other_states = self.read_states(self.other_id)
        self.assertTrue(
            all(value == 0 for value in other_states.values()),
            f"别人的消息被误标：{other_states}",
        )
        self.assertEqual(sorted(other_states), sorted(other_ids))

    def test_mark_all_read_with_nothing_unread_is_a_noop(self):
        self.seed(2, is_read=1)
        response = self.client.post("/messages/mark-all-read", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(value == 1 for value in self.read_states().values()))

    # ─── 契约 3：按勾选双向切换 ────────────────────────────────────────
    def test_selected_unread_can_be_marked_read(self):
        self.seed(3, is_read=0)
        ids = self.my_ids()
        response = self.client.post(
            "/messages/mark",
            data={"is_read": "1", "message_id": [str(ids[0]), str(ids[2])]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        states = self.read_states()
        self.assertEqual(states[ids[0]], 1)
        self.assertEqual(states[ids[1]], 0, "没勾的那条不该被改")
        self.assertEqual(states[ids[2]], 1)

    def test_selected_read_can_be_marked_unread(self):
        self.seed(3, is_read=1)
        ids = self.my_ids()
        self.client.post(
            "/messages/mark",
            data={"is_read": "0", "message_id": [str(ids[1])]},
        )
        states = self.read_states()
        self.assertEqual(states[ids[1]], 0, "已读的选中后应能设为未读")
        self.assertEqual(states[ids[0]], 1)
        self.assertEqual(states[ids[2]], 1)

    def test_mark_read_then_unread_round_trips(self):
        self.seed(1, is_read=0)
        message_id = str(self.my_ids()[0])
        self.client.post("/messages/mark", data={"is_read": "1", "message_id": [message_id]})
        self.assertEqual(self.read_states()[int(message_id)], 1)
        self.client.post("/messages/mark", data={"is_read": "0", "message_id": [message_id]})
        self.assertEqual(self.read_states()[int(message_id)], 0)

    def test_mark_rejects_other_users_message_ids(self):
        self.seed(1, is_read=0)
        other_ids = self.seed_other(2, is_read=0)
        mine = self.my_ids()[0]
        self.client.post(
            "/messages/mark",
            data={"is_read": "1", "message_id": [str(mine), *[str(i) for i in other_ids]]},
        )
        self.assertEqual(self.read_states()[mine], 1, "自己的那条应该被标")
        other_states = self.read_states(self.other_id)
        self.assertTrue(
            all(value == 0 for value in other_states.values()),
            f"越权标了别人的消息：{other_states}",
        )

    def test_mark_with_only_foreign_ids_changes_nothing(self):
        self.seed(1, is_read=0)
        other_ids = self.seed_other(1, is_read=0)
        response = self.client.post(
            "/messages/mark",
            data={"is_read": "1", "message_id": [str(i) for i in other_ids]},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.read_states()[self.my_ids()[0]], 0)
        self.assertEqual(self.read_states(self.other_id)[other_ids[0]], 0)

    def test_mark_with_no_selection_flashes_and_changes_nothing(self):
        self.seed(2, is_read=0)
        response = self.client.post("/messages/mark", data={"is_read": "1"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(value == 0 for value in self.read_states().values()))

    def test_mark_ignores_garbage_ids(self):
        self.seed(2, is_read=0)
        ids = self.my_ids()
        self.client.post(
            "/messages/mark",
            data={"is_read": "1", "message_id": ["abc", "", "-1", "0", str(ids[0])]},
        )
        states = self.read_states()
        self.assertEqual(states[ids[0]], 1, "合法 id 仍应生效")
        self.assertEqual(states[ids[1]], 0)

    def test_mark_is_idempotent(self):
        self.seed(1, is_read=1)
        message_id = str(self.my_ids()[0])
        self.client.post("/messages/mark", data={"is_read": "1", "message_id": [message_id]})
        self.assertEqual(self.read_states()[int(message_id)], 1)

    # ─── 未登录一律拒绝 ────────────────────────────────────────────────
    def test_anonymous_cannot_mark(self):
        self.seed(2, is_read=0)
        anonymous = self.module.app.test_client()
        for url in ("/messages/mark", "/messages/mark-all-read"):
            response = anonymous.post(url, data={"is_read": "1"})
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/login", response.headers.get("Location", ""), url)
        self.assertTrue(all(value == 0 for value in self.read_states().values()))


class MessagesMarkReadClientContractTest(unittest.TestCase):
    """前端契约：勾选态、提交端点、以及「不要触发 replaceData」。"""

    def test_messages_js_wires_three_buttons_to_the_two_endpoints(self):
        script = read("static", "messages.js")
        self.assertIn("data-message-mark-selected", script)
        self.assertIn("data-message-mark-all", script)
        self.assertIn("messageMarkForm", script)
        self.assertIn("messageMarkAllForm", script)
        self.assertIn("message_id", script)

    def test_selection_reads_source_table_not_mirror(self):
        script = read("static", "messages.js")
        # 镜像层的 checkbox 是副本；选中态必须从原表读，否则 replaceData 一来全丢。
        self.assertIn("messageSourceCheckboxes", script)
        self.assertIn("data-message-select", script)

    def test_select_all_dispatches_change_so_mirror_follows(self):
        script = read("static", "messages.js")
        block = script[script.index("data-message-select-all") :]
        self.assertIn('new Event("change"', block)

    def test_grid_skips_replace_data_for_checkbox_only_changes(self):
        script = read("static", "system-grid.js")
        block = script[script.index("sync() {") : script.index("updateCount() {")]
        self.assertIn("onlyCheckboxes", block, "缺少「只改勾选态」的短路分支")
        self.assertIn("input[type=checkbox]", block)
        # 该分支内部只能是「把原表勾选态同步给镜像副本」然后 return ——
        # 不能再出现 replaceData（那会重建整张表，滚动清零）。注释里提到
        # replaceData 不算数（熔断那段注释就在后面），所以先剥掉注释再断言。
        branch = block[block.index("if (onlyCheckboxes)"): block.index("const now = performance.now();")]
        code = re.sub(r"//[^\n]*", "", branch)
        self.assertNotIn("replaceData", code, "勾选态变化仍然会重建整张表（滚动会跳）")
        self.assertIn("return;", code)


if __name__ == "__main__":
    unittest.main()
