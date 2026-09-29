"""工单详情页的「查看」类行操作按钮，应在工作区里新开标签页。

需求（用户提出）：「工单的报销，点查看，应该新开标签页。」

这三个面板（日报 / 报销 / 发票）共用 selectable-table-actions.js 的
`data-row-action` 机制，原实现是 `window.location.href = <明细页>` —— 那是
**在 iframe 内自身跳转**，不会经过工作区外壳的链接拦截，结果把列表所在的标签页
整个换掉（滚动位置、筛选条件全丢）。

修法：外壳在 attach() 时把 `workspaceOpen` 注入到 iframe，按钮优先调它；
不在工作区里跑则退化为浏览器新窗口。

断言分两层：
1. 源码契约（三层：外壳注入 / 按钮调用 / 模板仍是行选择按钮）；
2. 真渲染验证 —— 用无头浏览器把外壳 + 内层页真的跑起来，点击按钮后断言
   外壳里**多出一个标签页 iframe**（而不是内层页被替换）。
"""

import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent
STATIC = REPO_DIR / "static"
TEMPLATES = REPO_DIR / "templates"


def _find_chrome():
    for candidate in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe")),
    ):
        if candidate.is_file():
            return candidate
    return None


class RowActionOpensWorkspaceTabSourceTest(unittest.TestCase):
    """第一层：源码契约（便宜、快，钉住三层调用链）。"""

    def test_shell_injects_workspace_open_into_frames(self):
        js = (STATIC / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("win.workspaceOpen", js)
        # 必须真的接到 shell 的 open()，而不是另起一套
        self.assertRegex(js, r"win\.workspaceOpen\s*=\s*\([^)]*\)\s*=>\s*open\(")

    def test_row_action_navigation_prefers_workspace_open(self):
        js = (STATIC / "selectable-table-actions.js").read_text(encoding="utf-8")
        self.assertIn("window.workspaceOpen", js)
        # 明细跳转不能再用 location.href 顶掉当前页（下载类除外，走 else 分支）
        self.assertIn("window.location.href = value;", js)
        self.assertIn("isDownload", js)

    def test_dialog_mode_still_short_circuits(self):
        """dialog 模式（buyers / users 的「编辑」）不能被改成开标签。"""
        js = (STATIC / "selectable-table-actions.js").read_text(encoding="utf-8")
        self.assertRegex(js, r'mode === "dialog"[\s\S]{0,120}showModal\(\)[\s\S]{0,40}return;')

    def test_templates_keep_row_select_buttons(self):
        html = (TEMPLATES / "service_order_detail.html").read_text(encoding="utf-8")
        # 报销 / 发票面板的「查看」
        self.assertGreaterEqual(html.count('data-row-action="detailUrl"'), 2)
        # 日报面板的「查看」/「编辑」
        self.assertIn('data-row-action="viewUrl"', html)
        self.assertIn('data-row-action="editUrl"', html)

    def test_daily_report_mobile_cards_can_drive_row_actions(self):
        html = (TEMPLATES / "service_order_detail.html").read_text(encoding="utf-8")
        js = (STATIC / "selectable-table-actions.js").read_text(encoding="utf-8")
        css = (STATIC / "erp-ui.css").read_text(encoding="utf-8")

        self.assertIn('class="erp-cards" data-selectable-cards', html)
        self.assertIn("data-selectable-card", html)
        for attribute in (
            "data-view-url=",
            "data-edit-url=",
            "data-copy-url=",
            "data-export-url=",
            "data-delete-url=",
        ):
            self.assertIn(attribute, html)
        self.assertIn('[data-selectable-cards]', js)
        self.assertIn('[data-selectable-card][data-row-id]', js)
        self.assertIn('.erp-card[data-selectable-card].is-selected', css)


class RowActionOpensWorkspaceTabRenderTest(unittest.TestCase):
    """第二层：真渲染 —— 起一个真服务器 + 无头 Chrome，点按钮看是否多出标签页。"""

    @classmethod
    def setUpClass(cls):
        chrome = _find_chrome()
        if chrome is None:
            raise unittest.SkipTest("找不到 Chrome，跳过真渲染验证")
        cls.chrome = chrome
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.serve_dir = Path(cls.temp_dir.name) / "site"
        cls.serve_dir.mkdir()

        # 外壳页 + 内层页：都靠本地静态文件模拟，只保留行为关键部分。
        # 外壳直接引用仓库里真实的 workspace.js / 两个静态脚本。
        (cls.serve_dir / "static").mkdir()
        for name in ("workspace.js", "selectable-table-actions.js"):
            shutil.copyfile(STATIC / name, cls.serve_dir / "static" / name)

        (cls.serve_dir / "shell.html").write_text(
            SHELL_HTML.replace("{{STATIC}}", "/static"), encoding="utf-8"
        )
        # 内层页：一个「查看」按钮 + 一行数据，行为与 service_order_detail.html 一致
        (cls.serve_dir / "detail-source.html").write_text(
            DETAIL_HTML.replace("{{STATIC}}", "/static"), encoding="utf-8"
        )
        # 目标明细页：只用来确认标签页真的指过来
        (cls.serve_dir / "expense-detail.html").write_text(
            "<!doctype html><html><head><title>报销明细</title></head>"
            "<body><div class='main'><h1>报销明细</h1></div></body></html>",
            encoding="utf-8",
        )
        # 外壳的默认首页（open('/') 会请求它）
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
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()

    def _run_chrome(self, script):
        """用无头 Chrome 跑一段页面脚本，返回它 dump 出来的 DOM。"""
        # 探针必须落在服务器根目录里，否则 404（服务器只服务 serve_dir）。
        probe = self.serve_dir / "probe.html"
        probe.write_text(script, encoding="utf-8")
        url = f"http://127.0.0.1:{self.port}/probe.html"
        out = subprocess.run(
            [
                str(self.chrome),
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--virtual-time-budget=8000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            timeout=90,
        )
        return out.stdout.decode("utf-8", "replace")

    def test_clicking_view_opens_a_workspace_tab(self):
        dom = self._run_chrome(self._probe_script())
        # 探针把结果写进 <title>，dump-dom 能读到
        marker = re.search(r"PROBE:(\{.*?\})", dom)
        self.assertIsNotNone(marker, f"探针没有输出结果：{dom[:500]}")
        result = json.loads(marker.group(1))
        self.assertTrue(result["clicked"], "没找到可点的「查看」按钮")
        self.assertEqual(result["tabsBefore"], 1, "初始应只有 1 个标签页")
        self.assertGreaterEqual(
            result["tabsAfter"], 2, "点「查看」后应多出一个工作区标签页"
        )
        self.assertTrue(result["frameLoaded"], "新标签页里应加载了报销明细页")
        self.assertIn("expense-detail", result["frameSrc"])

    def test_clicking_daily_report_card_selects_it_and_enables_toolbar(self):
        dom = self._run_chrome("""<!doctype html>
<html><head><meta charset="utf-8"><title>PROBE:{}</title></head>
<body>
  <section data-selectable-scope>
    <button type="button" data-row-action="editUrl" disabled>编辑</button>
    <div data-selectable-cards>
      <article tabindex="0" role="button" aria-selected="false"
               data-selectable-card data-row-id="7" data-row-label="2026-08-28"
               data-edit-url="/reports/7/edit">日报卡片</article>
    </div>
  </section>
  <script src="/static/selectable-table-actions.js"></script>
  <script>
    const card = document.querySelector('[data-selectable-card]');
    const button = document.querySelector('[data-row-action="editUrl"]');
    card.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    document.title = 'PROBE:' + JSON.stringify({
      selected: card.classList.contains('is-selected'),
      ariaSelected: card.getAttribute('aria-selected'),
      buttonEnabled: !button.disabled,
      selectedValue: button.dataset.selectedValue || ''
    });
  </script>
</body></html>""")
        marker = re.search(r"PROBE:(\{.*?\})", dom)
        self.assertIsNotNone(marker, f"卡片选择探针没有输出结果：{dom[:500]}")
        result = json.loads(marker.group(1))
        self.assertTrue(result["selected"])
        self.assertEqual(result["ariaSelected"], "true")
        self.assertTrue(result["buttonEnabled"])
        self.assertEqual(result["selectedValue"], "/reports/7/edit")

    def _probe_script(self):
        """探针：加载真实外壳页，再把内层 iframe 换成我们的「工单详情」页。

        直接用 shell.html（而不是在探针里重写一遍外壳 DOM），
        这样断言的是真实的 workspace.js 行为。
        """
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>PROBE:{{}}</title>
<style>html,body{{margin:0}} iframe{{width:600px;height:400px}}</style>
</head>
<body>
<script>
  const result = {{clicked: false, tabsBefore: 0, tabsAfter: 0, frameLoaded: false, frameSrc: ''}};
  function report() {{ document.title = 'PROBE:' + JSON.stringify(result); }}
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  (async () => {{
    // 内嵌真实外壳页，并让它启动时直接打开「工单详情」页
    const shell = document.createElement('iframe');
    shell.style.cssText = 'width:1000px;height:700px;border:0';
    shell.src = '/shell.html#/detail-source.html';
    document.body.appendChild(shell);
    for (let i = 0; i < 40; i++) {{
      await sleep(100);
      try {{ if (shell.contentDocument.getElementById('workspacePages')) break; }} catch (e) {{}}
    }}
    const frames = () => Array.from(shell.contentDocument.querySelectorAll('#workspacePages iframe'));
    for (let i = 0; i < 40; i++) {{
      await sleep(100);
      if (frames().length) break;
    }}
    result.tabsBefore = frames().length;
    const outer = frames()[0];
    if (!outer) {{ report(); return; }}
    // 等内层「工单详情」页加载完，在里面点「查看」
    let doc = null;
    for (let i = 0; i < 40; i++) {{
      await sleep(100);
      try {{ doc = outer.contentDocument; }} catch (e) {{}}
      if (doc && doc.querySelector('[data-row-action="detailUrl"]')) break;
    }}
    const button = doc && doc.querySelector('[data-row-action="detailUrl"]');
    const row = doc && doc.querySelector('[data-selectable-table] tbody tr[data-row-id]');
    if (!button || !row) {{ report(); return; }}
    // 脚本靠点击行来选中，再点「查看」
    row.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));
    button.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));
    result.clicked = true;
    for (let i = 0; i < 30; i++) {{
      await sleep(100);
      if (frames().length > result.tabsBefore) break;
    }}
    const all = frames();
    result.tabsAfter = all.length;
    const last = all[all.length - 1];
    if (last) {{
      result.frameSrc = last.getAttribute('src') || '';
      for (let i = 0; i < 20; i++) {{
        await sleep(100);
        try {{
          if (last.contentDocument.querySelector('h1')) {{ result.frameLoaded = true; break; }}
        }} catch (e) {{}}
      }}
    }}
    report();
    // 让 dump-dom 拿到稳定的最终 DOM
    document.documentElement.setAttribute('data-probe-done', '1');
  }})();
</script>
</body></html>"""


SHELL_HTML = """<!doctype html>
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

DETAIL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>工单详情</title></head>
<body><div class="main">
  <section data-selectable-scope>
    <button class="erp-btn" type="button" data-row-action="detailUrl" disabled>查看</button>
    <table data-selectable-table>
      <thead><tr><th>报销编号</th></tr></thead>
      <tbody>
        <tr tabindex="0" data-row-id="1" data-row-label="EX000001"
            data-detail-url="/expense-detail.html">
          <td>EX000001</td>
        </tr>
      </tbody>
    </table>
  </section>
</div>
<script src="{{STATIC}}/selectable-table-actions.js"></script>
</body></html>"""


if __name__ == "__main__":
    unittest.main()
