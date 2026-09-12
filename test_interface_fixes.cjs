const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8786/test-login');await page.waitForFunction(()=>document.querySelector('iframe')?.contentDocument?.querySelector('h1'));
 const frame=page.frames().find(f=>f.parentFrame());await frame.goto('http://127.0.0.1:8786/reports/expenses');await frame.waitForSelector('.grid-tools select');
 const count=await frame.locator('.grid-count').innerText();await frame.locator('.grid-tools input[type=search]').fill('IMPOSSIBLE-MATCH');
 await frame.waitForFunction(()=>document.querySelector('.grid-count').textContent.startsWith('0 / '));
 await frame.locator('.grid-tools input[type=search]').fill('');assert.equal(await frame.locator('.grid-count').innerText(),count);
 await frame.locator('.grid-tools select').first().selectOption('c1');const group=frame.locator('.grid-group-toggle').first();await group.focus();
 assert.equal(await group.evaluate(el=>el===document.activeElement),true);await group.press('Enter');assert.equal(await group.getAttribute('aria-expanded'),'false');
 await group.press('Space');assert.equal(await group.getAttribute('aria-expanded'),'true');assert.equal(await group.evaluate(el=>getComputedStyle(el).color),'rgb(53, 81, 91)');
 await frame.goto('http://127.0.0.1:8786/service-orders/1/expenses/new');await frame.waitForSelector('.grid-source',{state:'attached'});await frame.locator('[data-expense-submit][value=save]').click();
 await frame.waitForFunction(()=>document.activeElement.matches('.tabulator select'));await page.setViewportSize({width:320,height:900});
 assert.equal(await frame.locator('.tabulator input[type=number]').first().evaluate(el=>getComputedStyle(el).fontSize),'16px');
 assert.equal(await frame.locator('.page-header .actions').evaluate(el=>getComputedStyle(el).flexDirection),'row');
 assert.equal(await frame.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);console.log('PASS: filter count, keyboard groups, colors, first invalid field, mobile layout');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
