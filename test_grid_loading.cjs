const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});try{
 const page=await browser.newPage();await page.goto('http://127.0.0.1:8786/test-login');await page.waitForFunction(()=>document.querySelector('iframe')?.contentDocument?.querySelector('h1'));const frame=page.frames().find(f=>f.parentFrame());
 let release;const gate=new Promise(r=>release=r);await page.route('**/vendor/tabulator/tabulator.min.js',async route=>{await gate;await route.continue();});
 await frame.goto('http://127.0.0.1:8786/reports/service-reports',{waitUntil:'commit'});await frame.locator('table').waitFor({state:'attached'});
 assert.equal(await frame.locator('table').evaluate(el=>getComputedStyle(el).visibility),'hidden');release();await frame.waitForLoadState('load');
 await frame.locator('.system-grid:not(.grid-building)').waitFor();assert.equal(await frame.locator('table').evaluate(el=>getComputedStyle(el).display),'none');
 const headers=await frame.locator('.tabulator-col-title').allTextContents();assert.equal(headers.includes('机柜编号'),false);assert.equal(headers.length,10);
 assert.equal(await frame.evaluate(()=>window.systemGrids.payload(document.querySelector('table')).headers.includes('机柜编号')),false);
 await page.unroute('**/vendor/tabulator/tabulator.min.js');await page.route('**/vendor/tabulator/tabulator.min.js',route=>route.abort());await frame.goto(frame.url());
 assert.equal(await frame.locator('table').evaluate(el=>getComputedStyle(el).visibility),'visible');assert.equal(await frame.locator('table').evaluate(el=>getComputedStyle(el).display),'table');
 console.log('PASS: native table hidden during delayed loading, grid ready, removed column and export, fallback on library failure');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
