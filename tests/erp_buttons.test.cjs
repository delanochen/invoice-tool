const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const templates = fs.readdirSync(path.join(root, 'templates')).filter(f => f.endsWith('.html'));
const shared = read('static/erp-buttons.css');
const base = read('templates/base.html');

test('every base page loads the shared geometry after its custom head', () => {
  assert(base.indexOf("filename='erp-buttons.css'") > base.indexOf('{% block head %}{% endblock %}'));
  assert.match(base, /filename='erp-buttons.css', v=app_version/);
});

test('templates cannot introduce important action-button geometry overrides', () => {
  for (const name of templates) {
    const css = [...read('templates/' + name).matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(m => m[1]).join('\n');
    for (const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/(?:\.erp-btn\b|\.button\b|\bbutton\b|\.photo-local-picker\b)/.test(rule[1])) continue;
      assert(!/(?:height|padding(?:-\w+)?|line-height|font(?:-size)?|border-radius)\s*:[^;{}]*!important/i.test(rule[2]),
        `${name}: move action dimensions to erp-buttons.css: ${rule[1].trim()}`);
    }
  }
});

test('browser: action dimensions stay equal across page styles and responsive states', {timeout: 180000}, async () => {
  const {chromium} = require('playwright');
  const browser = await chromium.launch({headless: true, ...(process.env.ERP_BUTTON_BROWSER ? {channel: process.env.ERP_BUTTON_BROWSER} : {})});
  try {
    const page = await browser.newPage();
    const css = ['styles.css', 'erp-ui.css', 'workspace.css'].map(f => read('static/' + f)).join('\n');
    const fixture = `
      <main><div class="erp-app erp-compact" style="display:block;height:auto">
        <header class="erp-toolbar"><div class="service-report-nav">
          <button class="erp-btn" disabled data-check>← 上一个</button>
          <a class="erp-btn" href="#next" data-check>下一个 →</a>
        </div><div class="erp-toolbar-group"><form>
          <button class="erp-btn primary" type="submit" data-check>导出 Word</button>
        </form><button class="erp-btn" type="button" data-check>打印</button></div></header>
      </div><form class="service-report-form panel"><div class="form-grid"><div class="form-field">
        <div class="photo-source-actions">
          <label class="button small photo-local-picker" data-check><span>从本机选择</span><input type="file" aria-label="从本机选择"></label>
          <button class="small" type="button" data-check>从服务器选择</button>
        </div></div></div></form>
      <div class="actions"><a class="button" href="#" data-check>返回</a>
        <form><button type="submit" data-check>保存</button></form>
        <button class="danger" type="button" data-check>删除</button>
        <button class="erp-btn" hidden id="hiddenButton">隐藏</button>
        <button class="erp-btn" style="display:none" id="conditionalButton">条件隐藏</button>
      </div><span class="unified-file-control"><button class="unified-file-button" type="button" data-check>选择文件</button></span>
      <button id="componentIcon" style="height:18px;min-height:18px;padding:0">×</button></main>`;
    for (const width of [1440, 390, 844]) {
      await page.setViewportSize({width, height: 900});
      for (const name of templates) {
        const source = read('templates/' + name);
        const inline = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(m => m[1]).join('\n');
        const linked = [...source.matchAll(/filename=['"]([^'"]+\.css)['"]/g)]
          .map(m => path.join(root, 'static', m[1])).filter(f => fs.existsSync(f))
          .map(f => fs.readFileSync(f, 'utf8')).join('\n');
        await page.setContent(`<style>${css}\n${linked}\n${inline}</style><style>${shared}</style>${fixture}`);
        const values = await page.locator('[data-check]').evaluateAll(nodes => nodes.map(e => {
          const c = getComputedStyle(e);
          return {height:e.getBoundingClientRect().height, fontSize:c.fontSize, lineHeight:c.lineHeight,
            padding:c.padding, radius:c.borderRadius, border:c.borderTopWidth, box:c.boxSizing};
        }));
        assert(values.every(v => JSON.stringify(v) === JSON.stringify(values[0])), `${name} width=${width}: ${JSON.stringify(values)}`);
        assert.equal(values[0].height, width <= 640 ? 32 : 28, name);
        // Equal top and bottom edges in the report toolbar, including a form wrapper.
        const edges = await page.locator('.erp-toolbar [data-check]').evaluateAll(nodes => nodes.map(e => {
          const r=e.getBoundingClientRect();return [r.top,r.bottom];
        }));
        assert(edges.every(v => Math.abs(v[0]-edges[0][0]) < 1 && Math.abs(v[1]-edges[0][1]) < 1), `${name}: toolbar alignment`);
        assert.equal(await page.locator('#hiddenButton').isVisible(), false);
        assert.equal(await page.locator('#conditionalButton').isVisible(), false);
        assert.equal(await page.locator('#componentIcon').evaluate(e => e.getBoundingClientRect().height), 18);
      }
      for (const selector of ['a.erp-btn', '.primary', '.photo-local-picker']) {
        const button = page.locator(selector).first();
        const original = await button.evaluate(e => e.getBoundingClientRect().height);
        await button.hover();
        assert.equal(await button.evaluate(e => e.getBoundingClientRect().height), original);
        await button.focus();
        assert.equal(await button.evaluate(e => e.getBoundingClientRect().height), original);
      }
      const colors = await page.locator('.erp-toolbar .erp-btn').evaluateAll(nodes => nodes.map(e => getComputedStyle(e).backgroundImage));
      assert.notEqual(colors[0], colors[2], 'primary keeps its semantic color');
    }
    console.log(`Verified ${templates.length} templates at desktop, mobile and landscape widths.`);
  } finally {
    await browser.close();
  }
});
