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
      elements.set(id, {hidden:false, textContent:'', append(){}, attrs:{}, setAttribute(name,value){this.attrs[name]=String(value);}, getAttribute(name){return this.attrs[name] ?? null;}, classList:{
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
    stream:null,
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
    assert.equal($('departureKind').getAttribute('aria-pressed')==='true',true);
    assert.equal($('arrivalKind').getAttribute('aria-pressed')==='true',false);
    assert.equal($('safetyKind').getAttribute('aria-pressed')==='true',false);
    assert.equal(queuedPhoto.photo_type,'departure');
    assert(notices.at(-1).includes('离场'));
  });
}
test('accepted switch updates actual type and highlighted type together',async()=>{
  const {context:c,$}=setup();
  await c.chooseKind('arrival');
  assert.equal(c.batch.type,'arrival');
  assert.equal($('arrivalKind').getAttribute('aria-pressed')==='true',true);
  assert.equal($('departureKind').getAttribute('aria-pressed')==='true',false);
  await c.chooseKind('safety');
  assert.equal(c.batch.type,'safety');
  assert.equal($('safetyKind').getAttribute('aria-pressed')==='true',true);
});
test('single-row type control shows device fields only for equipment',async()=>{
  for(const type of ['equipment','departure','arrival','safety']) {
    const {context:c,$}=setup(type);
    assert.equal(c.batch.type,type);
    assert.equal($('deviceSession').hidden, type !== 'equipment');
    for(const kind of ['equipment','departure','arrival','safety']) assert.equal($(kind+'Kind').getAttribute('aria-pressed'),String(kind===type));
  }
});
for(const flag of ['taking','processingFiles','syncing','captureContext']) {
  test(`${flag} prevents classification changes during processing`,async()=>{
    const {context:c,$}=setup();c[flag]=true;
    await c.chooseKind('arrival');
    assert.equal(c.batch.type,'departure');
    assert.equal($('departureKind').getAttribute('aria-pressed')==='true',true);
  });
}
test('out-of-order queue checks cannot apply an obsolete selection',async()=>{
  const {context:c,$}=setup();const pending=[];
  c.queued=()=>new Promise(resolve=>pending.push(resolve));
  const first=c.chooseKind('arrival');const last=c.chooseKind('safety');
  pending[1]([]);await last;pending[0]([]);await first;
  assert.equal(c.batch.type,'safety');
  assert.equal($('safetyKind').getAttribute('aria-pressed')==='true',true);
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
  assert.equal($('departureKind').getAttribute('aria-pressed')==='true',true);
  assert(notices.at(-1).includes('未切换'));
});
const contextStart=source.indexOf('  function fieldTextValue(');
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
    activeRepair:null,repairStage:'before',captureWatermarkTime:actual=>actual,photoFilenameTime:()=>'',
    photoTypeLabel:type=>({equipment:'设备',arrival:'进场',departure:'离场',safety:'自检'}[type]||type||'-'),
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
test('typed equipment reaches the watermark after the shutter, including values entered during location',async()=>{
  const {c,$}=captureSetup('equipment');
  c.deviceSession=null;
  $('equipmentNumber').value='';
  $('equipmentModel').value='';
  $('noEquipmentNumber').checked=false;
  let resume;c.locate=()=>new Promise(resolve=>{resume=resolve;});
  const pending=c.makeContext('camera');
  $('equipmentNumber').value='10232A0407536';
  $('equipmentModel').value='LCI-400CR-01AZ-3487U';
  $('positionNumber').value='b6-1';
  $('containerNumber').value='csqu3054383';
  $('repairFault').value='水泵干磨';
  resume();
  const photo=await pending;
  assert.equal(photo.photo_type,'equipment');
  assert.equal(photo.photo_type_label,'设备');
  assert.equal(photo.equipment_number,'10232A0407536');
  assert.equal(photo.equipment_model,'LCI-400CR-01AZ-3487U');
  assert.equal(photo.position_number,'B6-1');
  assert.equal(photo.container_number,'CSQU3054383');
  assert.equal(photo.fault_note,'水泵干磨');
});
test('equipment typed before the shutter survives the form being cleared during location',async()=>{
  const {c,$}=captureSetup('equipment');
  c.deviceSession=null;
  $('equipmentNumber').value='10232A0407536';
  $('equipmentModel').value='LCI-400CR-01AZ-3487U';
  let resume;c.locate=()=>new Promise(resolve=>{resume=resolve;});
  const pending=c.makeContext('camera');
  $('equipmentNumber').value='';
  $('equipmentModel').value='';
  resume();
  const photo=await pending;
  assert.equal(photo.equipment_number,'10232A0407536');
  assert.equal(photo.equipment_model,'LCI-400CR-01AZ-3487U');
});
test('a later edit in the form replaces a stale device session on the watermark',async()=>{
  const {c,$}=captureSetup('equipment');
  c.deviceSession={id:'device-old',equipment_number:'OLDNUMBER1234',equipment_model:'OLD',position_number:'Z9',container_number:'OLD',no_equipment_number:false};
  $('equipmentNumber').value='10232A0407536';
  $('equipmentModel').value='LCI-400CR-01AZ-3487U';
  const photo=await c.makeContext('camera');
  assert.equal(photo.equipment_number,'10232A0407536');
  assert.equal(photo.equipment_model,'LCI-400CR-01AZ-3487U');
  assert.equal(photo.photo_type,'equipment');
});
test('queued equipment photo keeps stored aliases when the form is blank',()=>{
  const {c}=captureSetup('arrival');
  const fields=c.equipmentFieldsFor({photo_type:'equipment',capture_session:'older',batch_id:'older:equipment',order_id:44,
    machine_number:'10232A0407536',photo_model:'LCI-400CR-01AZ-3487U',position_number:'b6-1',fault:'水泵干磨'});
  assert.equal(fields.equipment_number,'10232A0407536');
  assert.equal(fields.equipment_model,'LCI-400CR-01AZ-3487U');
  assert.equal(fields.position_number,'B6-1');
  assert.equal(fields.fault_note,'水泵干磨');
});
test('a blank queued equipment photo for this order picks up the open form',()=>{
  const {c,$}=captureSetup('equipment');
  $('equipmentNumber').value='10232A0407536';
  $('equipmentModel').value='LCI-400CR-01AZ-3487U';
  $('positionNumber').value='b6-1';
  $('containerNumber').value='csqu3054383';
  const fields=c.equipmentFieldsFor({photo_type:'equipment',capture_session:'older',batch_id:'older:equipment',order_id:44,equipment_number:''});
  assert.equal(fields.equipment_number,'10232A0407536');
  assert.equal(fields.equipment_model,'LCI-400CR-01AZ-3487U');
  assert.equal(fields.position_number,'B6-1');
  assert.equal(fields.container_number,'CSQU3054383');
});
