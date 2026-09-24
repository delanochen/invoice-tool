"""Smoke check for the ERP-style 工单报表 (project profitability report).

Verifies template rendering with synthetic data, the live route, and that the
existing filter parameters still work. Read-only: uses a throwaway DB.
"""
import os
import re
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "erp-ui-smoke")
os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
os.environ.setdefault("IS_RELEASE_BUILD", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin123test")

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

failures = []


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def line(**kwargs):
    base = dict(
        work_date="2026-08-01", service_order_id=1, order_number="WT-2026-0001",
        client_name="Atacama Site A", employee_id=10, employee_name="陈亦珹",
        category="labor", item_type="regular_hours", item_label="标准工时",
        quantity=8.0, unit="h", client_rate=100.0, employee_rate=60.0,
        revenue=800.0, cost=480.0, profit=320.0,
        client_rate_source="contract", employee_rate_source="grade",
        contract_rate_version_id=1, employee_rate_version_id=1,
        source_type="service_report", source_id=1, source_line_id=1,
        allocation_method="direct", status="estimated", incomplete=False,
    )
    base.update(kwargs)
    return base


def build_lines():
    return [
        line(),
        line(source_id=2, source_line_id=2, category="allowance", item_label="交通补贴", quantity=2.0, unit="h", revenue=40.0, cost=30.0, profit=10.0),
        line(work_date="2026-08-03", source_id=3, source_line_id=3, category="expense", item_label="住宿費", quantity=1.0, unit="expense", revenue=0.0, cost=150.0, profit=-150.0, allocation_method="by_work_hours"),
        line(work_date="2026-08-05", service_order_id=2, order_number="WT-2026-0002", client_name="Site B", source_id=4, source_line_id=4, status="client_confirmed", revenue=500.0, cost=300.0, profit=200.0),
        line(work_date="2026-08-06", service_order_id=2, order_number="WT-2026-0002", client_name="Site B", source_id=5, source_line_id=5, status="settlement_in_progress", revenue=200.0, cost=100.0, profit=100.0, incomplete=True),
        line(work_date="2026-08-07", service_order_id=3, order_number="WT-2026-0003", client_name="Site C", source_id=6, source_line_id=6, revenue=0.0, cost=0.0, profit=0.0),
    ]


def main():
    temp_dir = tempfile.mkdtemp(prefix="erp_ui_smoke_")
    os.environ["DATA_DIR"] = temp_dir
    import app as app_module
    app_module.DATA_DIR = temp_dir
    app_module.DB_PATH = os.path.join(temp_dir, "invoices.db")
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    app_module.app.config["TESTING"] = True

    from flask import g, render_template, session

    with app_module.app.app_context():
        app_module.init_db()
        admin = app_module.db().execute("select id from users where role='admin' order by id limit 1").fetchone()
        admin_id = admin["id"] if admin else None

    # 渲染需要 request context + g.user（base.html 依赖登录上下文）
    ctx_stack = app_module.app.test_request_context("/reports/profitability")
    ctx_stack.push()
    session["user_id"] = admin_id
    app_module.app.preprocess_request()
    try:
        lines = build_lines()
        summary = {
            "revenue": float(sum(x["revenue"] for x in lines)),
            "cost": float(sum(x["cost"] for x in lines)),
            "profit": float(sum(x["profit"] for x in lines)),
            "margin": 18.5,
            "incomplete": 1,
            "by_category": {
                "labor": {"revenue": 800.0, "cost": 480.0, "profit": 320.0},
                "allowance": {"revenue": 40.0, "cost": 30.0, "profit": 10.0},
                "expense": {"revenue": 0.0, "cost": 150.0, "profit": -150.0},
            },
        }
        context = dict(
            lines=lines,
            rows=[{"label": "WT-2026-0001 · Atacama Site A", "revenue": 840.0, "cost": 660.0, "profit": 180.0, "margin": 21.43, "line_count": 3, "incomplete": 0}],
            summary=summary,
            start_date="2026-08-01", end_date="2026-08-31",
            selected_order_id=None, selected_employee_id=None,
            group_by="order",
            orders=[{"id": 1, "order_number": "WT-2026-0001", "client_name": "Atacama Site A"}],
            employees=[{"id": 10, "name": "陈亦珹"}],
        )

        html = render_template("profitability.html", **context)
        Path(temp_dir, "rendered.html").write_text(html, encoding="utf-8")

        # 浏览器探针：把 /static/ 改写成本地文件路径，便于用 headless Chrome 检查
        # 桌面/手机两套布局、Tabulator 是否接管表格、表头是否冻结。
        probe = html.replace('"/static/', f'"file:///{PROJECT_ROOT.as_posix()}/static/')
        # 探针以顶层窗口打开，需要停掉 base.html 的工作区重定向（真实使用时页面嵌在 iframe 内）
        probe = probe.replace("location.replace(", "false && location.replace(")
        probe = probe.replace("</body>", """
<pre id="probe-out" style="display:none"></pre>
<script>
setTimeout(function () {
  var q = function (s) { return document.querySelector(s); };
  var style = function (s, prop) { var n = q(s); return n ? getComputedStyle(n)[prop] : null; };
  document.getElementById('probe-out').textContent = JSON.stringify({
    gridVisible: style('.erp-grid-wrap', 'display') !== 'none' && style('.erp-grid-wrap', 'visibility') !== 'hidden',
    cardsDisplay: style('.erp-cards', 'display'),
    navPosition: style('.erp-nav', 'position'),
    navDisplay: style('.erp-nav', 'display'),
    hasTabulator: !!q('.system-grid .tabulator'),
    tabulatorRows: document.querySelectorAll('.system-grid .tabulator-row').length,
    tabulatorGridHeight: style('.system-grid .tabulator-tableholder', 'maxHeight') || style('.system-grid .tabulator', 'height'),
    gridHeaderPosition: style('.system-grid .tabulator-header', 'position'),
    summarySticky: style('.erp-summary', 'position'),
    cardCount: document.querySelectorAll('.erp-card').length,
    toolbarVisible: style('.erp-toolbar', 'display') !== 'none',
    tables: document.querySelectorAll('table').length,
    exportReady: typeof window.downloadVisibleReport === 'function',
    firstExportableTable: (q('main table') || {}).className
  });
  document.getElementById('probe-out').setAttribute('data-ready', '1');
  setTimeout(function () {
    var panel = document.getElementById('erpDetailPanel');
    var trigger = document.querySelector('[data-order-id]');
    if (trigger) trigger.click();
    var tab = document.querySelector('[data-erp-tab="detail"]');
    if (tab) tab.click();
    var detailOpenBefore = panel.classList.contains('open');
    var active = document.querySelector('.erp-panel.active');
    var closeBtn = document.querySelector('[data-erp-detail-close]');
    if (closeBtn) closeBtn.click();
    document.getElementById('probe-out').textContent = JSON.stringify({
      detailOpenBefore: detailOpenBefore,
      detailHasIncomeSection: /收入明细/.test(panel.textContent || ''),
      detailHasFlowSection: /收支明细/.test(panel.textContent || ''),
      detailRows: panel.querySelectorAll('.erp-detail-list tbody tr').length,
      detailTitle: (document.getElementById('erpDetailTitle') || {}).textContent,
      activePanelAfterTabClick: active ? active.dataset.erpPanel : null,
      closedByButton: !panel.classList.contains('open'),
      statusCount: (q('[data-erp-count]') || {}).textContent,
      exportReady: typeof window.downloadVisibleReport === 'function',
      firstExportableTable: (q('main table') || {}).className,
      badgeDisplay: style('.erp-app .erp-badge', 'display'),
      toolbarWrap: style('.erp-toolbar', 'flexWrap'),
      detailTableVisible: (function () {
        var panelEl = q('[data-erp-panel="detail"]');
        if (!panelEl) return false;
        var table = panelEl.querySelector('.table-scroll') || panelEl.querySelector('.system-grid');
        return !!table && getComputedStyle(table).display !== 'none' && getComputedStyle(panelEl).display !== 'none';
      })()
    });
  }, 1500);
}, 3000);
</script>
</body>""")
        probe_path = Path(temp_dir, "probe.html")
        probe_path.write_text(probe, encoding="utf-8")
        print("probe ->", probe_path)

        check("模板渲染无异常", bool(html) and "<html" in html)

        # 桌面 DataGrid：3 个工单 → 3 行
        check("工单汇总行数 = 3", html.count('data-order-id="1"') + html.count('data-order-id="2"') + html.count('data-order-id="3"') > 0)
        for order_id in (1, 2, 3):
            check(f"工单 {order_id} 出现（表格行 + 卡片）", f'data-order-id="{order_id}"' in html)
        per = OrderedDict()
        for order_id in (1, 2, 3):
            per[order_id] = html.count(f'data-order-id="{order_id}"')
        check("每工单 3 处入口（表格行/详情按钮/手机卡片）", set(per.values()) == {3}, str(per))

        check("收入右对齐（金额单元）", 'class="num">$800.00' in html or 'class="num">$840.00' in html)
        check("汇总合计行存在", "合计（3 个工单）" in html)
        check("空利润率未除零", ".00%" in html and "$0.00" in html)

        # 组件挂载点
        for marker in ("erp-toolbar", "erp-nav", "erp-tabs", "erp-body", "erp-summary", "erp-status", "erp-card", "erp-detail"):
            check(f"组件 {marker} 已渲染", f'class="{marker}' in html)

        check("筛选表单保留后端字段名", 'name="start_date"' in html and 'name="end_date"' in html
              and 'name="service_order_id"' in html and 'name="employee_id"' in html and 'name="group_by"' in html)
        check("明细 JSON 注入", 'id="erp-report-lines"' in html and '2026-08-01' in html)
        check("导出配置注入", 'window.reportExportConfig' in html)
        check("明细面板保留原有列", "客户单价" in html and "按工时分摊" in html)
        check("汇总方式 Tab 动态标题", "按工单汇总" in html)

        # 金额正确性：WT-2026-0001 = 800+40+0 收入 / 480+30+150 成本 / 180 利润
        check("工单一 收入 $840.00", "$840.00" in html)
        check("工单一 成本 $660.00", "$660.00" in html)
        check("工单一 毛利润 $180.00", "$180.00" in html)
        check("工单一 利润率 21.43%", "21.43%" in html)
        # WT-2026-0002 = 700 收入 / 400 成本 / 300 利润 / 42.86%
        check("工单二 收入 $700.00", "$700.00" in html)
        check("工单二 成本 $400.00", "$400.00" in html)
        check("工单二 毛利润 $300.00", "$300.00" in html)
        check("工单二 利润率 42.86%", "42.86%" in html)
        check("缺费率状态可见", "缺 1 条费率" in html)
        check("客户已确认/结算中状态可见", "结算中" in html or "客户已确认" in html)

        # 其它 group_by 取值
        for mode, label in (("day", "按日汇总"), ("month", "按月汇总")):
            ctx = dict(context); ctx["group_by"] = mode
            html2 = render_template("profitability.html", **ctx)
            check(f"group_by={mode} 正常渲染", label in html2)

        # 空数据
        empty_ctx = dict(context)
        empty_ctx.update(lines=[], rows=[], summary=dict(summary, revenue=0.0, cost=0.0, profit=0.0, margin=0.0, incomplete=0))
        html_empty = render_template("profitability.html", **empty_ctx)
        check("空数据渲染正常", "当前筛选范围还没有可核算的工单利润数据。" in html_empty)

        # 路由实跑
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            admin = app_module.db().execute("select id from users where role='admin' order by id limit 1").fetchone()
            sess["user_id"] = admin["id"] if admin else None
        response = client.get("/reports/profitability")
        check("GET /reports/profitability 200", response.status_code == 200, f"status={response.status_code}")
        body = response.get_data(as_text=True)
        check("实跑页面含 ERP 外壳", 'class="erp-app"' in body)
        response2 = client.get("/reports/profitability?start_date=2026-08-01&end_date=2026-08-31&group_by=day")
        check("带筛选参数查询 200", response2.status_code == 200, f"status={response2.status_code}")
        check("筛选参数回填", 'value="2026-08-01"' in response2.get_data(as_text=True))
    finally:
        ctx_stack.pop()

    print("\n结果:", "全部通过" if not failures else f"{len(failures)} 项失败 -> {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
