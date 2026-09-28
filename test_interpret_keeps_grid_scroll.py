"""报销详情页点「智能解读」后，页面不应「刷新并向上滚动」。

用户报告（2026-09-28，附截图）：报销明细里点「智能解读」按钮，页面会刷新并向上滚动。

根因（实测定位，非推测）：
1. 报销明细表格被 system-grid.js 镜像成 Tabulator（`formatter` 走 `mirror()`）；
2. 点了「智能解读」后，handler 把原单元格里的按钮 `remove()` 并插入一个
   `<a href="#">解读结果</a>` —— 这是原表格的 **childList 变化**；
3. `MutationObserver` 只特判了「行选中」这一种可视变更，其余一律 `schedule()`
   → `sync()` → **`grid.replaceData()`**；
4. `replaceData` 重建整张表，**把表格内部滚动位置清零**（整页 pageY 不变，
   但表格内容跳回顶部，用户看到的就是「页面往上跳」）。

修法：在 observer 里增加 `controlsOnly` 分支 —— 变更全部落在
`[data-interpret-cell]` 内部时，只对受影响的行 `reformat()`（重跑 formatter
→ mirror 更新该格），跳过 `replaceData`。

断言分两层：
1. 源码契约（便宜）：observer 里有 controlsOnly 分支且走 reformat；
2. 真渲染（关键）：无头 Chrome 起真页面，点镜像里的按钮后断言
   **表格内部 scrollTop 保持不变**、且镜像内容确实更新成「解读结果」。
"""

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


def _find_chrome():
    for candidate in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe")),
    ):
        if candidate.is_file():
            return candidate
    return None


class InterpretKeepsGridScrollSourceTest(unittest.TestCase):
    """源码契约：observer 必须把「控件自我替换」当可视变更处理。"""

    def setUp(self):
        self.js = (STATIC / "system-grid.js").read_text(encoding="utf-8")

    def test_observer_has_controls_only_branch(self):
        self.assertIn("controlsOnly", self.js)
        self.assertIn("data-interpret-cell", self.js)

    def test_controls_only_uses_reformat_not_replace_data(self):
        """该分支内必须走 reformat()，不能落到 replaceData。"""
        start = self.js.index("const controlsOnly")
        end = self.js.index("this.schedule();", start)
        branch = self.js[start:end]
        self.assertIn("reformat()", branch)
        self.assertNotIn("replaceData", branch)

    def test_selection_only_branch_still_present(self):
        """既有的行选中特判不能被覆盖掉。"""
        self.assertIn("selectionOnly", self.js)


class InterpretKeepsGridScrollRenderTest(unittest.TestCase):
    """真渲染：点镜像里的「智能解读」后，表格内部滚动位置必须保住。"""

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
        shutil.copyfile(STATIC / "system-grid.js", cls.serve_dir / "static" / "system-grid.js")
        shutil.copyfile(STATIC / "system-grid.css", cls.serve_dir / "static" / "system-grid.css")
        (cls.serve_dir / "static" / "vendor").mkdir()
        (cls.serve_dir / "static" / "vendor" / "tabulator").mkdir()
        for name in ("tabulator.min.js", "tabulator.min.css"):
            shutil.copyfile(
                STATIC / "vendor" / "tabulator" / name,
                cls.serve_dir / "static" / "vendor" / "tabulator" / name,
            )
        (cls.serve_dir / "detail.html").write_text(_DETAIL_HTML, encoding="utf-8")
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
             "--window-size=1000,700", "--virtual-time-budget=6000", "--dump-dom",
             f"http://127.0.0.1:{self.port}/probe.html"],
            capture_output=True, timeout=90,
        )
        return out.stdout.decode("utf-8", "replace")

    def _probe(self):
        # 直接打开 detail.html，在页内等待镜像完成 → 滚动 → 点按钮 → 采样
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>PROBE:{{}}</title></head>
<body>
<iframe id="f" src="/detail.html" style="width:1000px;height:700px;border:0"></iframe>
<script>
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  (async () => {{
    const out = {{}};
    const f = document.getElementById('f');
    for (let i = 0; i < 40; i++) {{ await sleep(100); try {{ if (f.contentDocument.querySelector('.system-grid')) break; }} catch(e) {{}} }}
    const d = f.contentDocument, w = f.contentWindow;
    const scroller = d.getElementById('gridScroll');
    // 等 Tabulator 建完
    for (let i = 0; i < 40; i++) {{ await sleep(100); if (d.querySelector('.system-grid .tabulator-row')) break; }}
    out.mirrorRows = d.querySelectorAll('.system-grid .tabulator-row').length;
    scroller.scrollTop = 120;
    out.scrollSet = Math.round(scroller.scrollTop);
    const btn = d.querySelector('.system-grid [data-interpret]');
    out.btnFound = !!btn;
    if (btn) btn.dispatchEvent(new w.MouseEvent('click', {{bubbles:true, cancelable:true}}));
    await sleep(700);
    out.scrollAfter = Math.round(scroller.scrollTop);
    const link = d.querySelector('.system-grid .grid-cell-content [data-interpretation-result]');
    out.mirrorUpdated = !!link;
    out.mirrorText = link ? link.textContent : '';
    document.title = 'PROBE:' + JSON.stringify(out);
  }})();
</script>
</body></html>"""

    def test_scroll_is_preserved_when_clicking_interpret(self):
        dom = self._run_chrome(self._probe())
        marker = re.search(r"PROBE:(\{.*?\})", dom)
        self.assertIsNotNone(marker, f"探针没有输出结果：{dom[:500]}")
        result = json.loads(marker.group(1))
        self.assertTrue(result["btnFound"], "没找到镜像里的「智能解读」按钮")
        self.assertGreaterEqual(result["mirrorRows"], 2, "镜像表格应有多行")
        self.assertEqual(result["scrollSet"], 120, "前置：表格内部已滚到 120")
        # 核心断言：点击后表格内部滚动位置必须保住（修复前会被 replaceData 清零）
        self.assertEqual(
            result["scrollAfter"], 120,
            "点「智能解读」后表格内部滚动被重置 —— 这正是用户看到的「页面往上跳」",
        )
        # 同时镜像内容必须真的更新（不能为了保滚动而不更新界面）
        self.assertTrue(result["mirrorUpdated"], "镜像单元格没更新成「解读结果」")
        self.assertEqual(result["mirrorText"], "解读结果")


_DETAIL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>报销详情</title>
<link rel="stylesheet" href="/static/vendor/tabulator/tabulator.min.css">
<link rel="stylesheet" href="/static/system-grid.css">
<style>
  body { margin: 0; font-family: sans-serif; }
  .page-spacer { height: 900px; background: linear-gradient(#eef,#fee); }
  .grid-scroll { height: 260px; overflow-y: auto; border: 1px solid #ccc; }
  table { width: 100%; }
</style>
</head>
<body>
<div class="page-spacer">页面顶部内容</div>
<div class="grid-scroll" id="gridScroll">
  <table data-system-grid id="srcTable">
    <thead><tr><th>员工报销项目</th><th>明细说明</th><th>金额</th><th>对应附件</th></tr></thead>
    <tbody>
      <tr><td>Express Delivery Fees</td><td>给陈总报销</td><td>$35.00</td>
        <td><span class="interpret-controls" data-interpret-cell="1">
          <button class="small" type="button" data-interpret="1">智能解读</button>
        </span></td></tr>
      <tr><td>MRO Supplies</td><td>配件及耗材</td><td>$95.26</td>
        <td><span class="interpret-controls" data-interpret-cell="2">
          <button class="small" type="button" data-interpret="2">智能解读</button>
        </span></td></tr>
      <tr><td>MRO Supplies</td><td>配件及耗材</td><td>$256.49</td>
        <td><span class="interpret-controls" data-interpret-cell="3">
          <button class="small" type="button" data-interpret="3">智能解读</button>
        </span></td></tr>
      <tr><td>Row 4</td><td>x</td><td>$10</td><td>—</td></tr>
      <tr><td>Row 5</td><td>x</td><td>$10</td><td>—</td></tr>
      <tr><td>Row 6</td><td>x</td><td>$10</td><td>—</td></tr>
      <tr><td>Row 7</td><td>x</td><td>$10</td><td>—</td></tr>
      <tr><td>Row 8</td><td>x</td><td>$10</td><td>—</td></tr>
    </tbody>
  </table>
</div>
<div style="height:1200px;background:#efe">页面底部内容</div>

<script src="/static/vendor/tabulator/tabulator.min.js"></script>
<script src="/static/system-grid.js"></script>
<script>
// 复刻 expense_detail.html 的 handler（去掉 fetch，保留 DOM 变更）
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-interpret]");
  if (!button) return;
  event.preventDefault();
  const attachmentId = button.dataset.interpret;
  const cell = button.closest("[data-interpret-cell]");
  if (cell) {
    button.remove();
    const link = document.createElement("a");
    link.href = "#";
    link.dataset.interpretationResult = attachmentId;
    link.textContent = "解读结果";
    cell.appendChild(link);
  }
});
</script>
</body></html>"""


if __name__ == "__main__":
    unittest.main()
