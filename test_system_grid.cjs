const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1280,height:850}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent(`<form id="form"><table id="edit"><thead><tr><th>项目</th><th>金额（USD）</th><th>附件</th><th>操作</th></tr></thead><tbody><tr><td><input name="project" value="A" required></td><td><input name="amount" type="number" value="12"></td><td><input name="files" type="file"></td><td><button type="button" onclick="this.closest('tr').remove()">删除</button></td></tr></tbody></table></form><table id="report"><thead><tr><th>工单</th><th>员工</th><th>金额</th></tr></thead><tbody><tr data-currency="USD"><td>SO1</td><td>A</td><td>$100.25</td></tr><tr data-currency="USD"><td>SO1</td><td>B</td><td>$200.50</td></tr><tr data-currency="EUR"><td>SO2</td><td>A</td><td>€30.00</td></tr></tbody></table>`);
 await page.addStyleTag({path:path.join(__dirname,'static/vendor/tabulator/tabulator.min.css')});
 await page.addStyleTag({path:path.join(__dirname,'static/system-grid.css')});
 await page.addScriptTag({path:path.join(__dirname,'static/vendor/tabulator/tabulator.min.js')});
 await page.addScriptTag({path:path.join(__dirname,'static/system-grid.js')});
 await page.evaluate(()=>document.dispatchEvent(new Event('DOMContentLoaded')));
 await page.waitForSelector('#report.grid-source',{state:'attached'});
 assert.equal(await page.locator('.tabulator').count(),2);
 const edit=page.locator('.system-grid').nth(0), report=page.locator('.system-grid').nth(1);
 await edit.locator('.tabulator-cell input').nth(0).fill('Changed'); await edit.locator('input[type=number]').fill('55.75');
 await edit.locator('input[type=file]').setInputFiles({name:'proof.jpg',mimeType:'image/jpeg',buffer:Buffer.from('proof')});
 const form=await page.evaluate(()=>{const data=new FormData(document.querySelector('#form'));return {project:data.getAll('project'),amount:data.getAll('amount'),file:data.get('files').name};});
 assert.deepEqual(form,{project:['Changed'],amount:['55.75'],file:'proof.jpg'});
 await report.locator('select').nth(0).selectOption('c0');await report.locator('select').nth(1).selectOption('c1');
 assert.equal(await report.locator('.tabulator-group').count(),5);
 console.log('Calculation rows',await report.locator('.tabulator-calcs').count());
 assert.match(await report.innerText(),/USD 300.75/);assert.match(await report.innerText(),/EUR 30.00/);
 await report.locator('input[type=search]').fill('SO1');
 const exported=await page.evaluate(()=>systemGrids.payload(document.querySelector('#report')));
 assert.equal(exported.rows.filter(row=>row[0]==='SO1').length,2);
 assert.match(exported.rows.at(-1)[2],/USD 300.75/);
 await edit.getByRole('button',{name:'删除',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#edit tbody').children.length===0);
 await page.screenshot({path:path.join(__dirname,'../tabulator-check.png'),fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS: form values/files, remove action, nested grouping, currency totals, filtered export');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
