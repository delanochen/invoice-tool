const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
// Generate synthetic fixtures first: python scripts/render_list_action_fixtures.py

test('management toolbars select, edit and delete locally at desktop/mobile/landscape sizes', {timeout:180000}, async () => {
  const {chromium} = require('playwright');
  const browser = await chromium.launch({headless:true, channel:process.env.ERP_BUTTON_BROWSER || 'msedge'});
  try {
    const page = await browser.newPage();
    let deletion = 'blocked';
    let posts = 0;
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    await page.route('http://list-actions.test/**', async route => {
      if (route.request().method() === 'POST') {
        posts++;
        assert.equal(route.request().headers()['x-requested-with'], 'XMLHttpRequest');
        await route.fulfill({status:deletion === 'blocked' ? 409 : 200, contentType:'application/json',
          body:JSON.stringify({ok:deletion !== 'blocked', message:'synthetic dependency restriction'})});
      } else await route.fulfill({body:'<html></html>', contentType:'text/html'});
    });
    const css = ['styles.css','erp-ui.css','system-grid.css','erp-buttons.css','vendor/tabulator/tabulator.min.css']
      .map(name => read('static/' + name)).join('\n');
    for (const [width,height] of [[1440,900],[390,844],[844,390]]) {
      await page.setViewportSize({width,height});
      for (const name of ['manufacturers','owners','clients','work_order_types','projects','buyers','users']) {
        await page.goto('http://list-actions.test/');
        const html = read(`tmp/list-actions/${name}.html`).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g,'');
        await page.setContent('<style>'+css+'</style>'+html);
        for (const script of ['vendor/tabulator/tabulator.min.js','system-grid.js','selectable-table-actions.js','local-list-actions.js','modal-forms.js','erp-report.js']) {
          await page.addScriptTag({content:read('static/'+script)});
        }
        await page.evaluate(()=>window.systemGrids.scan());
        await page.waitForFunction(() => window.systemGrids.instances.size > 0 && [...window.systemGrids.instances.values()].every(grid=>grid.ready));
        const edit = page.locator('header [data-row-action="editTarget"]');
        const remove = page.locator('header [data-local-delete] button');
        assert(await edit.isDisabled()); assert(await remove.isDisabled());
        assert.equal(await page.locator('[data-selected-label]').count(),0);
        if (name === 'buyers') assert.equal(await page.locator('[data-erp-filter]').count(),0);
        assert.equal(await page.locator('[data-selectable-table] .row-actions').count(),0);
        const row = page.locator('.system-grid .tabulator-row').filter({has:page.locator('.tabulator-cell')}).first();
        await row.click();
        await page.waitForFunction(() => !document.querySelector('header [data-row-action="editTarget"]').disabled);
        await page.waitForFunction(()=>document.querySelector('.system-grid .tabulator-row.is-selected'));
        await edit.click();
        assert(await page.locator('dialog[open]').isVisible());
        await page.locator('dialog[open]').evaluate(el=>el.close());
        if (name === 'users') {
          assert.equal(await page.locator('header [data-row-action="statusUrl"]').innerText(),'停用');
          assert.equal(await page.locator('header [data-selected-input]').inputValue(),'0');
        }
        if (name === 'projects') {
          await page.locator('[data-erp-tab=mapping]').click();
          assert(await edit.isDisabled()); assert(await remove.isDisabled());
          await page.locator('[data-erp-tab=list]').click();
        }
        deletion = 'blocked';
        const before = posts;
        await remove.click();
        await page.waitForFunction(()=>!document.querySelector('[data-local-list]').dataset.deletePending);
        assert.equal(posts,before+1);
        assert.equal(await page.locator('[data-selectable-table] tr[data-row-id]').count(),60);
        deletion = 'allowed';
        const documentToken = await page.evaluate(()=>{window.documentToken=Math.random();return window.documentToken;});
        await remove.click();
        await page.waitForFunction(()=>document.querySelectorAll('[data-selectable-table] tr[data-row-id]').length===59);
        assert.equal(await page.evaluate(()=>window.documentToken),documentToken);
        assert(await edit.isDisabled()); assert(await remove.isDisabled());
        assert.deepEqual(await page.locator('[data-list-count]').allTextContents(),['59','59','59']);
        assert.equal(await page.locator('.system-grid .tabulator-row.is-selected').count(),0);
        // Choose a later row with the keyboard; native POST status controls retain their payload.
        await page.locator('[data-selectable-table] tr[data-row-id="2"]').dispatchEvent('keydown',{key:'Enter'});
        assert(!(await edit.isDisabled()));
        if (name === 'users') {
          assert.equal(await page.locator('header [data-row-action="statusUrl"]').innerText(),'批准启用');
          assert.equal(await page.locator('header [data-selected-input]').inputValue(),'1');
        }
        if (name === 'manufacturers') {
          const scroll = await page.evaluate(()=>{
            const grid = window.systemGrids.instances.get(document.querySelector('[data-selectable-table]'));
            const holder = grid.host.querySelector('.tabulator-tableholder');
            holder.scrollTop = 250;
            return holder.scrollTop;
          });
          await remove.click();
          await page.waitForFunction(()=>document.querySelectorAll('[data-selectable-table] tr[data-row-id]').length===58);
          assert.equal(await page.evaluate(()=>document.querySelector('.tabulator-tableholder').scrollTop),scroll);
        }
        console.log('PASS',name,width,height);
      }
    }
    // Last-record deletion must keep the empty grid and its actions consistent.
    for (const fixture of ['manufacturer_single', 'manufacturer_readonly']) {
      await page.goto('http://list-actions.test/');
      await page.setContent('<style>'+css+'</style>'+read(`tmp/list-actions/${fixture}.html`).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,''));
      for (const script of ['vendor/tabulator/tabulator.min.js','system-grid.js','selectable-table-actions.js','local-list-actions.js']) {
        await page.addScriptTag({content:read('static/'+script)});
      }
      await page.evaluate(()=>window.systemGrids.scan());
      await page.waitForFunction(()=>[...window.systemGrids.instances.values()].every(grid=>grid.ready));
      if (fixture.endsWith('readonly')) {
        assert.equal(await page.locator('header [data-row-action],header [data-local-delete]').count(),0);
      } else {
        await page.locator('[data-selectable-table] tr[data-row-id]').dispatchEvent('click');
        await page.locator('header [data-local-delete] button').click();
        await page.waitForFunction(()=>document.querySelector('[data-list-count]').textContent==='0');
        assert.equal(await page.locator('[data-selectable-table] td.empty').count(),1);
        assert(await page.locator('header [data-local-delete] button').isDisabled());
        assert.equal(await page.evaluate(()=>[...window.systemGrids.instances.values()][0].grid.getDataCount()),0);
      }
    }
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});
