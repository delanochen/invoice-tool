const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const puppeteer = require(process.env.PUPPETEER_MODULE || "puppeteer");

(async () => {
 const browser = await puppeteer.launch({headless:true,args:["--no-sandbox"]});
 try {
  const page = await browser.newPage();
  const errors=[];
  page.on("pageerror",err=>errors.push(err.message));
  await page.setViewport({width:1365,height:900});
  async function load(file) {
   errors.length=0;
   await page.goto(pathToFileURL(path.resolve(file)).href);
   await page.waitForFunction(()=>window.systemGrids?.instances.size &&
    [...window.systemGrids.instances.values()].every(grid=>grid.ready));
  }
  if (process.argv[3]) {
   await load(process.argv[2]);
   const summary = await page.$(".grid-cell-content details summary");
   assert(summary,"Old fixture should contain the mirrored summary");
   await summary.click();
   const diagnosis=await page.evaluate(()=>{
    const details=document.querySelector(".grid-cell-content details[open]");
    return {opened:!!details,detailTableDisplay:details?.querySelector("table") &&
     getComputedStyle(details.querySelector("table")).display,
     gridCount:window.systemGrids.instances.size};
   });
   console.log("Before:",JSON.stringify(diagnosis));
  }
  await load(process.argv[3] || process.argv[2]);
  assert.equal(await page.$$eval(".grid-source",nodes=>nodes.length),1,
   "Only the main table should be converted to a grid");
  for (const index of [1,2,1]) {
   const selector='.system-grid [data-dialog-open="payrollDifference-'+index+'"]';
   await page.click(selector);
   const dialog="#payrollDifference-"+index;
   await page.waitForSelector(dialog+"[open]",{visible:true});
   const expected=index===1?"WORKER_A":"WORKER_B";
   assert((await page.$eval(dialog,node=>node.innerText)).includes("DETAIL_ONLY_"+expected));
   const geometry=await page.$eval(dialog+" tbody tr",node=>{
    const r=node.getBoundingClientRect();
    return {width:r.width,height:r.height,display:getComputedStyle(node).display};
   });
   assert(geometry.width>0 && geometry.height>0 && geometry.display!=="none");
   assert.equal(await page.$eval(dialog,node=>!!node.querySelector(".system-grid")),false);
   await page.click(dialog+" [data-dialog-close]");
   await page.waitForFunction(selector=>!document.querySelector(selector).open,{},dialog);
  }
  await page.evaluate(()=>{
   const grid=[...window.systemGrids.instances.values()][0];
   grid.grid.setSort("c0","desc");
  });
  await page.click('.system-grid [data-dialog-open="payrollDifference-2"]');
  await page.waitForSelector("#payrollDifference-2[open]",{visible:true});
  assert((await page.$eval("#payrollDifference-2",node=>node.innerText)).includes("DETAIL_ONLY_WORKER_B"));
  await page.keyboard.press("Escape");
  await page.setViewport({width:390,height:844});
  await page.click('.system-grid [data-dialog-open="payrollDifference-1"]');
  await page.waitForSelector("#payrollDifference-1[open]",{visible:true});
  const rect=await page.$eval("#payrollDifference-1",node=>{
   const r=node.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width};
  });
  assert(rect.width<=390 && rect.left>=0 && rect.right<=391);
  assert.deepEqual(errors,[]);
  console.log("After: desktop, repeated open/close, row selection after sorting, Escape, mobile width and visible details passed");
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
