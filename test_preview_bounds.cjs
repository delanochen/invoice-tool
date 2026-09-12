const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});try{
 for(const stage of ['image-preview-wrap','ledger-photo-stage'])for(const size of [[1600,2400],[2400,1600]]) {
  const page=await browser.newPage({viewport:{width:900,height:650}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${size[0]}" height="${size[1]}"><rect width="100%" height="100%" fill="teal"/></svg>`;
  const url='data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64');
  await page.setContent(`<div style="height:1400px"></div><a href="${url}" data-image-preview>Receipt</a><div style="height:600px"></div><dialog id="imageAttachmentPreviewDialog"><h2 id="imageAttachmentPreviewTitle"></h2><button data-image-preview-in>+</button><button data-image-preview-out>-</button><button data-image-preview-fit>Fit</button><button data-image-preview-original>Original</button><button data-image-preview-close>Close</button><div class="${stage}" style="width:500px;height:350px;display:grid;place-items:center;overflow:auto"><img id="imageAttachmentPreviewImage"></div></dialog>`);
  await page.addStyleTag({content:fs.readFileSync('static/attachment-preview.css','utf8')});await page.addScriptTag({path:'static/attachment-preview.js'});
  await page.locator('a').scrollIntoViewIfNeeded();const before=await page.evaluate(()=>scrollY);assert.ok(before>500);
  await page.locator('a').click();await page.waitForFunction(()=>document.querySelector('img').naturalWidth>0);
  for(const action of ['[data-image-preview-in]','[data-image-preview-original]']) {
   await page.locator(action).click();
   const bounds=await page.locator('img').evaluate(img=>{const v=img.parentElement;v.scrollTo(0,0);const a=img.getBoundingClientRect(),b=v.getBoundingClientRect();const start={x:a.left-b.left,y:a.top-b.top};v.scrollTo(v.scrollWidth,v.scrollHeight);const end=img.getBoundingClientRect();return {start,right:end.right-b.right,bottom:end.bottom-b.bottom,width:a.width,scroll:v.scrollWidth};});
   assert.ok(bounds.start.x>=-1 && bounds.start.y>=-1,JSON.stringify(bounds));assert.ok(bounds.right<=1&&bounds.bottom<=1,JSON.stringify(bounds));
  }
  await page.locator('[data-image-preview-fit]').click();assert.ok(await page.locator('img').evaluate(img=>img.width<=img.parentElement.clientWidth&&img.height<=img.parentElement.clientHeight));
  await page.locator('[data-image-preview-close]').click();assert.equal(await page.evaluate(()=>scrollY),before);
  // Re-render a grid link while the pointer is held: the browser no longer
  // dispatches click to that link, but a short release should still preview it.
  const box=await page.locator('a').boundingBox();
  await page.mouse.move(box.x+5,box.y+5);await page.mouse.down();
  await page.locator('a').evaluate(link=>link.replaceWith(link.cloneNode(true)));
  await page.mouse.up();await page.waitForSelector('dialog[open]');
  await page.locator('[data-image-preview-close]').click();
  await page.locator('a').focus();await page.keyboard.press('Enter');await page.waitForSelector('dialog[open]');
  assert.deepEqual(errors,[]);await page.close();
 }
 console.log('PASS: portrait/landscape, both viewers, all four corners reachable, fit and page scroll restored');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
