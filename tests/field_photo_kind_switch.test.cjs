const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.FIELD_WORK_JS || path.join(__dirname, '../static/field-work.js'), 'utf8');
const begin = source.indexOf('  const KIND_LABEL');
const end = source.indexOf('  async function verifyTimePassword', begin);
assert(begin >= 0 && end > begin);
function setup(type = 'departure') {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) {
      const flags = new Set();
      elements.set(id, {hidden:false, textContent:'', append(){}, classList:{
        add:value=>flags.add(value), remove:value=>flags.delete(value),
        contains:value=>flags.has(value), toggle(value,yes){yes ? flags.add(value) : flags.delete(value);},
      }});
    }
    return elements.get(id);
  };
  const notices = [];
  const context = vm.createContext({$, batch:null, deviceSession:null, taking:false,
    processingFiles:false,syncing:false,captureContext:null,key:()=> 'batch-test',
    queued:async()=>[],notice:message=>notices.push(message),
    setFieldText:(id,text)=>{$(id).textContent=text;},clearDeviceInputs(){},resetDevice(){},renderQueue(){},
  });
  vm.runInContext(source.slice(begin,end),context);
  context.startKind(type);
  return {context,$,notices};
}
for (const desired of ['arrival','safety','equipment']) {
  test(`pending departure photos reject ${desired} without misleading selection`,async()=>{
    const {context:c,$,notices}=setup();
    const queuedPhoto={photo_type:'departure'};
    c.queued=async()=>[queuedPhoto];
    await c.chooseKind(desired);
    assert.equal(c.batch.type,'departure');
    assert.equal($('departureKind').classList.contains('primary'),true);
    assert.equal($('arrivalKind').classList.contains('primary'),false);
    assert.equal($('safetyKind').classList.contains('primary'),false);
    assert.equal(queuedPhoto.photo_type,'departure');
    assert(notices.at(-1).includes('离场'));
  });
}
test('accepted switch updates actual type and highlighted type together',async()=>{
  const {context:c,$}=setup();
  await c.chooseKind('arrival');
  assert.equal(c.batch.type,'arrival');
  assert.equal($('arrivalKind').classList.contains('primary'),true);
  assert.equal($('departureKind').classList.contains('primary'),false);
  await c.chooseKind('safety');
  assert.equal(c.batch.type,'safety');
  assert.equal($('safetyKind').classList.contains('primary'),true);
});
test('opening non-equipment choices preserves the current capture state',async()=>{
  for(const type of ['equipment','departure','arrival','safety']) {
    const {context:c,$}=setup(type);
    await c.chooseKind('general');
    assert.equal(c.batch.type,type);
    assert.equal($('generalKindChoices').hidden,false);
    assert.equal($('deviceSession').hidden,type!=='equipment');
    if(type!=='equipment')assert.equal($(type+'Kind').classList.contains('primary'),true);
  }
});
for(const flag of ['taking','processingFiles','syncing','captureContext']) {
  test(`${flag} prevents classification changes during processing`,async()=>{
    const {context:c,$}=setup();c[flag]=true;
    await c.chooseKind('arrival');
    assert.equal(c.batch.type,'departure');
    assert.equal($('departureKind').classList.contains('primary'),true);
  });
}
test('out-of-order queue checks cannot apply an obsolete selection',async()=>{
  const {context:c,$}=setup();const pending=[];
  c.queued=()=>new Promise(resolve=>pending.push(resolve));
  const first=c.chooseKind('arrival');const last=c.chooseKind('safety');
  pending[1]([]);await last;pending[0]([]);await first;
  assert.equal(c.batch.type,'safety');
  assert.equal($('safetyKind').classList.contains('primary'),true);
});
test('capture starting while the queue check is pending keeps its original type',async()=>{
  const {context:c}=setup();let resolve;
  c.queued=()=>new Promise(done=>{resolve=done;});
  const attempt=c.chooseKind('arrival');c.taking=true;resolve([]);await attempt;
  assert.equal(c.batch.type,'departure');
});
test('queue read failure preserves both capture type and highlighted choice',async()=>{
  const {context:c,$,notices}=setup();c.queued=async()=>{throw new Error('storage unavailable');};
  await c.chooseKind('arrival');
  assert.equal(c.batch.type,'departure');
  assert.equal($('departureKind').classList.contains('primary'),true);
  assert(notices.at(-1).includes('未切换'));
});
const contextStart=source.indexOf('  async function makeContext(');
const contextEnd=source.indexOf('  async function processedPhoto(',contextStart);
const uploadStart=source.indexOf('  async function syncQueue(');
const uploadEnd=source.indexOf('  async function completeBatch(',uploadStart);
assert(contextStart>=0&&contextEnd>contextStart&&uploadStart>=0&&uploadEnd>uploadStart);
function captureSetup(type) {
  const {context:c,$}=setup(type);
  Object.assign(c,{identityReady:true,profile:{can_capture:true,user:{id:2,name:'Test'},technicians:[{id:2,name:'Test'}]},
    chosenOrder:()=>({id:44,order_number:'SO-TEST',client_name:'Test',site_address:'Test'}),
    cameraSelection:null,useSystemTime:true,position:{latitude:1,longitude:1,accuracy:1},locationNote:'',
    checkLocation:async()=>true,localStorage:{setItem(){}},locate:async()=>{},
  });
  $('timezoneName').value='UTC';$('technicianSelect').value='2';$('photoNote').value='';
  vm.runInContext(source.slice(contextStart,contextEnd),c);
  return {c,$};
}
for(const type of ['arrival','safety','departure']) {
  test(`${type} capture retains its own type while asynchronous work is pending`,async()=>{
    const {c}=captureSetup(type);let resume;c.locate=()=>new Promise(resolve=>{resume=resolve;});
    const pending=c.makeContext('camera');
    c.batch.type='departure';c.batch.id='later-batch';
    resume();const photo=await pending;
    assert.equal(photo.photo_type,type);
    assert.equal(photo.batch_id,'batch-test');
    assert.equal(Object.hasOwn(photo,'equipment_number'),false);
  });
}
test('equipment capture keeps its own device metadata across asynchronous work',async()=>{
  const {c}=captureSetup('equipment');
  c.deviceSession={id:'device-first',equipment_number:'TEST123',position_number:'A1',container_number:'C1',no_equipment_number:false};
  let resume;c.locate=()=>new Promise(resolve=>{resume=resolve;});
  const pending=c.makeContext('camera');c.batch.type='safety';c.deviceSession=null;resume();
  const photo=await pending;
  assert.equal(photo.photo_type,'equipment');assert.equal(photo.equipment_number,'TEST123');
});
for(const type of ['arrival','safety','departure']) {
  test(`upload sends the saved ${type} classification, independent of the current group`,async()=>{
    const {c}=captureSetup(type);const photo=await c.makeContext('camera');
    photo.blob=new Blob(['synthetic image bytes'],{type:'image/jpeg'});
    c.batch.type='departure';
    const removed=[];let uploaded;
    Object.assign(c,{Blob,FormData,navigator:{onLine:true},fieldText:value=>value,
      queued:async()=>[photo],storePhoto:async(item,remove)=>{if(remove)removed.push(item.client_id);},
      deletingDrafts:false,uploadingPhotoId:null,uploadIndex:0,uploadTotal:0,
      requestAPI:async(url,options)=>{
        if(url!=='/api/field/photos')return {ok:true,json:async()=>({user:{id:2},csrf:'test'})};
        uploaded=options.body;return {ok:true,status:200,json:async()=>({ok:true}),headers:new Map()};
      },
    });
    vm.runInContext(source.slice(uploadStart,uploadEnd),c);
    await c.syncQueue();
    assert(uploaded,JSON.stringify(photo));
    assert.equal(uploaded.get('photo_type'),type);
    assert.equal(uploaded.get('batch_id'),'batch-test');
    assert.deepEqual(removed,[photo.client_id]);
  });
}
