/* ==========================================================================
 * ERP 工单报表行为脚本（第一阶段：项目利润报表 / 工单通知书式视图）
 * 纯前端：只读取页面已有的数据（`#erp-report-lines` JSON + 已渲染的表格），
 * 不改变任何后端接口与利润计算逻辑。
 * 组件行为：TabWorkspace / SidebarTree / FilterBar / DataGrid 行选中 /
 *           WorkOrderDetailPanel / TopToolbar 动作 / 手机端导航。
 * ========================================================================== */
(() => {
  'use strict';

  const app = document.querySelector('.erp-app');
  if (!app) return;

  const money = value => {
    const amount = Number(value || 0);
    return `$${amount.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  };
  const percent = (profit, revenue) => `${(revenue ? (profit / revenue) * 100 : 0).toFixed(2)}%`;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));

  let lines = [];
  const source = document.getElementById('erp-report-lines');
  try { lines = JSON.parse(source?.textContent || '[]'); } catch (error) { lines = []; }

  /* ------------------------------------------------------ TabWorkspace */
  const panels = Array.from(app.querySelectorAll('[data-erp-panel]'));
  const switches = Array.from(app.querySelectorAll('[data-erp-tab]'));
  function activate(panelName) {
    panels.forEach(panel => panel.classList.toggle('active', panel.dataset.erpPanel === panelName));
    switches.forEach(button => button.classList.toggle('active', button.dataset.erpTab === panelName));
    app.dataset.activePanel = panelName;
  }
  switches.forEach(button => button.addEventListener('click', () => activate(button.dataset.erpTab)));
  if (!switches.some(button => button.classList.contains('active')) && switches.length) {
    switches[0].classList.add('active');
    panels[0]?.classList.add('active');
  }

  /* --------------------------------------------------- WorkOrderDetail */
  const detail = document.getElementById('erpDetailPanel');
  const backdrop = document.getElementById('erpDetailBackdrop');
  const detailTitle = document.getElementById('erpDetailTitle');
  const detailBody = document.getElementById('erpDetailBody');

  const CATEGORY_LABELS = {labor: '工时', allowance: '交通/补贴', expense: '费用'};
  const STATUS_LABELS = {
    client_confirmed: ['completed', '客户已确认'],
    settlement_in_progress: ['submitted', '结算中'],
    estimated: ['draft', '预计'],
  };

  function orderHead(orderId) {
    const first = lines.find(line => String(line.service_order_id) === String(orderId));
    return first || null;
  }

  function renderDetail(orderId) {
    const head = orderHead(orderId);
    if (!head) return false;
    const rows = lines.filter(line => String(line.service_order_id) === String(orderId));
    const revenue = rows.reduce((sum, line) => sum + Number(line.revenue || 0), 0);
    const cost = rows.reduce((sum, line) => sum + Number(line.cost || 0), 0);
    const profit = rows.reduce((sum, line) => sum + Number(line.profit || 0), 0);
    const incomplete = rows.filter(line => line.incomplete).length;

    const byCategory = new Map();
    rows.forEach(line => {
      const bucket = byCategory.get(line.category) || {revenue: 0, cost: 0, profit: 0, count: 0};
      bucket.revenue += Number(line.revenue || 0);
      bucket.cost += Number(line.cost || 0);
      bucket.profit += Number(line.profit || 0);
      bucket.count += 1;
      byCategory.set(line.category, bucket);
    });

    const sorted = [...rows].sort((a, b) => String(a.work_date).localeCompare(String(b.work_date))
      || String(a.category).localeCompare(String(b.category)));

    const missing = incomplete ? ` <span class="status returned">缺 ${incomplete} 条费率</span>` : '';
    const categoryRows = [...byCategory.entries()].map(([category, bucket]) => `
      <tr>
        <td>${escape(CATEGORY_LABELS[category] || category)}</td>
        <td class="num">${money(bucket.revenue)}</td>
        <td class="num">${money(bucket.cost)}</td>
        <td class="num">${money(bucket.profit)}</td>
        <td class="num">${bucket.count}</td>
      </tr>`).join('');

    const incomeRows = sorted.filter(line => Number(line.revenue || 0) !== 0).map(line => `
      <tr>
        <td>${escape(line.work_date)}</td>
        <td>${escape(CATEGORY_LABELS[line.category] || line.category)}</td>
        <td>${escape(line.item_label)}</td>
        <td>${escape(line.employee_name)}</td>
        <td class="num">${escape(line.quantity)} ${escape(line.unit)}</td>
        <td class="num">${money(line.revenue)}</td>
      </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:#64748b">没有收入明细</td></tr>';

    const detailRows = sorted.map(line => `
      <tr>
        <td>${escape(line.work_date)}</td>
        <td>${escape(CATEGORY_LABELS[line.category] || line.category)}</td>
        <td>${escape(line.item_label)}</td>
        <td>${escape(line.employee_name)}</td>
        <td class="num">${escape(line.quantity)} ${escape(line.unit)}</td>
        <td class="num">${money(line.revenue)}</td>
        <td class="num">${money(line.cost)}</td>
        <td class="num">${money(line.profit)}</td>
        <td>${line.allocation_method === 'by_work_hours' ? '按工时分摊' : '直接归属'}</td>
      </tr>`).join('');

    if (detailTitle) {
      detailTitle.textContent = `${head.order_number} · ${head.client_name}`;
    }
    if (detailBody) {
      detailBody.innerHTML = `
        <div class="erp-detail-meta" style="margin-bottom:6px">
          工单号 <b>${escape(head.order_number)}</b> · 客户 <b>${escape(head.client_name)}</b>${missing}
        </div>
        <div class="erp-detail-metrics">
          <div><span>收入</span><strong>${money(revenue)}</strong></div>
          <div><span>成本</span><strong>${money(cost)}</strong></div>
          <div><span>毛利润</span><strong class="${profit < 0 ? 'neg' : ''}" style="color:${profit < 0 ? '#b42318' : 'inherit'}">${money(profit)}</strong></div>
          <div><span>利润率</span><strong>${percent(profit, revenue)}</strong></div>
        </div>

        <div class="erp-detail-section-title">利润构成</div>
        <table class="erp-detail-list">
          <thead><tr><th>类别</th><th class="num">收入</th><th class="num">成本</th><th class="num">利润</th><th class="num">明细数</th></tr></thead>
          <tbody>${categoryRows}</tbody>
        </table>

        <div class="erp-detail-section-title">收入明细（客户计费）</div>
        <table class="erp-detail-list">
          <thead><tr><th>日期</th><th>类别</th><th>项目</th><th>员工</th><th class="num">数量</th><th class="num">收入</th></tr></thead>
          <tbody>${incomeRows}</tbody>
        </table>

        <div class="erp-detail-section-title">收支明细（${rows.length} 条）</div>
        <table class="erp-detail-list">
          <thead><tr><th>日期</th><th>类别</th><th>项目</th><th>员工</th><th class="num">数量</th><th class="num">收入</th><th class="num">成本</th><th class="num">利润</th><th>分摊</th></tr></thead>
          <tbody>${detailRows}</tbody>
        </table>`;
    }
    detail?.classList.add('open');
    backdrop?.classList.add('open');
    return true;
  }

  function closeDetail() {
    detail?.classList.remove('open');
    backdrop?.classList.remove('open');
  }

  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-order-id]');
    if (trigger) {
      event.preventDefault();
      renderDetail(trigger.dataset.orderId);
      return;
    }
    if (event.target === backdrop) closeDetail();
  });
  // 详情面板挂在 .erp-app 之外（全屏抽屉），因此从 document 取关闭按钮。
  document.querySelectorAll('[data-erp-detail-close]').forEach(button => button.addEventListener('click', closeDetail));
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeDetail(); });

  /* --------------------------------------------------------- Toolbar */
  const form = app.querySelector('[data-erp-filter]');
  app.querySelectorAll('[data-erp-action]').forEach(button => {
    button.addEventListener('click', () => {
      switch (button.dataset.erpAction) {
        case 'query': form?.requestSubmit(); break;
        case 'reset':
          form?.querySelectorAll('input[type=date], input[type=search], input[type=text]').forEach(input => { input.value = ''; });
          form?.querySelectorAll('select').forEach(select => { select.selectedIndex = 0; });
          form?.requestSubmit();
          break;
        case 'refresh': window.location.reload(); break;
        case 'print': window.print(); break;
        case 'export': {
          if (typeof window.downloadVisibleReport === 'function') window.downloadVisibleReport(button);
          else window.alert('当前页面没有可导出的报表表格。');
          break;
        }
        case 'toggle-filter':
          form?.classList.toggle('collapsed');
          button.setAttribute('aria-expanded', String(!form?.classList.contains('collapsed')));
          break;
        default: break;
      }
    });
  });

  /* -------------------------------------------- StatusBar 记录数同步 */
  // system-grid.js 在本脚本之后才构建 Tabulator，因此要等 .grid-count 出现。
  const countTarget = app.querySelector('[data-erp-count]');
  const findCounter = () => app.querySelector('[data-erp-panel="orders"] .system-grid .grid-count');
  if (countTarget) {
    let bound = null;
    const bind = counter => {
      if (!counter || bound === counter) return;
      bound = counter;
      const mirror = () => {
        // grid-count 形如「3 条记录」；状态栏只取数字，避免「记录 3 条记录」。
        const matched = counter.textContent.match(/\d+/);
        countTarget.textContent = matched ? matched[0] : counter.textContent.trim();
      };
      new MutationObserver(mirror).observe(counter, {childList: true, characterData: true, subtree: true});
      mirror();
    };
    const pending = findCounter();
    if (pending) bind(pending);
    else {
      const watcher = new MutationObserver(() => {
        const counter = findCounter();
        if (counter) { watcher.disconnect(); bind(counter); }
      });
      watcher.observe(app, {childList: true, subtree: true});
    }
  }
})();
