const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');

test('report photo buttons open matching native picker and retain selected files at all widths', async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.ERP_BUTTON_BROWSER ? {channel:process.env.ERP_BUTTON_BROWSER} : {})});
  try {
    let page;
    const template=fs.readFileSync('templates/service_report_form.html','utf8');
    const controls=template.match(/<span class="photo-local-control">.*?<\/span>/g);
    assert.equal(controls.length,4);
    const categories=['arrival','departure','self_check','site'];
    const css=['styles.css','erp-ui.css','erp-buttons.css'].map(n=>fs.readFileSync('static/'+n,'utf8')).join('\n');
    const errors=[];
    for (const viewport of [{width:1440,height:900},{width:390,height:844},{width:844,height:390}]) {
      page=await browser.newPage({viewport}); page.on('pageerror',e=>errors.push(e.message));
      await page.setContent('<html><head><style>'+css+'</style></head><body><div class="erp-app erp-compact"><div class="erp-detail-scroll"><form id="serviceReportForm">'+categories.map((c,i)=>`<section><div class="photo-source-actions">${controls[i]}</div><div data-local-list="${c}"></div></section>`).join('')+'</form></div></div></body></html>');
      await page.addScriptTag({path:'static/file-picker.js'});
      await page.addScriptTag({path:'static/service-report.js'});
      for (const category of categories) {
        const button=page.locator(`[data-local-photo-picker="${category}"]`);
        const [chooser]=await Promise.all([page.waitForEvent('filechooser',{timeout:15000}),button.click({timeout:3000})]);
        assert.equal(await chooser.element().getAttribute('data-local-photo'),category);
        assert.equal(chooser.isMultiple(),true);
        await chooser.setFiles({name:category+'.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA6sAAAAASUVORK5CYII=','base64')});
        assert.equal(await page.locator(`[data-local-list="${category}"] .selected-photo-card`).count(),1);
        assert.equal(await page.locator(`[data-local-photo="${category}"]`).evaluate(el=>new FormData(el.form).getAll(el.name)[0].name),category+'.png');
      }
      const first=page.locator('[data-local-photo-picker="arrival"]');
      await first.focus();
      const [keyboardChooser] = await Promise.all([page.waitForEvent('filechooser',{timeout:15000}),first.press('Enter')]);
      await keyboardChooser.setFiles([]);
      await page.locator('[data-local-photo="arrival"]').evaluate(el=>{el.disabled=true});
      await page.waitForFunction(()=>document.querySelector('[data-local-photo-picker="arrival"]').disabled);
      assert.equal(await first.isDisabled(),true); await page.close();
    }
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});
