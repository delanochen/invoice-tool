/* Tabulator 6.3.1. The original table remains the form/action model so existing
 * validation, attachment handlers and POST payloads do not depend on rendering. */
(() => {
  'use strict';
  const instances = new Map();
  const text = node => (node?.textContent || '').trim();
  const t = value => window.uiTranslate ? window.uiTranslate(value) : value;
  const monetary = /^(金额|明细金额|合同金额|税额|合计|总额|报销金额|报销总额|工时费|差旅费|里程费|基本工资|标准工资|交通工资|加班工资|假期工资|自驾车补|随行车补|租车驾驶补贴|补贴|合计工资|住宿费|机票费|行李费|租车费|燃油费|停车费|出租车费|住宿|机票|行李|租车|燃油|停车|出租车|其他|Amount|Tax|Line Total)$/;
  const dimension = /姓名|工单|站点|客户|员工|人员|施工员|创建人|提交人|开票人|项目|日期|时间|状态|国家|业主|报销编号|发票编号|Description/;
  const valueOf = cell => {
    const control = cell?.querySelector('input:not([type=hidden]),select,textarea');
    return control ? (control.tagName === 'SELECT' ? text(control.selectedOptions[0]) : control.value) : text(cell);
  };
  function amounts(value, currency) {
    const result = {};
    const pattern = /(?:\b(USD|EUR|GBP|CAD|CNY)\s*|([$€£¥])\s*)?(-?\d[\d,]*(?:\.\d+)?)/g;
    const matches = [...String(value).matchAll(pattern)];
    // Ignore compound descriptions; a financial cell must contain one amount.
    if (matches.length !== 1) return result;
    const match = matches[0];
    const unit = currency || match[1] || ({'$':'USD','€':'EUR','£':'GBP','¥':'CNY'}[match[2]]) || 'USD';
    result[unit] = Math.round(Number(match[3].replaceAll(',', '')) * 100);
    return result;
  }
  function total(values) {
    const sums = {};
    values.forEach(value => Object.entries(value || {}).forEach(([currency, cents]) => { sums[currency] = (sums[currency] || 0) + cents; }));
    return Object.entries(sums).map(([currency, cents]) => `${currency} ${(cents / 100).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}`).join('\n');
  }
  function button(label, action) {
    const node = document.createElement('button'); node.type = 'button'; node.textContent = t(label);
    node.addEventListener('click', action); return node;
  }
  class Grid {
    constructor(source) {
      this.source = source; this.rows = new Map(); this.nextId = 0; this.ready = false;
      this.listeners=new AbortController();
      this.labels = [...source.tHead.rows[0].cells].map(cell => (cell.dataset.gridLabel || text(cell)).replace(/（.*）$/, ''));
      this.headers = this.labels.map(t);
      this.money = this.labels.map(label => !location.pathname.includes('employee-grades') && monetary.test(label));
      this.editable = !!source.querySelector('input:not([type=checkbox]):not([type=hidden]),textarea,select');
      this.key = `grid:${document.body.dataset.gridUser || ''}:${location.pathname}:${source.id || [...document.querySelectorAll('table')].indexOf(source)}`;
      this.shell = document.createElement('section'); this.shell.className = 'system-grid grid-building';
      if(source.classList.contains('photo-query-table'))this.shell.classList.add('photo-ledger-grid');
      if(source.classList.contains('ledger-table'))this.shell.classList.add('field-ledger-grid');
      this.tools = document.createElement('div'); this.tools.className = 'grid-tools no-print';
      this.host = document.createElement('div'); this.shell.append(this.tools, this.host); source.before(this.shell);
      if(source.classList.contains('service-orders-table')) this.shell.classList.add('service-orders-grid');
      this.data = this.read();
      const columns = this.headers.map((title, index) => ({
        title, field:`c${index}`, minWidth:85, width: /备注|说明|附件|工作内容/.test(title) ? 240 : undefined,
        widthGrow: /备注|说明/.test(title) ? 2 : 1,
        headerSort: true, variableHeight:true,
        sorter: (a,b) => this.money[index] ? this.numeric(a)-this.numeric(b) : String(a).localeCompare(String(b), undefined, {numeric:true}),
        hozAlign: this.money[index] ? 'right' : 'left',
        formatter: cell => this.mirror(this.rows.get(cell.getData()._id)?.cells[index], cell),
        ...(this.money[index] ? {bottomCalc:(values, data) => total(data.map(row => row[`m${index}`])), bottomCalcFormatter:'textarea'} : {}),
      }));
      if (this.money.some(Boolean) && !this.money[0]) columns[0].bottomCalc = () => t('小计 / 合计');
      this.grid = new Tabulator(this.host, {
        data:this.data, index:'_id', columns, layout:'fitDataStretch', renderVertical:'basic',
        movableColumns:true, columnCalcs:'both', groupClosedShowCalcs:true,
        groupToggleElement:'header', placeholder:t('没有符合条件的记录'),
        groupHeader:(value,count,data,group) => {
          const label=button(`${value || '—'} · ${count}`,event => { event.stopPropagation(); group.toggle(); });
          label.className='grid-group-toggle';
          label.setAttribute('aria-expanded',String(group.isVisible()));
          return label;
        },
        rowFormatter: row => {
          const sourceRow = this.rows.get(row.getData()._id);
          const element = row.getElement();
          element.classList.toggle('is-selected', sourceRow?.classList.contains('is-selected'));
          ['closed-paid','closed-invoiced','open-invoiced'].forEach(className => element.classList.toggle(className, sourceRow?.classList.contains(className)));
        },
      });
      this.grid.on('tableBuilt', () => { this.ready=true; source.classList.add('grid-source'); this.controls(); this.sync(); this.shell.classList.remove('grid-building'); settle(); });
      this.grid.on('renderComplete', () => { this.parentTotals(); this.updateCount(); });
      this.grid.on('dataFiltered', () => this.updateCount());
      this.grid.on('dataProcessed', () => this.updateCount());
      this.grid.on('columnResized', () => this.parentTotals());
      this.grid.on('groupVisibilityChanged', (group,visible) => {
        group.getElement()?.querySelector?.('.grid-group-toggle')?.setAttribute('aria-expanded',String(visible));
        this.parentTotals();
      });
      this.grid.on('columnVisibilityChanged', () => this.parentTotals());
      this.grid.on('rowClick', (event,row) => {
        if (event.target.closest('a,button,input,select,textarea,label')) return;
        this.rows.get(row.getData()._id)?.click();
      });
      this.observer = new MutationObserver(() => this.schedule());
      this.observer.observe(source, {subtree:true, childList:true, characterData:true, attributes:true});
      source.addEventListener('input', () => this.schedule());
      source.addEventListener('change', () => this.schedule());
      source.addEventListener('invalid', event => {
        event.preventDefault();
        if(this.validationPending) return;
        this.validationPending=true;
        const row=this.grid.getRow(event.target.closest('tr')._gridId);
        const column=this.grid.getColumn(`c${event.target.closest('td').cellIndex}`);column?.show();
        let group=row?.getGroup();while(group){group.show();group=group.getParentGroup();}
        setTimeout(()=>{
          this.validationPending=false;
          const mirror = [...this.host.querySelectorAll('input,select,textarea')].find(node => node._source === event.target);
          if (mirror) { mirror.focus(); mirror.setCustomValidity(event.target.validationMessage); mirror.reportValidity(); }
        },0);
      }, true);
      source.closest('form')?.addEventListener('reset',()=>setTimeout(()=>this.schedule(),0),{signal:this.listeners.signal});
      this.resizeObserver=new ResizeObserver(() => { if(this.ready && this.host.offsetWidth) this.grid.redraw(); });this.resizeObserver.observe(this.shell);
    }
    numeric(value) { return Number(String(value).replace(/[^\d.-]/g,'')) || 0; }
    parentTotals() {
      if(!this.ready) return;
      this.host.querySelectorAll('.grid-parent-calc').forEach(node=>node.remove());
      const levels=new Map();const parents=[];
      const walk=(groups,depth)=>groups.forEach(group=>{levels.set(group.getElement(),depth);const children=group.getSubGroups();if(children.length){walk(children,depth+1);parents.push(group);}});
      walk(this.grid.getGroups(),0);
      const gather=group=>group.getSubGroups().length ? group.getSubGroups().flatMap(gather) : group.getRows().map(row=>row.getData());
      for(const group of parents){
        const header=group.getElement();if(!header?.parentElement)continue;
        const data=gather(group);const summary=document.createElement('div');summary.className='tabulator-row tabulator-calcs grid-parent-calc';summary.setAttribute('role','row');
        let labeled=false;
        this.grid.getColumns().filter(column=>column.isVisible()).forEach(column=>{
          const cell=document.createElement('div');cell.className='tabulator-cell';cell.setAttribute('role','cell');cell.style.width=`${column.getWidth()}px`;cell.style.whiteSpace='pre-wrap';
          const index=Number(column.getField().slice(1));
          if(this.money[index]){cell.textContent=total(data.map(row=>row[`m${index}`]));cell.style.textAlign='right';}
          else if(!labeled){cell.textContent=`${group.getKey()} · ${t('小计')}`;labeled=true;}
          summary.append(cell);
        });
        let next=header.nextElementSibling;
        while(next && !(levels.has(next) && levels.get(next)<=levels.get(header)))next=next.nextElementSibling;
        header.parentElement.insertBefore(summary,next);
      }
    }
    read() {
      this.rows.clear();
      return [...this.source.tBodies].flatMap(body => [...body.rows]).filter(row => !row.querySelector('td.empty') && !row.matches('.report-total,.table-summary-row') && !row.hidden && row.style.display !== 'none').map(row => {
        if(!row._gridId) row._gridId = ++this.nextId;
        this.rows.set(row._gridId,row);
        const data = {_id:row._gridId};
        this.headers.forEach((_,index) => {
          data[`c${index}`]=valueOf(row.cells[index]);
          const input=row.cells[index]?.querySelector('input[data-auto-amount]');
          const value=input ? Number(input.value||0)+Number(input.dataset.autoAmount||0) : data[`c${index}`];
          data[`m${index}`]=amounts(value,row.cells[index]?.dataset.gridCurrency || row.dataset.currency);
        });
        return data;
      });
    }
    mirror(source, cell) {
      const wrapper = document.createElement('div'); wrapper.className='grid-cell-content';
      if (!source) return wrapper;
      wrapper.innerHTML = source.innerHTML;
      const originals = [...source.querySelectorAll('*')];
      [...wrapper.querySelectorAll('*')].forEach((copy,index) => {
        const original=originals[index]; copy.removeAttribute('id'); copy.removeAttribute('name'); copy.removeAttribute('form');
        [...copy.attributes].filter(attr => attr.name.startsWith('on')).forEach(attr => copy.removeAttribute(attr.name));
        if (copy.matches('input,select,textarea')) {
          copy._source=original; copy.removeAttribute('required');
          if(copy.type !== 'file') copy.value=original.value;
          else copy.files=original.files;
          if('checked' in copy) copy.checked=original.checked;
          for(const eventName of ['input','change']) copy.addEventListener(eventName,event => {
            event.stopPropagation(); copy.setCustomValidity('');
            if(copy.type==='file') original.files=copy.files;
            else original.value=copy.value;
            if('checked' in copy) original.checked=copy.checked;
            original.dispatchEvent(new Event(eventName,{bubbles:true}));
          });
        } else if (copy.matches('button,a,[onclick],[data-image-preview]') || original.hasAttribute('onclick')) {
          copy.addEventListener('click',event => { event.preventDefault(); event.stopPropagation(); original.click(); });
        }
      });
      wrapper.addEventListener('focusout', () => setTimeout(() => this.schedule(),0));
      return wrapper;
    }
    schedule() { clearTimeout(this.timer); this.timer=setTimeout(() => this.sync(),60); }
    sync() {
      if(!this.ready) return;
      // Do not reconstruct an active editor (including file pickers) while typing.
      if(this.host.contains(document.activeElement) && document.activeElement.matches('input:not([type=file]):not([type=checkbox]),textarea')) return;
      const data=this.read();
      const signature=JSON.stringify(data)+this.source.innerHTML+JSON.stringify([...this.source.querySelectorAll('input,select,textarea')].map(input=>[input.value,input.checked,input.disabled]));
      if(signature===this.signature) return;
      this.signature=signature; this.grid.replaceData(data).then(()=>this.updateCount());
    }
    updateCount() {
      if(!this.count) return;
      const all=this.grid.getDataCount(); const active=this.grid.getDataCount('active');
      this.count.textContent=`${active===all ? all : `${active} / ${all}`} ${t('条记录')}`;
    }
    controls() {
      this.count=document.createElement('span'); this.count.className='grid-count'; this.tools.append(this.count);
      if(!this.editable) {
        const search=document.createElement('input'); search.type='search'; search.placeholder=t('表内搜索'); search.setAttribute('aria-label',t('表内搜索'));
        search.addEventListener('input',() => this.grid.setFilter(row => this.headers.some((_,index) => String(row[`c${index}`]).toLocaleLowerCase().includes(search.value.toLocaleLowerCase()))));
        this.tools.append(search);
      }
      if(this.money.some(Boolean)) {
        const selects=[0,1].map(level => {
          const select=document.createElement('select'); select.setAttribute('aria-label',t(level ? '二级分组' : '一级分组'));
          select.add(new Option(t(level ? '二级分组：无' : '分组：无'),''));
          this.headers.forEach((label,index) => { if(!this.money[index] && dimension.test(this.labels[index])) select.add(new Option(label,`c${index}`)); });
          this.tools.append(select); return select;
        });
        selects.forEach(select => select.addEventListener('change',() => { const fields=[...new Set(selects.map(item=>item.value).filter(Boolean))]; this.groupFields=fields; this.grid.setGroupBy(fields.length ? fields : false); }));
        const toggle = open => { const visit=groups=>groups.forEach(group=>{open?group.show():group.hide();visit(group.getSubGroups());});visit(this.grid.getGroups()); };
        this.tools.append(button('展开',()=>toggle(true)),button('折叠',()=>toggle(false)));
      }
      const settings=document.createElement('details'); settings.className='grid-columns';
      const summary=document.createElement('summary'); summary.textContent=t('列设置');settings.append(summary);
      const menu=document.createElement('div');
      this.grid.getColumns().forEach(column=>{const label=document.createElement('label');const check=document.createElement('input');check.type='checkbox';check.checked=true;check.addEventListener('change',()=>check.checked?column.show():column.hide());label.append(check,document.createTextNode(column.getDefinition().title));menu.append(label);});
      menu.append(button('恢复默认',()=>{this.grid.getColumns().forEach(column=>column.show());menu.querySelectorAll('input').forEach(input=>input.checked=true);}));settings.append(menu);this.tools.append(settings);
      document.addEventListener('click',event=>{if(!settings.contains(event.target))settings.open=false;},{signal:this.listeners.signal});
      settings.addEventListener('focusout',()=>setTimeout(()=>{if(!settings.contains(document.activeElement))settings.open=false;},0));
    }
    export() {
      const columns=this.grid.getColumns().filter(column=>column.isVisible() && !/^(操作|选择)$/.test(column.getDefinition().title));
      const active=this.grid.getData('active');
      const fields=this.groupFields || [];
      const summary=(data,label)=>columns.map((column,index)=>{
        const field=column.getField();const number=Number(field.slice(1));
        return this.money[number] ? total(data.map(row=>row[`m${number}`])) : index===0 ? label : '';
      });
      const collect=(data,level)=>{
        if(level>=fields.length) return data.map(row=>columns.map(column=>row[column.getField()]));
        const groups=new Map();data.forEach(row=>{const key=row[fields[level]];if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);});
        return [...groups].flatMap(([key,rows])=>[...collect(rows,level+1),summary(rows,`${key} · ${t('小计')}`)]);
      };
      const rows=collect(active,0);
      if(this.money.some(Boolean))rows.push(summary(active,t('合计')));
      return {headers:columns.map(column=>column.getDefinition().title),rows};
    }
  }
  function settle() {
    if([...instances.values()].every(instance=>instance.ready)) document.documentElement.classList.remove('grids-pending');
  }
  function scan() {
    if(!window.Tabulator || location.pathname.includes('/print')) { document.documentElement.classList.remove('grids-pending'); return; }
    for(const [source,instance] of instances)if(!source.isConnected){clearTimeout(instance.timer);instance.listeners.abort();instance.observer.disconnect();instance.resizeObserver.disconnect();instance.grid.destroy();instance.shell.remove();instances.delete(source);}
    document.querySelectorAll('table').forEach(source=>{
      if(instances.has(source) || source.closest('.system-grid') || !source.tHead?.rows[0]?.cells.length) return;
      try { const instance=new Grid(source); instances.set(source,instance); } catch(error) { source.dataset.gridFailed='true'; source.previousElementSibling?.classList.contains('grid-building') && source.previousElementSibling.remove(); console.error('Grid initialization failed',error); }
    });
    settle();
  }
  window.systemGrids={instances,scan,payload:source=>instances.get(source)?.export()};
  document.addEventListener('DOMContentLoaded',()=>{
    scan();
    let timer; new MutationObserver(records=>{
      if(records.some(record=>record.target.nodeName==='THEAD' || [...record.addedNodes,...record.removedNodes].some(node=>node.nodeType===1 && (node.matches('table,thead') || node.querySelector('table'))))) {clearTimeout(timer);timer=setTimeout(scan,0);}
    }).observe(document.body,{childList:true,subtree:true});
  });
})();
