"""消息页勾选与批量标记的**真渲染**验证（v0.1.329）。

为什么必须真渲染：消息表格会被 system-grid.js 镜像成 Tabulator。点镜像出来的
checkbox 时，镜像层把状态写回原表并派发 change → `sync()` 的签名里含
`input.checked` → 不加拦截就会 `replaceData()` 重建整张表：**滚动清零、
刚勾好的选中态全丢**。这正是 0.1.328 修过的那类「点一下页面往上跳」，
换了个触发点（checkbox 而不是按钮↔链接）又冒出来。

断言（真 Chrome）：
1. 勾选一行后，表格内部 scrollTop 不变、该行仍是选中态、按钮从禁用变可用；
2. 「全选」勾上后所有行都选中，且滚动位置仍然不变。

前四行是源码契约，这一层是行为契约 —— 只有真渲染才能证明
`onlyCheckboxes` 短路分支真的生效。
"""

import http.server
import json
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


import os  # noqa: E402  (放在 _find_chrome 之后，避免与下方 unittest 用法混淆)


class MessagesSelectionRenderTest(unittest.TestCase):
    """真渲染：勾选 checkbox 不能重置表格滚动。"""

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
        (cls.serve_dir / "messages.html").write_text(_MESSAGES_HTML, encoding="utf-8")
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
             "--window-size=1100,700", "--virtual-time-budget=6000", "--dump-dom",
             f"http://127.0.0.1:{self.port}/probe.html"],
            capture_output=True, timeout=90,
        )
        return out.stdout.decode("utf-8", "replace")

    def _probe(self):
        return """<!doctype html>
<html><head><meta charset="utf-8"><title>PROBE:{}</title></head>
<body>
<iframe id="f" src="/messages.html" style="width:1100px;height:700px;border:0"></iframe>
<script>
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  (async () => {
    const out = {};
    const f = document.getElementById('f');
    for (let i = 0; i < 40; i++) { await sleep(100); try { if (f.contentDocument.querySelector('.system-grid')) break; } catch(e) {} }
    const d = f.contentDocument, w = f.contentWindow;
    for (let i = 0; i < 40; i++) { await sleep(100); if (d.querySelector('.system-grid .tabulator-row')) break; }
    const scroller = d.getElementById('gridScroll');
    out.mirrorRows = d.querySelectorAll('.system-grid .tabulator-row').length;
    scroller.scrollTop = 100;
    out.scrollSet = Math.round(scroller.scrollTop);

    // 点镜像里的第一个 checkbox（镜像层会写回原表并派发 change）
    const mirrorBox = d.querySelector('.system-grid .tabulator-row input[type=checkbox]');
    out.mirrorBoxFound = !!mirrorBox;
    if (mirrorBox) mirrorBox.dispatchEvent(new w.MouseEvent('click', {bubbles:true, cancelable:true}));
    await sleep(600);
    out.scrollAfterRowClick = Math.round(scroller.scrollTop);
    const sourceBoxes = [...d.querySelectorAll('[data-message-select]')].filter(b => !b.closest('.system-grid'));
    out.sourceTotal = sourceBoxes.length;
    out.sourceCheckedAfterRowClick = sourceBoxes.filter(b => b.checked).length;
    out.markReadDisabledAfterRowClick = d.querySelector('[data-message-mark-selected][data-mark-read="1"]').disabled;
    out.selectedLabel = d.querySelector('[data-message-selected-count]').textContent;

    // 再点「全选」
    const all = d.querySelector('[data-message-select-all]');
    all.checked = true;
    all.dispatchEvent(new w.Event('change', {bubbles:true}));
    await sleep(600);
    out.scrollAfterSelectAll = Math.round(scroller.scrollTop);
    const boxes2 = [...d.querySelectorAll('[data-message-select]')].filter(b => !b.closest('.system-grid'));
    out.sourceCheckedAfterSelectAll = boxes2.filter(b => b.checked).length;
    out.totalCheckboxes = boxes2.length;
    out.allCheckedAfterSelectAll = d.querySelector('[data-message-select-all]').checked;

    document.title = 'PROBE:' + JSON.stringify(out);
  })();
</script>
</body></html>"""

    def test_checkbox_click_keeps_grid_scroll_and_selection(self):
        dom = self._run_chrome(self._probe())
        marker = re.search(r"PROBE:(\{.*?\})", dom)
        self.assertIsNotNone(marker, f"探针没有输出结果：{dom[:600]}")
        result = json.loads(marker.group(1))
        self.assertTrue(result["mirrorBoxFound"], "镜像表格里没有 checkbox")
        self.assertGreaterEqual(result["mirrorRows"], 2, "镜像表格应有多行")
        self.assertEqual(result["scrollSet"], 100, "前置：表格已滚到 100")
        # 核心断言 1：勾选后滚动位置必须保住
        self.assertEqual(
            result["scrollAfterRowClick"], 100,
            "勾选消息后表格滚动被重置 —— checkbox 变化走了 replaceData",
        )
        # 核心断言 2：勾选态必须真的落到原表（否则提交时会漏行）。
        # 这里同时钉住「不能把镜像副本也数进来」——镜像行与原表在同一个
        # document 里，只查 [data-message-select] 会把一行算成两行。
        self.assertEqual(
            result["sourceCheckedAfterRowClick"], 1,
            "勾选没有写回原表、或把镜像副本重复计数了",
        )
        self.assertEqual(
            result["sourceTotal"], _SEED_COUNT,
            f"原表应有 {_SEED_COUNT} 个勾选框，实际 {result['sourceTotal']}（多出来的多半是镜像副本）",
        )
        # 核心断言 3：按钮要跟着启用，且状态栏显示已选条数
        self.assertFalse(
            result["markReadDisabledAfterRowClick"],
            "勾选后「标为已读」按钮仍禁用",
        )
        self.assertIn("已选 1 条", result["selectedLabel"])

        # 核心断言 4：全选也要不重置滚动、并把所有行勾上
        self.assertEqual(
            result["scrollAfterSelectAll"], 100,
            "全选后表格滚动被重置",
        )
        self.assertEqual(
            result["sourceCheckedAfterSelectAll"], result["totalCheckboxes"],
            "全选没有把所有行勾上",
        )
        self.assertTrue(result["allCheckedAfterSelectAll"], "全选后表头勾选框应保持勾上")
        self.assertEqual(result["totalCheckboxes"], _SEED_COUNT)


_SEED_COUNT = 10


def _message_rows():
    rows = []
    for i in range(_SEED_COUNT):
        unread = i % 2 == 0
        rows.append(
            f'      <tr class="{"message-unread" if unread else ""}" '
            f'data-message-row data-is-read="{0 if unread else 1}">\n'
            f'        <td class="message-select-col">'
            f'<input type="checkbox" data-message-select value="{i + 1}"></td>\n'
            f'        <td data-message-status>{"未读" if unread else "已读"}</td>\n'
            f'        <td><a href="#" data-message-link data-message-title>测试消息 {i + 1}</a></td>\n'
            f'        <td data-message-body>正文内容 {i + 1}</td>\n'
            f'        <td>2026-09-28 10:0{i}</td>\n'
            f'      </tr>\n'
        )
    return "".join(rows)


_MESSAGES_HTML = (
    """<!doctype html>
<html><head><meta charset="utf-8"><title>消息</title>
<link rel="stylesheet" href="/static/vendor/tabulator/tabulator.min.css">
<link rel="stylesheet" href="/static/system-grid.css">
<style>
  body { margin: 0; font-family: sans-serif; }
  .grid-scroll { height: 260px; overflow-y: auto; border: 1px solid #ccc; }
  table { width: 100%; }
  .message-select-col { width: 2.4rem; text-align: center; }
</style>
</head>
<body>
<div class="grid-scroll" id="gridScroll">
  <table data-system-grid id="srcTable">
    <thead><tr>
      <th class="message-select-col"><input type="checkbox" data-message-select-all></th>
      <th>状态</th><th>标题</th><th>内容</th><th>时间</th>
    </tr></thead>
    <tbody>
"""
    + _message_rows()
    + """    </tbody>
  </table>
</div>
<footer>
  <button type="button" data-message-mark-selected data-mark-read="1" disabled>标为已读</button>
  <button type="button" data-message-mark-selected data-mark-read="0" disabled>标为未读</button>
  <span data-message-selected-count></span>
</footer>
<script src="/static/vendor/tabulator/tabulator.min.js"></script>
<script src="/static/system-grid.js"></script>
<script>
// 复刻 messages.js 的勾选逻辑（只保留选中态与按钮联动，不真的提交）
// 注意排除镜像副本（.system-grid 里的行同在一个 document，会重复计数）
function messageSourceCheckboxes() {
  return [...document.querySelectorAll('[data-message-select]')].filter((box) => !box.closest('.system-grid'));
}
function messageSelection() {
  const boxes = messageSourceCheckboxes();
  const selected = boxes.filter((box) => box.checked);
  for (const button of document.querySelectorAll('[data-message-mark-selected]')) {
    button.disabled = selected.length === 0;
  }
  const all = document.querySelector('[data-message-select-all]');
  if (all) { all.checked = boxes.length > 0 && selected.length === boxes.length; }
  const count = document.querySelector('[data-message-selected-count]');
  if (count) count.textContent = selected.length ? `已选 ${selected.length} 条` : '';
}
document.querySelector('[data-message-select-all]')?.addEventListener('change', (event) => {
  const checked = event.target.checked;
  messageSourceCheckboxes().forEach((box) => {
    box.checked = checked;
    box.dispatchEvent(new Event('change', {bubbles: true}));
  });
  messageSelection();
});
document.addEventListener('change', (event) => {
  if (event.target.closest('[data-message-select]')) messageSelection();
});
messageSelection();
</script>
</body></html>"""
)



if __name__ == "__main__":
    unittest.main()
