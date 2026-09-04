(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const SESSION_KEY = 'field-session';
  let profile = null, position = null, stream = null, syncing = false, taking = false;
  let currentOrder = null, watchId = null, warningResolve = null, lastWarning = 0, locationNote = '';
  let captureContext = null, previewURL = null, database = null, installPrompt = null;
  let identityReady = false;
  let farSamples = 0;
  async function requestAPI(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.method === 'POST' ? 60000 : 15000);
    try { return await fetch(url, {...options, signal:controller.signal}); }
    finally { clearTimeout(timer); }
  }
  const notice = (text, error = false) => { $('notice').textContent = text; $('notice').classList.toggle('error', error); };
  const textNode = (tag, text, className = '') => { const el = document.createElement(tag); el.textContent = text; el.className = className; return el; };
  const key = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');

  function openDB() {
    if (database) return database;
    database = new Promise((resolve, reject) => {
      const request = indexedDB.open('prasinos-field-photos', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('photos', {keyPath:'client_id'});
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => { database = null; reject(request.error); };
    });
    return database;
  }
  async function storePhoto(photo, remove = false) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('photos', 'readwrite');
      const store = tx.objectStore('photos');
      if (remove) store.delete(photo.client_id); else store.put(photo);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('手机存储写入失败'));
    });
  }
  async function queued() {
    if (!profile || !identityReady) return [];
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const request = db.transaction('photos').objectStore('photos').getAll();
      request.onsuccess = () => resolve(request.result.filter(item => item.user_id === profile.user.id).sort((a,b) => a.captured_at.localeCompare(b.captured_at)));
      request.onerror = () => reject(request.error);
    });
  }
  function panel(name) {
    document.querySelectorAll('[data-panel]').forEach(el => { el.hidden = el.dataset.panel !== name; });
    document.querySelectorAll('[data-tab]').forEach(el => el.setAttribute('aria-current', el.dataset.tab === name ? 'page':'false'));
    if (name !== 'camera') stopCamera();
    if (name === 'orders') renderOrders();
    if (name === 'ledger') loadLedger();
  }
  function lock(message) {
    identityReady = false;
    profile = null;
    currentOrder = null;
    captureContext = null;
    $('timezoneName').value = '';
    clearPreview();
    localStorage.removeItem(SESSION_KEY);
    stopCamera();
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    $('workspace').hidden = true;
    $('loginPanel').hidden = false;
    $('queueList').replaceChildren();
    $('ledgerList').replaceChildren();
    $('networkStatus').textContent = '请登录';
    notice(message, true);
  }
  async function bootstrap() {
    try {
      const response = await requestAPI('/api/field/session', {cache:'no-store'});
      if (response.status === 401 || response.status === 403) { lock('请使用有工单权限的员工账号登录。'); return; }
      if (!response.ok) throw new Error('服务器暂时不可用');
      const next = await response.json();
      if (profile && profile.user.id !== next.user.id) { stopCamera(); clearPreview(); currentOrder = null; captureContext = null; $('timezoneName').value = ''; $('ledgerList').replaceChildren(); }
      profile = next;
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(profile)); }
      catch (_) { notice('无法保存离线登录资料，请检查手机存储空间。',true); }
      $('networkStatus').textContent = '在线';
    } catch (error) {
      try { profile = JSON.parse(localStorage.getItem(SESSION_KEY)); } catch (_) { profile = null; }
      if (!profile) { lock('首次使用需要联网登录。'); return; }
      $('networkStatus').textContent = '离线暂存';
      notice('暂时无法连接系统。使用最近同步的工单，照片先保存在本机。');
    }
    identityReady = true;
    $('loginPanel').hidden = true;
    $('workspace').hidden = false;
    $('operatorName').textContent = profile.user.name;
    $('profileName').textContent = profile.user.name;
    $('versionText').textContent = '系统版本 V' + profile.version;
    if (!$('timezoneName').value) $('timezoneName').value = localStorage.getItem('field-timezone-' + profile.user.id) || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    $('createOrder').hidden = !profile.create_order_url;
    if (profile.create_order_url) $('createOrder').href = profile.create_order_url;
    $('openCamera').disabled = !profile.can_capture;
    $('fileCapture').disabled = !profile.can_capture;
    renderSelect();
    renderOrders();
    await renderQueue();
    locate().catch(() => {});
    if (watchId === null && navigator.geolocation) {
      watchId = navigator.geolocation.watchPosition(updatePosition, () => {}, {enableHighAccuracy:true,maximumAge:15000,timeout:15000});
    }
    syncQueue();
  }
  function renderSelect() {
    const selected = currentOrder?.id || new URLSearchParams(location.search).get('order_id') || localStorage.getItem('field-order-' + profile.user.id);
    $('orderSelect').replaceChildren(new Option('请选择工单',''));
    profile.orders.forEach(order => $('orderSelect').add(new Option(order.order_number + ' · ' + order.client_name, String(order.id))));
    $('orderSelect').value = String(selected || '');
    chooseOrder($('orderSelect').value);
  }
  function chooseOrder(id) {
    currentOrder = profile?.orders.find(order => String(order.id) === String(id)) || null;
    $('orderSelect').value = currentOrder ? String(currentOrder.id) : '';
    $('orderContext').textContent = currentOrder ? [currentOrder.customer_name,currentOrder.site_address].filter(Boolean).join(' · ') : '请先选择照片所属工单。';
    if (profile) localStorage.setItem('field-order-' + profile.user.id, String(currentOrder?.id || ''));
    locationNote = '';
    lastWarning = 0;
    farSamples = 0;
  }
  function distance(order) {
    if (!position || order.latitude === null || order.longitude === null) return null;
    const rad = x => x * Math.PI / 180;
    const a = Math.sin(rad(order.latitude-position.latitude)/2)**2 + Math.cos(rad(position.latitude))*Math.cos(rad(order.latitude))*Math.sin(rad(order.longitude-position.longitude)/2)**2;
    return 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0,1-a)));
  }
  function sortedOrders() { return [...(profile?.orders || [])].sort((a,b) => (distance(a) ?? Infinity) - (distance(b) ?? Infinity)); }
  function orderButton(order, callback) {
    const button = textNode('button', '', 'order-card'); button.type = 'button';
    button.append(textNode('strong', order.order_number + ' · ' + order.client_name));
    const d = distance(order);
    button.append(textNode('span', [order.customer_name, d === null ? '站点坐标未设置' : (d/1000).toFixed(2)+' km', order.site_address].filter(Boolean).join(' · ')));
    button.addEventListener('click', () => callback(order)); return button;
  }
  function renderOrders() {
    const search = $('orderSearch').value.trim().toLowerCase();
    $('orderList').replaceChildren();
    sortedOrders().filter(order => [order.order_number,order.client_name,order.customer_name].join(' ').toLowerCase().includes(search)).forEach(order => {
      $('orderList').append(orderButton(order, item => { chooseOrder(item.id); panel('camera'); }));
    });
    if (!$('orderList').children.length) $('orderList').append(textNode('p','没有符合条件的工单，请新建或申请。','muted'));
  }
  function updatePosition(result) {
    position = {latitude:result.coords.latitude,longitude:result.coords.longitude,accuracy:result.coords.accuracy,timestamp:result.timestamp};
    $('locationStatus').textContent = `${position.latitude.toFixed(5)}, ${position.longitude.toFixed(5)} · ±${Math.round(position.accuracy)}m`;
    if (identityReady && currentOrder && !taking && !document.hidden && Date.now()-lastWarning > 120000 && position.accuracy <= 100) {
      const d = distance(currentOrder);
      farSamples = d !== null && d - position.accuracy > profile.distance_limit ? farSamples + 1 : 0;
      if (farSamples >= 2) checkLocation(false).catch(() => {});
    }
  }
  async function locate() {
    if (!navigator.geolocation) { notice('设备不支持定位。',true); throw new Error('定位不可用'); }
    $('locationStatus').textContent = '正在检查位置…';
    return new Promise((resolve,reject) => navigator.geolocation.getCurrentPosition(result => {
      updatePosition(result); resolve(position);
    }, () => { position = null; $('locationStatus').textContent = '定位失败，请允许定位后重试'; reject(new Error('请允许定位，并重新检查位置。')); }, {enableHighAccuracy:true,timeout:15000,maximumAge:0}));
  }
  async function checkLocation(forCapture) {
    if (!currentOrder) { panel('orders'); return false; }
    if (!position || Date.now()-position.timestamp > 30000) await locate();
    if ($('locationDialog').open) return false;
    const d = distance(currentOrder);
    const warning = position.accuracy > 100 ? `当前定位精度较低（±${Math.round(position.accuracy)}米），请确认现场。` : d === null ? '当前工单的站点还没有坐标，请确认工单。' : d-position.accuracy > profile.distance_limit ? `当前位置距离当前工单站点约 ${(d/1000).toFixed(2)} 公里，是否需要切换工单？` : '';
    if (!warning) { locationNote = ''; return true; }
    if (!forCapture && (position.accuracy > 100 || d === null)) return false;
    lastWarning = Date.now();
    $('locationWarning').textContent = warning;
    $('locationReason').value = '';
    $('nearbyOrders').replaceChildren();
    sortedOrders().filter(order => order.id !== currentOrder.id).slice(0,3).forEach(order => $('nearbyOrders').append(orderButton(order, item => {
      finishWarning(false); chooseOrder(item.id); notice('已切换工单，请确认后重新拍照。');
    })));
    $('locationDialog').showModal();
    return new Promise(resolve => { warningResolve = resolve; });
  }
  function finishWarning(result) { $('locationDialog').close(); const resolve = warningResolve; warningResolve = null; resolve?.(result); }
  $('keepOrder').addEventListener('click', () => { const reason = $('locationReason').value.trim(); if (!reason) { $('locationReason').focus(); $('locationReason').setCustomValidity('请填写确认原因'); $('locationReason').reportValidity(); return; } locationNote = $('locationWarning').textContent + ' ' + reason; finishWarning(true); });
  $('locationReason').addEventListener('input', () => $('locationReason').setCustomValidity(''));
  $('switchOrder').addEventListener('click', () => { finishWarning(false); panel('orders'); });
  $('cancelLocation').addEventListener('click', () => finishWarning(false));
  $('locationDialog').addEventListener('cancel', event => { event.preventDefault(); finishWarning(false); });

  function clearPreview() {
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
    $('photoPreview').removeAttribute('src');
    $('photoPreview').hidden = true;
  }
  function stopCamera() {
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    $('viewfinder').srcObject = null; $('viewfinder').hidden = true;
    $('openCamera').hidden = false; $('takePhoto').hidden = true; $('closeCamera').hidden = true;
    $('cameraPlaceholder').hidden = Boolean(previewURL);
    $('photoPreview').hidden = !previewURL;
  }
  async function openCamera() {
    if (!identityReady || !profile?.can_capture) return;
    if (!currentOrder) { panel('orders'); notice('先选择工单。'); return; }
    try {
      stream?.getTracks().forEach(track => track.stop());
      stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1440}},audio:false});
      $('viewfinder').srcObject = stream; $('viewfinder').hidden = false; await $('viewfinder').play();
      $('photoPreview').hidden = true; $('cameraPlaceholder').hidden = true;
      $('openCamera').hidden = true; $('takePhoto').hidden = false; $('closeCamera').hidden = false;
    } catch (error) { notice('无法打开实时相机，可使用下方“系统相机 / 选择照片”。请检查相机权限。',true); stopCamera(); }
  }
  async function makeContext(source) {
    if (!identityReady || !profile?.can_capture || !currentOrder) throw new Error('请登录并选择工单。');
    // Refresh before every capture. The snapshot will not change while uploading.
    await locate();
    if (!(await checkLocation(true))) return null;
    const timezoneName = $('timezoneName').value.trim() || 'UTC';
    new Intl.DateTimeFormat('en', {timeZone:timezoneName}).format();
    localStorage.setItem('field-timezone-' + profile.user.id, timezoneName);
    return {client_id:key(),user_id:profile.user.id,employee_name:profile.user.name,order_id:currentOrder.id,order_number:currentOrder.order_number,site_name:currentOrder.client_name,
      captured_at:new Date().toISOString(),timezone_name:timezoneName,latitude:position.latitude,longitude:position.longitude,accuracy:position.accuracy,
      note:$('photoNote').value.trim(),location_note:locationNote,source,error:''};
  }
  async function watermarked(source, context) {
    const width = source.videoWidth || source.naturalWidth || source.width;
    const height = source.videoHeight || source.naturalHeight || source.height;
    if (!width || !height) throw new Error('相机尚未准备好，请重试。');
    const scale = Math.min(1,1800/Math.max(width,height));
    const canvas = document.createElement('canvas'); canvas.width = Math.round(width*scale); canvas.height = Math.round(height*scale);
    const ctx = canvas.getContext('2d'); ctx.drawImage(source,0,0,canvas.width,canvas.height);
    const size = Math.max(12,Math.round(canvas.width*.024));
    const time = new Intl.DateTimeFormat('zh-CN',{timeZone:context.timezone_name,dateStyle:'short',timeStyle:'medium',hour12:false}).format(new Date(context.captured_at));
    const lines = ['PRASINOS POWER · '+context.order_number, context.site_name+' · '+context.employee_name,
      time+' · '+context.timezone_name,`${context.latitude.toFixed(6)}, ${context.longitude.toFixed(6)} ±${Math.round(context.accuracy)}m`,
      context.source === 'camera' ? '现场相机 · 设备时间' : '系统相机 / 选图 · 本次记录时间'];
    if (context.note) lines.push(context.note.slice(0,80));
    const pad = size*.7, lineHeight = size*1.4, box = lines.length*lineHeight+pad*2;
    ctx.fillStyle = 'rgba(0,0,0,.70)'; ctx.fillRect(0,canvas.height-box,canvas.width,box);
    ctx.font = `600 ${size}px sans-serif`; ctx.fillStyle = 'white';
    lines.forEach((line,i) => ctx.fillText(line,pad,canvas.height-box+pad+lineHeight*(i+.8),canvas.width-pad*2));
    return new Promise((resolve,reject) => canvas.toBlob(blob => blob ? resolve(blob):reject(new Error('照片压缩失败')),'image/jpeg',.82));
  }
  async function keepCapture(source, context) {
    if (!context || context.user_id !== profile?.user.id || !identityReady) throw new Error('账号已改变，请重新拍照。');
    const photo = {...context,blob:await watermarked(source,context)};
    // Do not report success or clear the capture until the IDB transaction commits.
    await storePhoto(photo);
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = URL.createObjectURL(photo.blob); $('photoPreview').src = previewURL;
    if (!stream) { $('photoPreview').hidden = false; $('cameraPlaceholder').hidden = true; }
    notice('照片已保存在本机，正在尝试上传。');
    await renderQueue(); syncQueue();
  }
  $('takePhoto').addEventListener('click', async () => {
    if (taking) return; taking = true; $('takePhoto').disabled = true;
    try { const context = await makeContext('camera'); if (context) await keepCapture($('viewfinder'),context); }
    catch (error) { notice('照片未保存：'+error.message,true); }
    finally { taking = false; $('takePhoto').disabled = false; }
  });
  $('fileCapture').addEventListener('click', async () => {
    if (taking) return; taking = true;
    try { captureContext = await makeContext('file'); if (captureContext) $('photoFile').click(); }
    catch (error) { notice(error.message,true); }
    finally { taking = false; }
  });
  $('photoFile').addEventListener('change', async () => {
    const file = $('photoFile').files[0], context = captureContext; captureContext = null;
    if (!file || !context) return;
    const url = URL.createObjectURL(file);
    try { const image = new Image(); image.src = url; await image.decode(); context.captured_at = new Date().toISOString(); await keepCapture(image,context); }
    catch(error) { notice('照片未保存：'+error.message+'。请保留原照片后重试。',true); }
    finally { URL.revokeObjectURL(url); $('photoFile').value = ''; }
  });
  async function renderQueue() {
    try {
      const photos = await queued(); $('queueCount').textContent = String(photos.length); $('queueList').replaceChildren();
      photos.forEach(photo => {
        const row = textNode('div','','queue-item'); row.append(textNode('strong',photo.order_number+' · '+photo.site_name),textNode('small',photo.captured_at),textNode('span',photo.error || '等待上传'));
        const save = textNode('button','保存备份到手机'); save.type = 'button'; save.addEventListener('click', () => {
          const href = URL.createObjectURL(photo.blob), link = document.createElement('a'); link.href = href; link.download = photo.order_number+'-'+photo.client_id+'.jpg'; link.click(); setTimeout(() => URL.revokeObjectURL(href),10000);
        }); row.append(save); $('queueList').append(row);
      });
      if (!photos.length) $('queueList').append(textNode('p','没有待上传照片。','muted'));
    } catch(error) { notice('无法读取本机照片存储：'+error.message,true); }
  }
  async function syncQueue() {
    if (syncing || !navigator.onLine || !profile || !identityReady) return;
    syncing = true;
    try {
      const sessionResponse = await requestAPI('/api/field/session',{cache:'no-store'});
      if (sessionResponse.status === 401 || sessionResponse.status === 403) { lock('登录已过期或权限已改变。照片仍留在本机，请重新登录拍摄账号。'); return; }
      if (!sessionResponse.ok) return;
      const live = await sessionResponse.json();
      if (live.user.id !== profile.user.id) { lock('账号已改变，已暂停原账号的照片上传。'); return; }
      profile.csrf = live.csrf;
      $('networkStatus').textContent = '在线';
      for (const photo of await queued()) {
        if (!identityReady || photo.user_id !== profile?.user.id) break;
        try {
          const data = new FormData();
          Object.entries(photo).forEach(([k,v]) => { if (k !== 'blob' && k !== 'error') data.append(k,String(v)); });
          data.append('photo',photo.blob,photo.client_id+'.jpg');
          const response = await requestAPI('/api/field/photos',{method:'POST',headers:{'X-Field-Token':profile.csrf},body:data});
          if (response.status === 401) { lock('登录已过期，照片仍留在本机。请重新登录后补传。'); break; }
          const result = await response.json().catch(() => ({}));
          if (!response.ok || !result.ok) throw new Error(result.error || '上传未成功（'+response.status+'），照片仍保留');
          await storePhoto(photo,true);
          notice('照片已上传到 '+photo.order_number+'，系统已按拍摄日期归档。');
        } catch(error) {
          photo.error = error.message; await storePhoto(photo);
          if (!navigator.onLine || error instanceof TypeError) break;
        }
        await renderQueue();
      }
    } catch(error) { notice('暂时无法上传，照片仍保留在本机。'); }
    finally { syncing = false; await renderQueue(); }
  }
  async function loadLedger() {
    if (!identityReady) return;
    const params = new URLSearchParams(new FormData($('ledgerFilter')));
    $('exportLedger').href = '/api/field/photos.xlsx?'+params;
    try {
      const response = await requestAPI('/api/field/photos?'+params,{cache:'no-store'});
      if (response.status === 401 || response.status === 403) { lock('请重新登录后查询台账。'); return; }
      if (!response.ok) throw new Error('查询失败');
      const result = await response.json(); $('ledgerList').replaceChildren();
      $('ledgerSummary').textContent = `${result.rows.length} 张照片${result.truncated ? '，结果较多，请缩小日期范围':''}`;
      result.rows.forEach(photo => {
        const card = textNode('article','','photo-card'), link = document.createElement('a'), image = document.createElement('img');
        link.href = photo.preview; link.target = '_blank'; link.rel = 'noopener'; image.src = photo.thumbnail; image.alt = '工单照片'; image.loading = 'lazy'; link.append(image);
        const detail = document.createElement('div'); detail.append(textNode('strong',photo.order_number+' · '+photo.site_name),textNode('p',photo.employee_name+' · '+photo.capture_date+' · '+photo.timezone_name),textNode('p',photo.note),textNode('p',photo.source === 'camera' ? '现场相机':'系统相机 / 选图'));
        card.append(link,detail); $('ledgerList').append(card);
      });
    } catch(error) { $('ledgerSummary').textContent = '台账需要联网查看。待上传照片请到“拍照”页面查看。'; }
  }
  $('requestOrder').addEventListener('click', () => {
    if (!navigator.onLine) { notice('新建工单申请需要联网。',true); return; }
    $('requestDetail').value = position ? `现场坐标：${position.latitude}, ${position.longitude}\n站点：\n工作说明：` : '';
    $('requestDialog').showModal();
  });
  $('requestForm').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try { const response = await requestAPI('/api/field/order-request',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf},body:JSON.stringify({detail:$('requestDetail').value})}); if (!response.ok) throw new Error('提交失败，请确认登录和网络后重试。'); $('requestDialog').close(); notice('申请已发送给管理员和经理。'); }
    catch(error) { notice(error.message,true); } finally { button.disabled = false; }
  });
  $('cancelRequest').addEventListener('click', () => $('requestDialog').close());
  $('orderSelect').addEventListener('change', () => chooseOrder($('orderSelect').value));
  $('orderSearch').addEventListener('input',renderOrders);
  $('refreshLocation').addEventListener('click', () => locate().catch(error => notice(error.message,true)));
  $('openCamera').addEventListener('click',openCamera); $('closeCamera').addEventListener('click',stopCamera);
  $('retryUpload').addEventListener('click',syncQueue); $('reloadOrders').addEventListener('click',bootstrap);
  $('refreshLedger').addEventListener('click',loadLedger);
  $('ledgerFilter').addEventListener('submit', event => { event.preventDefault(); loadLedger(); });
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => panel(button.dataset.tab)));
  $('fieldLogout').addEventListener('click', () => { localStorage.removeItem(SESSION_KEY); identityReady = false; stopCamera(); });
  window.addEventListener('storage', event => { if (event.key === SESSION_KEY && event.newValue === null) lock('账号已退出，请重新登录。'); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopCamera(); else if (!taking) bootstrap(); });
  window.addEventListener('online',bootstrap); window.addEventListener('offline', () => { $('networkStatus').textContent = '离线暂存'; });
  window.addEventListener('pagehide',stopCamera);
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; $('installApp').hidden = false; });
  $('installApp').addEventListener('click', async () => { await installPrompt?.prompt(); installPrompt = null; $('installApp').hidden = true; });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/field/sw.js').catch(() => notice('离线界面尚未准备好，请联网重新打开一次。',true));
  navigator.storage?.persist?.().catch(() => {});
  bootstrap().catch(error => notice('无法准备现场工作界面：'+error.message,true));
})();
