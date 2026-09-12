const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8786/test-login');
 await page.route('**/api/field/photos?*',route=>route.fulfill({json:{rows:[{id:1,order_number:'SO-TEST',site_name:'Test site',equipment_number:'M1',position_number:'A1',container_number:'C1',employee_name:'Account',technician_name:'Tech',captured_at:'2026-09-11T21:31:40.091000+00:00',received_at:'2026-09-11T16:32:03-05:00',timezone_name:'America/Chicago',preview:'/test-image.svg',thumbnail:'/test-image.svg',watermark_source:'original',source:'camera',note:''}],truncated:false}}));
 await page.route('**/test-image.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="400"><rect width="200" height="400" fill="teal"/></svg>'}));
 await page.goto('http://127.0.0.1:8786/field/');await page.locator('[data-tab=ledger]').click();await page.waitForSelector('.field-ledger-grid img');
 const text=await page.locator('#ledgerList').innerText();assert.match(text,/16:31:40 GMT[-−]5/);assert.match(text,/16:32:03 GMT[-−]5/);assert.match(text,/实际拍摄账号/);
 const thumb=page.locator('.field-ledger-grid img').first();assert.equal(await thumb.evaluate(el=>getComputedStyle(el).objectFit),'contain');await thumb.click();
 await page.waitForSelector('#ledgerPhotoDialog[open]');await page.locator('[data-image-preview-in]').click();assert.equal(await page.locator('#ledgerPhotoImage').evaluate(el=>el.style.transform),'scale(1.25)');
 await page.locator('[data-image-preview-out]').click();assert.equal(await page.locator('#ledgerPhotoImage').evaluate(el=>el.style.transform),'scale(1)');
 await page.locator('[data-image-preview-original]').click();assert.match(await page.locator('#ledgerPhotoImage').getAttribute('class'),/is-original/);
 await page.locator('[data-image-preview-fit]').click();assert.match(await page.locator('#ledgerPhotoImage').getAttribute('class'),/is-fit/);
 await page.screenshot({path:'../photo-preview-updated.png'});await page.locator('#closeLedgerPhoto').click();
 assert.deepEqual(errors,[]);console.log('PASS: matching time zones, separate columns, full thumbnails, shared preview zoom controls');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
