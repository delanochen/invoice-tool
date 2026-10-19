const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/settlement-source-editor.js', 'utf8');
function setup() {
  const handlers = {}, choiceHandlers = {}, inputHandlers = {}, reasonHandlers = {};
  const panel = {hidden:true,dataset:{settlementTabPanel:'other'}};
  const choice = {checked:false,disabled:false,addEventListener:(n,f)=>choiceHandlers[n]=f,setCustomValidity(){}};
  const amount = {value:'373.99',max:'373.99',validity:'',addEventListener:(n,f)=>inputHandlers[n]=f,
    setCustomValidity(v){this.validity=v;},focus(){this.focused=true;},reportValidity(){this.reported=this.validity;},
    closest(q){return q==='tr'?row:panel;}};
  const reason = {value:'',addEventListener:(n,f)=>reasonHandlers[n]=f,
    setCustomValidity(v){this.validity=v;},focus(){this.focused=true;},reportValidity(){this.reported=this.validity;},closest(){return panel;}};
  const row = {querySelector(q){return {'.source-choice':choice,'.source-settled-amount':amount,'.source-adjustment-reason':reason}[q];},querySelectorAll(){return [choice,amount,reason];}};
  choice.closest=()=>row;
  const pool = {querySelectorAll(q){return {'.source-choice':[choice],'.source-settled-amount':[amount],'.source-adjustment-reason':[reason],'tbody tr':[row]}[q];},addEventListener(n,f){handlers[n]=f;}};
  const form = {addEventListener(n,f){handlers[n]=f;}};
  const confirms=[],timers=[];
  const window={uiConfirm(message){confirms.push(message);return window.answer;},answer:true,setTimeout:f=>timers.push(f)};
  const document={getElementById:id=>id==='customerReimbursementForm'?form:pool,querySelector:()=>({click(){panel.hidden=false;}})};
  vm.runInNewContext(source,{document,window});
  return {choice,amount,reason,panel,confirms,window,handlers,inputHandlers,choiceHandlers,submit(){const e={defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};handlers.submit(e);timers.splice(0).forEach(f=>f());return e;}};
}
{
 const s=setup();s.amount.value='7.47';s.inputHandlers.input();assert.equal(s.choice.checked,true);assert.equal(s.submit().defaultPrevented,true);assert.match(s.reason.reported,/原因/);assert.equal(s.panel.hidden,false);
 s.reason.value='Customer share';assert.equal(s.submit().defaultPrevented,false);assert.match(s.confirms[0],/7.47/);assert.match(s.confirms[0],/366.52/);
}
{
 const s=setup();s.choice.checked=true;s.choiceHandlers.change();assert.equal(s.confirms.length,0);assert.equal(s.amount.value,'373.99');
}
for (const amount of ['0','7.47']) {
 const s=setup();s.amount.value=amount;s.reason.value='Customer share';s.inputHandlers.input();assert.equal(s.submit().defaultPrevented,false);
}
for (const amount of ['','-1','374','NaN','Infinity']) {
 const s=setup();s.amount.value=amount;s.inputHandlers.input();assert.equal(s.submit().defaultPrevented,true);assert.match(s.amount.reported,/金额/);
}
{
 const s=setup();s.amount.value='7.47';s.reason.value='Customer share';s.inputHandlers.input();s.window.answer=false;assert.equal(s.submit().defaultPrevented,true);
}
{
 const s=setup();s.amount.value='7.47';s.inputHandlers.input();s.choice.checked=false;assert.equal(s.submit().defaultPrevented,false);assert.equal(s.confirms.length,0);
}
console.log('Settlement source editor: 12 interaction cases passed');
