const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/workspace.js', 'utf8');

function setup() {
  const timers = new Map(), windowEvents = {}, elements = {}, frames = [];
  let sequence = 0;
  function node(tag) {
    return {tag, hidden:false, handlers:{}, children:[], dataset:{},
      classList:{toggle(){}, add(){}, remove(){}},
      setAttribute(){}, removeAttribute(){}, scrollIntoView(){}, focus(){}, remove(){},
      append(...children){this.children.push(...children);},
      addEventListener(name, fn){(this.handlers[name] ||= []).push(fn);},
      querySelector(selector){return (this.queries ||= {})[selector] ||= node('control');},
    };
  }
  const location = {origin:'https://example.test', pathname:'/workspace', hash:'#%2Fservice-orders'};
  const document = {hidden:false, title:'Workspace',
    getElementById:id=>elements[id] ||= node(id),
    querySelectorAll:()=>[], addEventListener(){},
    createElement(tag) {
      const element = node(tag);
      if(tag === 'iframe') {
        const doc = node('document');
        doc.documentElement = {dataset:{}};
        doc.title = '工单 - 发票工具';
        doc.hasMain = false;
        doc.querySelector = selector => selector === '.main' && doc.hasMain ? node('main') : null;
        const win = node('window');
        win.document = doc;
        win.location = {href:'https://example.test/service-orders',pathname:'/service-orders'};
        element.contentWindow = win;
        element.navigations = [];
        Object.defineProperty(element, 'src', {set(value){this.navigations.push(value);}});
        frames.push(element);
      }
      return element;
    }
  };
  const window = {uiConfirm:()=>true, addEventListener(name, fn){windowEvents[name] = fn;}};
  const context = {window, document, location, navigator:{onLine:true}, URL,
    history:{replaceState(){}}, queueMicrotask:fn=>fn(),
    setTimeout(fn, delay){const id=++sequence;timers.set(id,{fn,delay});return id;},
    clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(source, context);
  const frame = frames[0], doc = frame.contentWindow.document;
  return {frame,doc,timers,windowEvents,location,elements,
    ready() {doc.hasMain=true;doc.documentElement.dataset.workspaceReady='1';},
    message(origin=location.origin, source=frame.contentWindow){windowEvents.message({origin,source,data:{type:'workspace:ready'}});},
    fire(delay){const entry=[...timers.entries()].find(([, timer])=>timer.delay===delay);assert.ok(entry);timers.delete(entry[0]);entry[1].fn();},
    load(){frame.handlers.load[0]();},
    failure(){return elements.workspacePages.children.find(child=>child.tag==='section');}
  };
}

{
  const s=setup();s.ready();s.message();
  assert.equal(s.timers.size,0,'body-ready cancels watchdog before iframe load');
  assert.equal(s.failure().hidden,true);
  assert.equal(s.frame.hidden,false);
  assert.equal(s.frame.navigations.length,1);
}
{
  const s=setup();s.ready();s.fire(45000);
  assert.equal(s.timers.size,0,'watchdog preserves a ready page even if ready message was missed');
  assert.equal(s.failure().hidden,true);
  assert.equal(s.frame.navigations.length,1);
}
{
  const s=setup();s.ready();s.message('https://untrusted.test');
  assert.equal(s.timers.size,1,'foreign origin cannot mark page ready');
  s.message(s.location.origin,{});
  assert.equal(s.timers.size,1,'unrelated window cannot mark page ready');
}
{
  const s=setup();s.ready();s.message();s.load();s.message();
  assert.equal(s.doc.handlers.submit.length,1,'early ready and later load must not duplicate save listeners');
  assert.equal(s.doc.handlers.click.length,1);
}
{
  const s=setup();s.fire(45000);
  assert.equal(s.failure().hidden,false,'actual missing page retains retry behavior');
  s.fire(1200);
  assert.equal(s.frame.navigations.length,2);
}
{
  const s=setup();s.frame.contentWindow.location.pathname='/login';s.load();
  assert.equal(s.location.href,'/login','expired session returns to login');
}
{
  const s=setup();s.doc.documentElement.dataset.workspaceReady='1';s.fire(45000);
  assert.equal(s.failure().hidden,false,'marker without application content is not ready');
}
console.log('Workspace loading: 7 regressions passed; no network or database');
