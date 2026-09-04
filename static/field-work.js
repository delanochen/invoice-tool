(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const SESSION_KEY = 'field-session';
  let profile = null, position = null, stream = null, syncing = false, taking = false;
  let currentOrder = null, watchId = null, warningResolve = null, lastWarning = 0, locationNote = '';
  let captureContext = null, previewURL = null, database = null, installPrompt = null;
  let identityReady = false;
  let farSamples = 0;
  let initialOrder = new URLSearchParams(location.search).get('order_id');
  let cameraSelection = null, bootstrapGeneration = 0;
  let deviceSession = null, scanTimer = null, detector = null;
  async function requestAPI(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.method === 'POST' ? 60000 : 15000);
    try { return await fetch(url, {...options, signal:controller.signal}); }
    finally { clearTimeout(timer); }
  }
  const notice = (text, error = false) => { $('notice').textContent = text; $('notice').classList.toggle('error', error); };
  const textNode = (tag, text, className = '') => { const el = document.createElement(tag); el.textContent = text; el.className = className; return el; };
  const key = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');

  function resetDevice() {
    deviceSession = null;
    $('equipmentNumber').closest('fieldset').classList.remove('locked');
    $('equipmentNumber').value = '';
    $('positionNumber').value = '';
    $('noEquipmentNumber').checked = false;
    $('equipmentNumber').disabled = false;
    $('deviceStatus').textContent = '新设备：请扫描或输入编号，然后确认。';
  }
  function confirmDevice() {
    const number = $('equipmentNumber').value.trim(), noNumber = $('noEquipmentNumber').checked;
    if (!number && !noNumber) { notice('请输入设备编号，或勾选“此设备没有编号”。', true); $('equipmentNumber').focus(); return; }
    deviceSession = {id:key(), equipment_number:noNumber ? '' : number, position_number:$('positionNumber').value.trim(), no_equipment_number:noNumber};
    $('equipmentNumber').closest('fieldset').classList.add('locked');
    $('equipmentNumber').value = deviceSession.equipment_number;
    $('equipmentNumber').disabled = noNumber;
    $('deviceStatus').textContent = `已锁定：${number || '无设备编号'}${deviceSession.position_number ? ' · 位置 '+deviceSession.position_number : ''}。后续照片沿用；换设备请点“下一台设备”。`;
    notice('设备已确认，可以连续拍摄。');
  }
  async function scanDevice() {
    if (!stream || deviceSession || !('BarcodeDetector' in window)) return;
    try {
      detector ||= new BarcodeDetector();
      const results = await detector.detect($('viewfinder'));
      const value = results.map(item => item.rawValue?.trim()).find(Boolean);
      if (value) { $('equipmentNumber').value = value.slice(0,200); $('deviceStatus').textContent = '自动识别到：'+value+'。请核对后确认。'; }
    } catch (_) {}
  }

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
    bootstrapGeneration++;
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
    const generation = ++bootstrapGeneration;
    try {
      const response = await requestAPI('/api/field/session', {cache:'no-store'});
      if (response.status === 401 || response.status === 403) { lock('请使用有工单权限的员工账号登录。'); return; }
      if (!response.ok) throw new Error('服务器暂时不可用');
      const next = await response.json();
      if (generation !== bootstrapGeneration) return;
      if (profile && profile.user.id !== next.user.id) { stopCamera(); clearPreview(); currentOrder = null; captureContext = null; $('timezoneName').value = ''; $('ledgerList').replaceChildren(); }
      profile = next;
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(profile)); }
      catch (_) { notice('无法保存离线登录资料，请检查手机存储空间。',true); }
      $('networkStatus').textContent = '在线';
    } catch (error) {
      if (generation !== bootstrapGeneration) return;
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
    // iOS native pickers may still be committing their input when visibility resumes.
    // Never reset the control underneath an open picker or an active camera session.
    if (document.activeElement === $('orderSelect') || cameraSelection || captureContext || taking) return;
    const selected = currentOrder?.id || initialOrder || localStorage.getItem('field-order-' + profile.user.id);
    // A launch link is only an initial selection, never a permanent override on reload.
    initialOrder = null;
    const url = new URL(location.href);
    if (url.searchParams.has('order_id')) { url.searchParams.delete('order_id'); history.replaceState(history.state, '', url); }
    const options = [['', '请选择工单'], ...profile.orders.map(order => [String(order.id), order.order_number + ' · ' + order.client_name])];
    const existing = Array.from($('orderSelect').options, option => [option.value, option.text]);
    // Foreground refresh must not rebuild a native phone picker while it is open.
    if (JSON.stringify(existing) !== JSON.stringify(options)) {
      $('orderSelect').replaceChildren(...options.map(([value, label]) => new Option(label, value)));
    }
    $('orderSelect').value = String(selected || '');
    chooseOrder($('orderSelect').value);
  }
  function chooseOrder(id) {
    if (cameraSelection || taking) { $('orderSelect').value = String(currentOrder?.id || ''); return; }
    const previous = currentOrder?.id;
    currentOrder = profile?.orders.find(order => String(order.id) === String(id)) || null;
    $('orderSelect').value = currentOrder ? String(currentOrder.id) : '';
    $('cameraOrder').textContent = currentOrder ? currentOrder.order_number + ' · ' + currentOrder.client_name : '';
    $('orderContext').textContent = currentOrder ? [currentOrder.customer_name,currentOrder.site_address].filter(Boolean).join(' · ') : '请先选择照片所属工单。';
    if (profile) localStorage.setItem('field-order-' + profile.user.id, String(currentOrder?.id || ''));
    if (previous !== currentOrder?.id) {
      locationNote = '';
      lastWarning = 0;
      farSamples = 0;
    }
  }
  function chosenOrder() {
    const ids = [$('orderSelect').value, currentOrder?.id, profile && localStorage.getItem('field-order-' + profile.user.id)];
    for (const id of ids) {
      const order = profile?.orders.find(item => String(item.id) === String(id));
      if (order) return order;
    }
    return null;
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
  async function checkLocation(forCapture, selected = chosenOrder()) {
    if (!selected) return false;
    if (!position || Date.now()-position.timestamp > 30000) await locate();
    const d = distance(selected);
    const warning = position.accuracy > 100 ? `当前定位精度较低（±${Math.round(position.accuracy)}米）。` : d === null ? '当前工单的站点还没有坐标。' : d-position.accuracy > profile.distance_limit ? `当前位置距离所选工单站点约 ${(d/1000).toFixed(2)} 公里。` : '';
    if (!warning) { locationNote = ''; return true; }
    lastWarning = Date.now();
    locationNote = warning + ' 系统保留员工明确选择的工单。';
    notice(warning + ' 照片仍保存到 ' + selected.order_number + '；如需更换，请先关闭相机。');
    return true;
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
    clearInterval(scanTimer); scanTimer = null;
    cameraSelection = null;
    document.body.classList.remove('camera-active');
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    $('viewfinder').srcObject = null; $('viewfinder').hidden = true;
    $('openCamera').hidden = false; $('takePhoto').hidden = true; $('closeCamera').hidden = true;
    $('cameraPlaceholder').hidden = Boolean(previewURL);
    $('photoPreview').hidden = !previewURL;
  }
  async function openCamera() {
    if (!identityReady || !profile?.can_capture) return;
    const selected = chosenOrder();
    if (!selected) { notice('请先在上方选择工单。',true); $('orderSelect').focus(); return; }
    try {
      currentOrder = selected;
      cameraSelection = {order:{...selected}, userId:profile.user.id};
      stream?.getTracks().forEach(track => track.stop());
      stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1440}},audio:false});
      $('viewfinder').srcObject = stream; $('viewfinder').hidden = false; await $('viewfinder').play();
      document.body.classList.add('camera-active');
      $('photoPreview').hidden = true; $('cameraPlaceholder').hidden = true;
      $('openCamera').hidden = true; $('takePhoto').hidden = false; $('closeCamera').hidden = false;
      scanDevice(); scanTimer = setInterval(scanDevice, 800);
    } catch (error) { notice('无法打开实时相机，可使用下方“系统相机 / 选择照片”。请检查相机权限。',true); stopCamera(); }
  }
  async function makeContext(source) {
    const resolved = chosenOrder();
    if (!identityReady || !profile?.can_capture || !resolved) throw new Error('请先在上方选择工单。');
    const selected = cameraSelection?.order || {...resolved};
    if (!deviceSession) throw new Error('请先确认设备编号，或明确选择“此设备没有编号”。');
    const selectedId = selected.id, selectedUser = cameraSelection?.userId || profile.user.id;
    // Refresh before every capture. The snapshot will not change while uploading.
    await locate();
    if (!(await checkLocation(true, selected))) return null;
    if (!identityReady || profile?.user.id !== selectedUser) throw new Error('账号已改变，请重新拍照。');
    const timezoneName = $('timezoneName').value.trim() || 'UTC';
    new Intl.DateTimeFormat('en', {timeZone:timezoneName}).format();
    localStorage.setItem('field-timezone-' + profile.user.id, timezoneName);
    return {client_id:key(),user_id:profile.user.id,employee_name:profile.user.name,order_id:selected.id,order_number:selected.order_number,site_name:selected.client_name,site_address:selected.site_address,
      captured_at:new Date().toISOString(),timezone_name:timezoneName,latitude:position.latitude,longitude:position.longitude,accuracy:position.accuracy,
      note:$('photoNote').value.trim(),location_note:locationNote,source,error:'',equipment_number:deviceSession.equipment_number,
      position_number:deviceSession.position_number,equipment_session:deviceSession.id,no_equipment_number:deviceSession.no_equipment_number};
  }
  async function watermarked(source, context) {
    const width = source.videoWidth || source.naturalWidth || source.width;
    const height = source.videoHeight || source.naturalHeight || source.height;
    if (!width || !height) throw new Error('相机尚未准备好，请重试。');
    const scale = Math.min(1,1800/Math.max(width,height));
    const canvas = document.createElement('canvas'); canvas.width = Math.round(width*scale); canvas.height = Math.round(height*scale);
    const ctx = canvas.getContext('2d'); ctx.drawImage(source,0,0,canvas.width,canvas.height);
    await window.PrasinosWatermark.draw(ctx,canvas.width,canvas.height,context);
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
    notice('照片已保存在本机：'+photo.order_number+'，正在尝试上传。');
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
  $('photoFile').addEventListener('cancel', () => { captureContext = null; });
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
        const detail = document.createElement('div'); detail.append(textNode('strong',photo.order_number+' · '+photo.site_name),textNode('p','设备：'+(photo.equipment_number || '无编号')+(photo.position_number ? ' · 位置：'+photo.position_number : '')),textNode('p',photo.employee_name+' · '+photo.capture_date+' · '+photo.timezone_name),textNode('p',photo.note),textNode('p',photo.source === 'camera' ? '现场相机':'系统相机 / 选图'));
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
  $('confirmDevice').addEventListener('click',confirmDevice);
  $('nextDevice').addEventListener('click', () => { stopCamera(); resetDevice(); $('equipmentNumber').focus(); });
  $('noEquipmentNumber').addEventListener('change', () => { $('equipmentNumber').disabled = $('noEquipmentNumber').checked; if ($('noEquipmentNumber').checked) $('equipmentNumber').value=''; deviceSession=null; });
  $('equipmentNumber').addEventListener('input', () => { deviceSession=null; $('deviceStatus').textContent='编号已修改，请重新确认。'; });
  $('positionNumber').addEventListener('input', () => { deviceSession=null; $('deviceStatus').textContent='位置号已修改，请重新确认。'; });
  $('orderSelect').addEventListener('input', () => chooseOrder($('orderSelect').value));
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
