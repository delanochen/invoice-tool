"""工单详情页「工单结算」面板：只读明细表 + 「编辑工单结算」在工作区新开标签页。

需求（2026-09-28 用户提出）：
1. 结算面板此前只有小计 / 合计，明细必须进编辑页才看得到 —— 现在直接在面板里
   以只读表格展示明细（不可编辑）。
2. 「编辑工单结算」应像日报 / 报销那样在工作区里新开标签页，而不是把当前
   工单详情页整个换掉；下载 PDF / Excel 保持浏览器原生行为。

按渲染结果断言（真跑 /service-orders/<id>），而不是只断言模板源码字符串。
"""

import importlib.util
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent
STATIC = REPO_DIR / "static"


def _strip_tags(html):
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ").strip()


def _settlement_panel(html):
    """截出「工单结算」面板（data-erp-panel="settlement"）的片段。"""
    marker = 'data-erp-panel="settlement"'
    start = html.index(marker)
    # 面板到下一个 </section> 结束
    end = html.index("</section>", start)
    return html[start:end]


def _panel_table(panel):
    """取面板内只读明细表的表头与数据行文本。"""
    start = panel.index("<table")
    body = panel[start : panel.index("</table>", start)]
    thead = body[body.index("<thead>") : body.index("</thead>")]
    tbody = body[body.index("<tbody>") : body.index("</tbody>")]
    headers = [_strip_tags(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)]
    rows = [
        [_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.S)
    ]
    return headers, rows


class SettlementPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location(
            "invoice_tool_settlement_panel_test_app", module_path
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
                values ('99996', 'Settlement Panel Client', 'SP Client', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.order_id = self._insert_order(connection, "SO-SETTLE")
            # 有明细的结算单
            self.settled_order = self._insert_order(connection, "SO-WITH-ITEMS")
            self.rid = self._insert_settlement(connection, self.settled_order)
            self._insert_item(connection, self.rid, "张三", "2026-08-01")
            self._insert_item(connection, self.rid, "李四", "2026-08-02")
            # 无明细的结算单
            self.bare_order = self._insert_order(connection, "SO-BARE")
            self._insert_settlement(connection, self.bare_order)
            connection.commit()
        self.admin = self._client(self.admin_id)

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
                status, start_date, created_by, created_at
            ) values (?, ?, 'Site', 'Test Site', ?, 'open', '2026-08-01', ?, '2026-08-13T00:00:00')
            """,
            (number, self.client_id, number, self.admin_id),
        ).lastrowid

    def _insert_settlement(self, connection, order_id):
        return connection.execute(
            """
            insert into customer_reimbursements (
                service_order_id, file_name, stored_filename, status, created_by, created_at
            ) values (?, 'settle.pdf', 'settle.pdf', 'draft', ?, '2026-08-13T00:00:00')
            """,
            (order_id, self.admin_id),
        ).lastrowid

    def _insert_item(self, connection, rid, worker, project_date):
        connection.execute(
            """
            insert into customer_reimbursement_items (
                customer_reimbursement_id, worker_name, project_date,
                standard_hours, transport_hours, public_transport_hours, overtime_hours, holiday_hours,
                standard_rate, transport_rate, public_transport_rate, overtime_rate, holiday_rate,
                labor_total, lodging, airfare, baggage, rental_car, fuel, parking, taxi,
                miles, mileage_rate, mileage_total, other, total, sort_order,
                auto_lodging, auto_airfare, auto_baggage, auto_rental_car,
                auto_fuel, auto_parking, auto_taxi, auto_other, auto_expense_sources
            ) values (
                ?, ?, ?,
                1, 2, 0, 3, 0,
                100, 10, 0, 50, 0,
                280, 0, 0, 0, 0, 0, 0, 0,
                12, 2, 24, 0, 304, 0,
                0, 0, 0, 0, 0, 0, 0, 0, '{}'
            )
            """,
            (rid, worker, project_date),
        )

    def _page(self, order_id):
        response = self.admin.get(f"/service-orders/{order_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    # --- 需求 1：只读明细表 ---

    def test_items_are_listed_in_the_panel(self):
        """结算单有明细时，详情页面板里直接出现明细行，无需进编辑页。"""
        panel = _settlement_panel(self._page(self.settled_order))
        headers, rows = _panel_table(panel)
        self.assertIn("姓名", headers)
        self.assertIn("合计", headers)
        self.assertEqual(len(rows), 2)
        names = [r[0] for r in rows]
        self.assertEqual(names, ["张三", "李四"])

    def test_item_amounts_match_stored_values(self):
        """行内金额取落库快照（工时费 280、里程费 24、合计 304）。"""
        panel = _settlement_panel(self._page(self.settled_order))
        headers, rows = _panel_table(panel)
        row = rows[0]
        self.assertEqual(row[headers.index("工时费")], "$280.00")
        self.assertEqual(row[headers.index("里程费")], "$24.00")
        self.assertEqual(row[headers.index("合计")], "$304.00")

    def test_panel_table_is_read_only(self):
        """明细表必须是只读的：面板里不应出现任何输入控件。"""
        panel = _settlement_panel(self._page(self.settled_order))
        table = panel[panel.index("<table") : panel.index("</table>")]
        self.assertNotIn("<input", table)
        self.assertNotIn("<select", table)
        self.assertNotIn("<textarea", table)

    def test_settlement_without_items_renders_no_table(self):
        """结算单存在但无明细时不渲染空表格（避免误导）。"""
        panel = _settlement_panel(self._page(self.bare_order))
        self.assertNotIn("<table", panel)

    def test_summary_metrics_still_present(self):
        """原有小计 / 合计指标不能被明细表挤掉。"""
        panel = _settlement_panel(self._page(self.settled_order))
        self.assertIn("工时费小计", panel)
        self.assertIn("差旅费小计", panel)
        self.assertIn("里程费小计", panel)

    # --- 需求 2：编辑按钮新开标签页 ---

    def test_edit_button_opens_in_workspace_tab(self):
        """「编辑工单结算」要带 data-workspace-url（由外壳/脚本开新标签页），
        而不是原来的 window.location.href 整页跳转。"""
        panel = _settlement_panel(self._page(self.settled_order))
        self.assertIn("data-workspace-url=", panel)
        self.assertIn(f"/service-orders/{self.settled_order}/customer-reimbursement", panel)
        # 该按钮不应再使用 window.location.href
        button = panel[panel.index("data-workspace-url=") :]
        button = button[: button.index(">")]
        self.assertNotIn("window.location.href", button)

    def test_generate_button_also_opens_in_workspace_tab(self):
        """没有结算单时的「生成工单结算」同样走工作区标签页。"""
        panel = _settlement_panel(self._page(self.order_id))
        self.assertIn("data-workspace-url=", panel)
        self.assertIn("生成工单结算", panel)

    def test_download_buttons_stay_native(self):
        """下载 PDF / Excel 不能进 iframe 标签，必须保持原生 onclick。"""
        panel = _settlement_panel(self._page(self.settled_order))
        self.assertIn("/customer-reimbursements/1/download", panel)
        self.assertIn(".xlsx", panel)
        # 下载按钮用原生跳转，且不带 data-workspace-url
        for label in ("下载 PDF", "导出 Excel"):
            idx = panel.index(label)
            button = panel[panel.rindex("<button", 0, idx) : idx]
            self.assertIn("window.location.href", button)
            self.assertNotIn("data-workspace-url", button)

    def test_workspace_url_handler_exists_in_shared_script(self):
        """共享脚本要有 data-workspace-url 的处理器（含不在工作区时的降级）。"""
        script = (REPO_DIR / "static" / "selectable-table-actions.js").read_text(encoding="utf-8")
        self.assertIn("[data-workspace-url]", script)
        self.assertIn("workspaceOpen", script)


def _find_chrome():
    for candidate in (
        Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path(os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe")),
    ):
        if candidate.is_file():
            return candidate
    return None


_SHELL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>工作区</title></head>
<body>
<div id="workspaceTabs"></div>
<div id="workspacePages"></div>
<div id="workspaceNotice" hidden></div>
<button id="workspaceRefresh"></button>
<button id="workspaceNoticeRefresh"></button>
<button id="workspaceCloseOthers"></button>
<nav id="topnav"></nav>
<script src="{{STATIC}}/workspace.js"></script>
</body></html>"""

# 内层页：模拟工单详情页的结算面板按钮（带 data-workspace-url）
_PANEL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>工单详情</title></head>
<body><div class="main">
  <section data-erp-panel="settlement">
    <button class="erp-btn" type="button"
            data-workspace-url="/settlement-form.html"
            data-workspace-title="工单结算 · SO-TEST">编辑工单结算</button>
  </section>
</div>
<script src="{{STATIC}}/selectable-table-actions.js"></script>
</body></html>"""


class WorkspaceUrlOpensTabRenderTest(unittest.TestCase):
    """真渲染：点「编辑工单结算」（data-workspace-url）应在工作区里多出一个标签页。"""

    @classmethod
    def setUpClass(cls):
        chrome = _find_chrome()
        if chrome is None:
            raise unittest.SkipTest("找不到 Chrome，跳过真渲染验证")
        cls.chrome = chrome
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.serve_dir = Path(cls.temp_dir.name) / "site"
        cls.serve_dir.mkdir()
        (cls.serve_dir / "static").mkdir()
        for name in ("workspace.js", "selectable-table-actions.js"):
            shutil.copyfile(STATIC / name, cls.serve_dir / "static" / name)
        (cls.serve_dir / "shell.html").write_text(
            _SHELL_HTML.replace("{{STATIC}}", "/static"), encoding="utf-8"
        )
        (cls.serve_dir / "detail-source.html").write_text(
            _PANEL_HTML.replace("{{STATIC}}", "/static"), encoding="utf-8"
        )
        (cls.serve_dir / "settlement-form.html").write_text(
            "<!doctype html><html><head><title>工单结算</title></head>"
            "<body><div class='main'><h1>工单结算</h1></div></body></html>",
            encoding="utf-8",
        )
        (cls.serve_dir / "index.html").write_text(
            "<!doctype html><html><head><title>首页</title></head>"
            "<body><div class='main'><h1>首页</h1></div></body></html>",
            encoding="utf-8",
        )
        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
            *args, directory=str(cls.serve_dir), **kwargs
        )
        handler.log_message = lambda *args, **kwargs: None
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()

    def _run_chrome(self, script):
        probe = self.serve_dir / "probe.html"
        probe.write_text(script, encoding="utf-8")
        out = subprocess.run(
            [str(self.chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=8000", "--dump-dom",
             f"http://127.0.0.1:{self.port}/probe.html"],
            capture_output=True, timeout=90,
        )
        return out.stdout.decode("utf-8", "replace")

    def test_edit_button_opens_a_workspace_tab(self):
        dom = self._run_chrome(self._probe_script())
        marker = re.search(r"PROBE:(\{.*?\})", dom)
        self.assertIsNotNone(marker, f"探针没有输出结果：{dom[:500]}")
        result = json.loads(marker.group(1))
        self.assertTrue(result["clicked"], "没找到 data-workspace-url 按钮")
        self.assertEqual(result["tabsBefore"], 1, "初始应只有 1 个标签页")
        self.assertGreaterEqual(result["tabsAfter"], 2, "点按钮后应多出一个工作区标签页")
        self.assertTrue(result["frameLoaded"], "新标签页里应加载了结算编辑页")
        self.assertIn("settlement-form", result["frameSrc"])

    def _probe_script(self):
        return """<!doctype html>
<html><head><meta charset="utf-8"><title>PROBE:{}</title>
<style>html,body{margin:0} iframe{width:600px;height:400px}</style>
</head>
<body>
<script>
  const result = {clicked: false, tabsBefore: 0, tabsAfter: 0, frameLoaded: false, frameSrc: ''};
  function report() { document.title = 'PROBE:' + JSON.stringify(result); }
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  (async () => {
    const shell = document.createElement('iframe');
    shell.style.cssText = 'width:1000px;height:700px;border:0';
    shell.src = '/shell.html#/detail-source.html';
    document.body.appendChild(shell);
    for (let i = 0; i < 40; i++) {
      await sleep(100);
      try { if (shell.contentDocument.getElementById('workspacePages')) break; } catch (e) {}
    }
    const frames = () => Array.from(shell.contentDocument.querySelectorAll('#workspacePages iframe'));
    for (let i = 0; i < 40; i++) { await sleep(100); if (frames().length) break; }
    result.tabsBefore = frames().length;
    const outer = frames()[0];
    if (!outer) { report(); return; }
    let doc = null;
    for (let i = 0; i < 40; i++) {
      await sleep(100);
      try { doc = outer.contentDocument; } catch (e) {}
      if (doc && doc.querySelector('[data-workspace-url]')) break;
    }
    const button = doc && doc.querySelector('[data-workspace-url]');
    if (!button) { report(); return; }
    button.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    result.clicked = true;
    for (let i = 0; i < 30; i++) {
      await sleep(100);
      if (frames().length > result.tabsBefore) break;
    }
    const all = frames();
    result.tabsAfter = all.length;
    const last = all[all.length - 1];
    if (last) {
      result.frameSrc = last.getAttribute('src') || '';
      for (let i = 0; i < 20; i++) {
        await sleep(100);
        try { if (last.contentDocument.querySelector('h1')) { result.frameLoaded = true; break; } } catch (e) {}
      }
    }
    report();
  })();
</script>
</body></html>"""


if __name__ == "__main__":
    unittest.main()
