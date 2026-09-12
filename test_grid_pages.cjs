const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:950}});const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.log(e.stack)});page.on('console',msg=>{if(msg.type()==='error')console.log(msg.text());});
 await page.goto('http://127.0.0.1:8786/test-login');await page.waitForURL('**/workspace#**');
 await page.waitForFunction(()=>document.querySelector('iframe')?.contentDocument?.querySelector('h1'));
 const frame=page.frames().find(f=>f.parentFrame());
 for(const url of ['/reports/service-reports','/invoices','/reports/expenses','/users','/service-orders/1/expenses/new','/service-orders/1/customer-reimbursement','/service-reports/1/edit','/employee-grades','/reports/customer-reimbursements']){
  await frame.goto('http://127.0.0.1:8786'+url);await frame.waitForTimeout(500);
  const result=await frame.evaluate(()=>({title:document.title,tables:document.querySelectorAll('table').length,converted:document.querySelectorAll('table.grid-source').length,errors:document.body.innerText.includes('Internal Server Error')}));
  console.log(url,JSON.stringify(result));assert.equal(result.errors,false);assert.equal(result.tables,result.converted);
 }
 await frame.goto('http://127.0.0.1:8786/service-orders/1/expenses/new');await frame.waitForSelector('table.grid-source',{state:'attached'});
 const first=frame.locator('.system-grid .tabulator-row').filter({has:frame.locator('input[type=number]')}).first();
 const choice=await first.locator('select').first().evaluate(select=>[...select.options].find(option=>option.value)?.value);
 await first.locator('select').first().selectOption(choice);await first.locator('input[type=number]').fill('12.34');
 await first.locator('input[type=file]').setInputFiles({name:'proof.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB9sAAAAASUVORK5CYII=','base64')});
 await frame.locator('#addExpenseItem').click();
 await frame.waitForFunction(()=>document.querySelectorAll('.tabulator-row input[type=number]').length===2);
 const inputs=frame.locator('.system-grid .tabulator-row input[type=number]');await inputs.nth(1).fill('56.78');
 const choices=frame.locator('.system-grid .tabulator-row .expense-project-select');await choices.nth(1).selectOption(choice);
 await frame.locator('h2').filter({hasText:'报销明细'}).click();await frame.waitForTimeout(200);
 const payload=await frame.evaluate(()=>{const data=new FormData(document.querySelector('#expenseItems').closest('form'));return {amounts:data.getAll('item_amount'),projects:data.getAll('project_id'),files:[...data].filter(([key,value])=>key.startsWith('item_attachments_')&&value.size).map(([key,value])=>[key,value.name])};});
 assert.deepEqual(payload.amounts,['12.34','56.78']);assert.equal(payload.projects.length,2);assert.equal(payload.files.length,1);assert.equal(payload.files[0][1],'proof.png');
 assert.ok(await frame.locator('.system-grid .pending-attachment-card').count());
 await frame.locator('.system-grid .pending-attachment-card button').click();await frame.waitForTimeout(150);
 assert.equal(await frame.evaluate(()=>[...new FormData(document.querySelector('#expenseItems').closest('form'))].filter(([key,value])=>key.startsWith('item_attachments_')&&value.size).length),0);
 await frame.locator('.system-grid input[type=file]').first().setInputFiles({name:'saved-proof.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB9sAAAAASUVORK5CYII=','base64')});
 await frame.locator('[data-expense-submit][value=save]').click();
 await frame.waitForURL(/\/expenses\/\d+\/edit$/);
 await frame.waitForSelector('table.grid-source',{state:'attached'});
 assert.match(await frame.locator('main').innerText(),/saved-proof.png/);
 assert.match(await frame.locator('main').innerText(),/69.12/);
 await frame.goto('http://127.0.0.1:8786/reports/expenses');await frame.waitForTimeout(500);
 await page.screenshot({path:require('path').join(__dirname,'../tabulator-page.png'),fullPage:true});
 assert.deepEqual(errors,[]);
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
