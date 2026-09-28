"""日报「里程佐证」附件删除不应让页面滚回最上面（v0.1.330）。

用户报告（原话）：日报里的里程佐证图片删除一张，页面就自动滚到最上面，不方便。

根因（非推测）：删除按钮是整张日报表单的 submit 按钮（formaction 指向
`delete_report_attachment`），服务端删完 redirect 回
`edit_service_report + '#report-mileage-proof'`。整页重载必然先重绘到顶部、
再跳到锚点 —— 用户看到的就是「删一张图，页面滚回最上面」，而且表单里尚未
保存的编辑会被一并丢掉。

修法：service-report.js 拦下点击，带 X-Requested-With 走 fetch；后端按该头
返回 JSON（与 delete_invoice_attachment 同一约定），前端只把这一行从 DOM
移除。滚动位置与表单状态都不动。非 JS / 请求失败时降级为原生表单提交。

断言分三层：
1. 后端契约：AJAX 请求返回 JSON，普通表单 POST 仍 302 回锚点；
2. 模板/脚本契约：按钮带 data-mileage-proof-delete、没有内联 onclick、
   JS 里有 fetch 就地删行；
3. 真渲染：删一行后表格内部 scrollTop 不变、该行确实消失、其余行还在。
"""

import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
STATIC = REPO / "static"
os.environ.setdefault("ADMIN_EMAIL", "pytest-admin@example.invalid")
os.environ.setdefault("ADMIN_PASSWORD", "pytest-password")


def read(*parts):
    return (REPO.joinpath(*parts)).read_text(encoding="utf-8")


def _find_chrome():
    for candidate in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe")),
    ):
        if candidate.is_file():
            return candidate
    return None


class MileageProofDeleteBackendTest(unittest.TestCase):
    """后端：AJAX 与非 AJAX 两条分支。"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp_dir.name)
        shutil.copyfile(REPO / "app.py", tmp / "app.py")
        cls._inserted_paths = [str(tmp)]
        sys.path.insert(0, str(tmp))
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        spec = importlib.util.spec_from_file_location("invoice_tool_mileage_test", tmp / "app.py")
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
            order = db.execute(
                "select id from service_orders order by id limit 1"
            ).fetchone()
            if order is None:
                raise unittest.SkipTest("测试库里没有可用工单，跳过")
            cls.order_id = order["id"]

    @classmethod
    def tearDownClass(cls):
        for path in getattr(cls, "_inserted_paths", []):
            if path in sys.path:
                sys.path.remove(path)
        cls.temp_dir.cleanup()

    def setUp(self):
        """造一份日报 + 2 个里程佐证附件（含真实文件，删除要落盘）。"""
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("delete from service_report_attachments where report_id in"
                       " (select id from service_reports where service_order_id = ?)",
                       (self.order_id,))
            db.commit()
            cursor = db.execute(
                "insert into service_reports (service_order_id, report_date, created_by, created_at, updated_at)"
                " values (?, ?, ?, ?, ?)",
                (self.order_id, "2026-09-01", self.admin_id, self.module.now(), self.module.now()),
            )
            self.report_id = cursor.lastrowid
            db.commit()
            self.attachment_ids = []
            for index in range(2):
                stored = f"mileage_proof/evidence-{index}.png"
                target = self.module.report_attachment_path(
                    {"report_id": self.report_id, "stored_filename": stored}
                )
                os.makedirs(os.path.dirname(target), exist_ok=True)
                Path(target).write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
                att = db.execute(
                    "insert into service_report_attachments"
                    " (report_id, category, original_filename, stored_filename, content_type, uploaded_by, uploaded_at)"
                    " values (?, 'mileage_proof', ?, ?, 'image/png', ?, ?)",
                    (self.report_id, f"佐证{index}.png", stored, self.admin_id, self.module.now()),
                )
                self.attachment_ids.append(att.lastrowid)
            db.commit()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin_id

    def remaining_ids(self):
        with self.module.app.app_context():
            rows = self.module.db().execute(
                "select id from service_report_attachments where report_id = ? order by id",
                (self.report_id,),
            ).fetchall()
            return [row["id"] for row in rows]

    # ─── AJAX 分支 ────────────────────────────────────────────────────
    def test_ajax_delete_returns_json_and_removes_row(self):
        response = self.client.post(
            f"/service-report-attachments/{self.attachment_ids[0]}/delete",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200, "AJAX 删除应返回 200 JSON，不是 302")
        self.assertTrue(response.is_json, "AJAX 删除必须返回 JSON")
        payload = response.get_json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("deleted"), self.attachment_ids[0])
        self.assertEqual(self.remaining_ids(), [self.attachment_ids[1]])

    def test_ajax_delete_removes_file_from_disk(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select * from service_report_attachments where id = ?", (self.attachment_ids[0],)
            ).fetchone()
            path = self.module.report_attachment_path(row)
        self.assertTrue(os.path.isfile(path), "前置：文件应存在")
        self.client.post(
            f"/service-report-attachments/{self.attachment_ids[0]}/delete",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertFalse(os.path.isfile(path), "删除后磁盘文件应被清掉")

    # ─── 非 AJAX 分支（降级路径必须保持可用）──────────────────────────
    def test_plain_form_post_still_redirects_to_anchor(self):
        response = self.client.post(
            f"/service-report-attachments/{self.attachment_ids[0]}/delete",
            data={"redirect_anchor": "#report-mileage-proof"},
        )
        self.assertEqual(response.status_code, 302, "普通表单提交仍应重定向")
        location = response.headers["Location"]
        self.assertIn("/service-reports/", location)
        self.assertTrue(location.endswith("#report-mileage-proof"), f"锚点丢了：{location}")
        self.assertEqual(self.remaining_ids(), [self.attachment_ids[1]])

    def test_plain_post_rejects_unsafe_anchor(self):
        response = self.client.post(
            f"/service-report-attachments/{self.attachment_ids[0]}/delete",
            data={"redirect_anchor": "javascript:alert(1)"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("javascript:", response.headers["Location"])

    def test_delete_missing_attachment_is_404(self):
        response = self.client.post(
            "/service-report-attachments/99999999/delete",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_ajax_delete_is_rejected(self):
        anonymous = self.module.app.test_client()
        response = anonymous.post(
            f"/service-report-attachments/{self.attachment_ids[0]}/delete",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("Location", ""))
        self.assertEqual(len(self.remaining_ids()), 2, "未登录不应删掉任何附件")


class MileageProofDeleteSourceContractTest(unittest.TestCase):
    """模板与脚本契约：钩子、去掉内联 onclick、走 fetch。"""

    def setUp(self):
        self.template = read("templates", "service_report_form.html")
        self.script = read("static", "service-report.js")

    def button_block(self):
        """整段 <button>…</button>。

        注意必须从 <button 起切：form= / formaction= / formmethod= /
        formnovalidate 都写在 data-mileage-proof-delete 之前的同一标签里，
        从 data- 属性起切会把它们整段漏掉（这正是本测试最初误报的原因）。
        """
        start = self.template.rindex("<button", 0, self.template.index("data-mileage-proof-delete="))
        return self.template[start: self.template.index("</button>", start)]

    def test_button_carries_inplace_delete_hook(self):
        self.assertIn("data-mileage-proof-delete=", self.template)

    def test_button_has_no_inline_onclick_confirm(self):
        # 内联 onclick 的 return false 只取消默认行为，拦不住冒泡到 document 的
        # 处理器 —— 用户点「取消」仍会发出删除请求。
        self.assertNotIn(
            "onclick=", self.button_block(), "确认弹窗应搬到 JS，不能留在内联 onclick 里"
        )

    def test_button_keeps_native_fallback_attributes(self):
        block = self.button_block()
        for attr in ("form=", "formaction=", "formmethod=", "formnovalidate"):
            self.assertIn(attr, block, f"降级路径缺少 {attr}")

    def test_script_sends_ajax_header(self):
        self.assertIn('"X-Requested-With": "XMLHttpRequest"', self.script)

    def test_script_removes_row_without_reload(self):
        block = self.script[self.script.index("data-mileage-proof-delete"):]
        self.assertIn("row?.remove()", block)
        self.assertNotIn("location.reload()", block, "就地删除不该整页刷新")

    def test_script_confirms_before_deleting(self):
        block = self.script[self.script.index("data-mileage-proof-delete"):]
        self.assertIn("uiConfirm", block)
        self.assertLess(
            block.index("uiConfirm"),
            block.index("fetch(url"),
            "确认必须发生在发请求之前",
        )

    def test_script_falls_back_to_native_submit(self):
        block = self.script[self.script.index("data-mileage-proof-delete"):]
        self.assertIn("button.form.submit()", block, "请求失败时应降级为原生提交")


class _MileageEndpointHandler(http.server.SimpleHTTPRequestHandler):
    """同时提供静态文件 + 模拟后端的 /delete 端点。

    POST /service-report-attachments/<id>/delete：
      - 带 X-Requested-With → 200 JSON（与修复后的 app.py 一致）
      - 不带                 → 302 到 /report.html#report-mileage-proof
                               （与修复前的 redirect 行为一致）
    这样「有没有走 AJAX 分支」在行为上可观测。
    """

    def do_POST(self):  # noqa: N802 - http.server 约定
        if not re.fullmatch(r"/service-report-attachments/\d+/delete", self.path):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.headers.get("X-Requested-With") == "XMLHttpRequest":
            body = json.dumps({"ok": True, "deleted": 1}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(302)
            self.send_header("Location", "/report.html#report-mileage-proof")
            self.end_headers()


class MileageProofDeleteRenderTest(unittest.TestCase):
    """真渲染：删一行后表格内部滚动位置必须保住。"""

    @classmethod
    def setUpClass(cls):
        chrome = _find_chrome()
        if chrome is None:
            raise unittest.SkipTest("找不到 Chrome，跳过真渲染验证")
        cls.chrome = chrome
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.serve_dir = Path(cls.temp_dir.name) / "site"
        cls.serve_dir.mkdir()
        (cls.serve_dir / "report.html").write_text(_REPORT_HTML, encoding="utf-8")
        handler = lambda *args, **kwargs: _MileageEndpointHandler(
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
<iframe id="f" src="/report.html" style="width:1100px;height:700px;border:0"></iframe>
<script>
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  (async () => {
    const out = {};
    const f = document.getElementById('f');
    for (let i = 0; i < 40; i++) { await sleep(100); try { if (f.contentDocument.getElementById('mileageDeleteTable')) break; } catch(e) {} }
    const d = f.contentDocument, w = f.contentWindow;
    const scroller = d.getElementById('pageScroll');
    out.rowsBefore = d.querySelectorAll('#mileageDeleteTable tbody tr').length;

    // 只统计「删除动作之后」的自发滚动。人为设置 scrollTop 也会触发 scroll 事件，
    // 所以必须先置位再开启计数。
    scroller.scrollTop = 180;
    out.scrollSet = Math.round(scroller.scrollTop);
    out.pageYBefore = Math.round(w.scrollY);

    let scrollEvents = 0;
    let reloadStarted = false;
    w.addEventListener('scroll', () => { scrollEvents += 1; }, {capture:true, passive:true});
    w.addEventListener('beforeunload', () => { reloadStarted = true; });

    // 记录 iframe 是否发生过导航（整页重载的特征）。302 会被 fetch 静默跟随后
    // 报 response.redirected，这里再加一道 iframe 级保险。
    out.navigations = 0;
    w.addEventListener('unload', () => { out.navigations += 1; });

    const btn = d.querySelector('[data-mileage-proof-delete]');
    out.btnFound = !!btn;
    out.hasInlineOnclick = !!(btn && btn.getAttribute('onclick'));
    if (btn) btn.dispatchEvent(new w.MouseEvent('click', {bubbles:true, cancelable:true}));
    await sleep(500);

    const tail = d.querySelector('.scroll-tail');
    out.scrollMax = tail ? Math.round(scroller.scrollHeight - scroller.clientHeight) : -1;
    out.scrollHeightBefore = out.scrollMax === -1 ? -1 : out.scrollMax + Math.round(scroller.clientHeight);
    out.scrollHeightAfter = Math.round(scroller.scrollHeight);
    out.clientHeight = Math.round(scroller.clientHeight);
    out.rowsAfter = d.querySelectorAll('#mileageDeleteTable tbody tr').length;
    out.scrollAfter = Math.round(scroller.scrollTop);
    out.scrollEvents = scrollEvents;
    out.reloadStarted = reloadStarted;
    out.pageYAfter = Math.round(w.scrollY);
    out.statusText = d.getElementById('mileageEvidenceStatus').textContent;
    document.title = 'PROBE:' + JSON.stringify(out);
  })();
</script>
</body></html>"""

    def test_scroll_is_preserved_when_deleting_mileage_proof(self):
        dom = self._run_chrome(self._probe())
        marker = re.search(r"PROBE:(\{.*?\})", dom)
        self.assertIsNotNone(marker, f"探针没有输出结果：{dom[:600]}")
        result = json.loads(marker.group(1))
        self.assertTrue(result["btnFound"], "没找到里程佐证删除按钮")
        self.assertFalse(result["hasInlineOnclick"], "按钮上还有内联 onclick")
        self.assertEqual(result["rowsBefore"], 3, "前置：应有 3 行附件")
        self.assertEqual(result["scrollSet"], 180, "前置：容器已滚到 180")
        self.assertEqual(result["clientHeight"], 240, "前置：容器确实可滚动")

        # 核心断言 1：请求确实走了 AJAX 分支、没有整页导航。
        # 反向对照实测：去掉 X-Requested-With 后 navigations=1、reloadStarted=true、
        # scrollAfter=0、rowsAfter 不变（探针落到「整页重载」分支）——本断言会失败。
        self.assertFalse(result["reloadStarted"], "删除触发了页面卸载 —— 说明还在整页提交")
        self.assertEqual(result["navigations"], 0, "删除导致 iframe 发生了导航（整页重载）")
        self.assertNotIn(
            "整页重载", result["statusText"],
            f"前端识别到后端返回了重定向页面：{result['statusText']}",
        )
        # 核心断言 2：滚动位置分毫不动。harness 已关掉 scroll anchoring、容器高度由
        # tail 占位块主导（删行不改 scrollHeight），所以这里可以要求精确相等。
        # 修复前是整页重载，位置一定归 0。
        self.assertEqual(
            result["scrollAfter"], 180,
            f"滚动位置从 180 变成 {result['scrollAfter']} —— 说明页面发生了整页重载",
        )
        # 核心断言 4：整页 pageY 也不能跳
        self.assertEqual(result["pageYAfter"], result["pageYBefore"])
        # 核心断言 5：那一行真的没了，其余行还在
        self.assertEqual(result["rowsAfter"], result["rowsBefore"] - 1)
        self.assertIn("已删除", result["statusText"])


_REPORT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>日报编辑</title>
<style>
  body { margin: 0; font-family: sans-serif; }
  .page-spacer { height: 700px; background: linear-gradient(#eef,#fee); }
  .grid-scroll { height: 240px; overflow-y: auto; border: 1px solid #ccc; }
  /* 关掉浏览器的 scroll anchoring：删掉一行时 Chrome 会自动把 scrollTop 往上
     「锚定」修正（本例 180 → 150），那是浏览器行为而非应用行为，会掩盖本测试
     真正要测的「是否整页重载」。 */
  .grid-scroll, .grid-scroll * { overflow-anchor: none; }
  /* 让容器内容真正超过 240px，否则 scrollTop=180 会被浏览器夹回 0，
     前置断言就失去意义（这是本测试最初的第二个误报点）。 */
  .scroll-tail { height: 600px; background: #fafafa; }
  table { width: 100%; }
</style>
</head>
<body>
<div class="page-spacer">页面上方的表单内容</div>
<h3>已保存附件</h3>
<div class="grid-scroll" id="pageScroll">
  <table class="list-table" id="mileageDeleteTable">
    <thead><tr><th>文件名</th><th>上传人</th><th>上传时间</th><th>操作</th></tr></thead>
    <tbody>
      <tr><td>佐证1.png</td><td>管理员</td><td>2026-09-01</td>
        <td><button class="danger small" type="submit" form="serviceReportForm"
              data-mileage-proof-delete="/service-report-attachments/1/delete">删除</button></td></tr>
      <tr><td>佐证2.png</td><td>管理员</td><td>2026-09-01</td>
        <td><button class="danger small" type="submit" form="serviceReportForm"
              data-mileage-proof-delete="/service-report-attachments/2/delete">删除</button></td></tr>
      <tr><td>佐证3.png</td><td>管理员</td><td>2026-09-01</td>
        <td><button class="danger small" type="submit" form="serviceReportForm"
              data-mileage-proof-delete="/service-report-attachments/3/delete">删除</button></td></tr>
    </tbody>
  </table>
  <div class="scroll-tail">其余已保存附件…</div>
</div>
<span id="mileageEvidenceStatus" class="muted-line"></span>
<div style="height:900px;background:#efe">页面下方内容</div>
<form id="serviceReportForm" method="post"></form>
<script>
// 复刻 service-report.js 里里程佐证就地删除的处理器。
// 关键：真去打一个「和线上同构」的端点 —— AJAX 分支返回 200 JSON（就地删行），
// 非 AJAX 分支返回 302 到另一个页面（等价于整页重载）。这样反向对照才有意义：
// 只要去掉 preventDefault / 去掉 X-Requested-With，请求就会落到 302 分支，
// 探针立刻能观察到「页面被换掉」。
window.uiConfirm = (msg) => true;
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-mileage-proof-delete]");
  if (!button || button.disabled) return;
  const url = button.dataset.mileageProofDelete;
  if (!url) return;
  event.preventDefault();
  if (!window.uiConfirm("确定删除这个里程佐证附件吗？")) return;
  const row = button.closest("tr");
  const table = button.closest("table");
  const tbody = table?.querySelector("tbody");
  const status = document.getElementById("mileageEvidenceStatus");
  button.disabled = true;
  fetch(url, {
    method: "POST",
    body: new FormData(),
    headers: { "X-Requested-With": "XMLHttpRequest" },
    credentials: "same-origin",
    redirect: "follow",
  })
    .then((response) => {
      if (response.redirected) throw new Error("整页重载");
      if (!response.ok) throw new Error("删除失败 (" + response.status + ")");
      return response.json().catch(() => ({ ok: true }));
    })
    .then(() => {
      row?.remove();
      if (tbody && tbody.querySelectorAll("tr").length === 0) {
        const heading = table?.previousElementSibling;
        if (heading?.tagName === "H3") heading.remove();
        table?.remove();
      }
      if (status) status.textContent = "已删除 1 个里程佐证附件。";
    })
    .catch((error) => {
      button.disabled = false;
      if (status) status.textContent = error.message + "，正在重试…";
      if (button.form) button.form.submit();
    });
});
</script>
</body></html>"""


if __name__ == "__main__":
    unittest.main()
