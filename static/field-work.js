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
  let batch = null, timeAuthorized = false, draftSelection = null;
  async function requestAPI(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.method === 'POST' ? 60000 : 15000);
    try { return await fetch(url, {...options, signal:controller.signal}); }
    finally { clearTimeout(timer); }
  }
  const notice = (text, error = false) => { $('notice').textContent = text; $('notice').classList.toggle('error', error); };
  const textNode = (tag, text, className = '') => { const el = document.createElement(tag); el.textContent = text; el.className = className; return el; };
  const key = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
  const orderStorageKey = () => 'field-order-' + profile.user.id + '-' + new Date().toLocaleDateString('en-CA');

  function resetDevice() {
    deviceSession = null;
    $('equipmentNumber').closest('fieldset').classList.remove('locked');
    $('equipmentNumber').value = '';
    $('positionNumber').value = '';
    $('containerNumber').value = '';
    $('pumpFuseNumbers').value = '';
    $('noEquipmentNumber').checked = false;
    $('equipmentNumber').disabled = false;
    $('deviceStatus').textContent = '新设备：请扫描或输入编号，然后确认。';
  }
  function confirmDevice() {
    const number = $('equipmentNumber').value.trim(), noNumber = $('noEquipmentNumber').checked;
    if (!number && !noNumber) { notice('请输入设备编号，或勾选“此设备没有编号”。', true); $('equipmentNumber').focus(); return; }
    deviceSession = {id:key(), equipment_number:noNumber ? '' : number,
      position_number:$('positionNumber').value.trim(), container_number:$('containerNumber').value.trim(),
      pump_fuse_numbers:$('pumpFuseNumbers').value.trim(),
      no_equipment_number:noNumber};
    $('equipmentNumber').closest('fieldset').classList.add('locked');
    $('equipmentNumber').value = deviceSession.equipment_number;
    $('equipmentNumber').disabled = noNumber;
    $('deviceStatus').textContent = `已锁定：${number || '无铭牌号'}${deviceSession.position_number ? ' · 位置 '+deviceSession.position_number : ''}${deviceSession.container_number ? ' · 集装箱 '+deviceSession.container_number : ''}${deviceSession.pump_fuse_numbers ? ' · 水泵保险 '+deviceSession.pump_fuse_numbers : ''}。后续照片沿用；换设备请点“下一台设备”。`;
    notice('设备已确认，可以连续拍摄。');
  }
  function chooseKind(type) {
    if (batch && batch.type !== type) {
      queued(batch.id).then(items => { if (items.length) notice('当前组已有照片，请先完成上传或删除后再更换类型。',true); else startKind(type); });
    } else startKind(type);
  }
  function startKind(type) {
    const previousType = batch?.type;
    batch ||= {id:key(), type, actual_start:Date.now(), watermark_start:null};
    batch.type = type;
    $('deviceSession').hidden = type !== 'equipment';
    $('timeSettings').hidden = false;
    $('kindStatus').textContent = type === 'equipment' ? '设备照片：需确认 Machine Number。' : '非设备照片：不显示铭牌号、位置号和集装箱号。';
    $('equipmentKind').classList.toggle('primary',type==='equipment'); $('generalKind').classList.toggle('primary',type==='general');
    if (type === 'general') deviceSession = {id:batch.id,equipment_number:'',position_number:'',container_number:'',pump_fuse_numbers:'',no_equipment_number:true};
    else if (previousType !== 'equipment') resetDevice();
  }
  async function verifyTimePassword() {
    const response = await requestAPI('/api/field/verify-watermark-password',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf},body:JSON.stringify({password:$('watermarkPassword').value})});
    timeAuthorized = response.ok;
    $('watermarkStart').disabled = !timeAuthorized;
    $('timeStatus').textContent = timeAuthorized ? '密码正确，可以调整本组水印时间。' : '密码错误。';
    if (!timeAuthorized) notice('水印时间调整密码错误。',true);
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
  async function recognizeDevice() {
    if (!stream || !$('viewfinder').videoWidth) { await openCamera(true); if (!stream) return; }
    const button = $('recognizeDevice'); button.disabled = true;
    try {
      const video = $('viewfinder'), scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
      const canvas = document.createElement('canvas'); canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('无法读取相机画面')),'image/jpeg',.86));
      const data = new FormData(); data.append('photo',blob,'nameplate.jpg');
      notice('正在识别铭牌，请保持相机对准设备编号…');
      const response = await requestAPI('/api/field/recognize-equipment',{method:'POST',headers:{'X-Field-Token':profile.csrf},body:data});
      const result = await response.json().catch(()=>({}));
      if (!response.ok) throw new Error(result.error || '铭牌识别失败');
      if (!result.candidates?.length) throw new Error('没有识别到 Machine Number，请靠近铭牌重试或手工输入。');
      $('equipmentNumber').disabled = false; $('noEquipmentNumber').checked = false;
      $('equipmentNumber').value = result.candidates[0]; deviceSession = null;
      $('deviceStatus').textContent = '识别到：'+result.candidates.join('、')+'。请核对设备编号后点击确认。';
      notice('已识别设备编号 '+result.candidates[0]+'，请核对后确认。');
    } catch(error) { notice(error.message,true); }
    finally { button.disabled = false; }
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
  async function queued(batchId = null) {
    if (!profile || !identityReady) return [];
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const request = db.transaction('photos').objectStore('photos').getAll();
      request.onsuccess = () => resolve(request.result.filter(item => item.user_id === profile.user.id && (!batchId || item.batch_id === batchId)).sort((a,b) => a.captured_at.localeCompare(b.captured_at)));
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
    const technicians = profile.technicians?.length ? profile.technicians : [profile.user];
    profile.technicians = technicians;
    const technicianId = $('technicianSelect').value || String(profile.user.id);
    $('technicianSelect').replaceChildren(...technicians.map(person => new Option(person.name, String(person.id))));
    $('technicianSelect').value = technicians.some(person => String(person.id) === technicianId)
      ? technicianId : String(profile.user.id);
    $('versionText').textContent = '系统版本 V' + profile.version;
    if (!$('timezoneName').value) $('timezoneName').value = localStorage.getItem('field-timezone-' + profile.user.id) || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    $('createOrder').hidden = !profile.create_order_url;
    if (profile.create_order_url) $('createOrder').href = profile.create_order_url;
    $('openCamera').disabled = !profile.can_capture;
    $('fileCapture').classList.toggle('disabled', !profile.can_capture);
    $('fileCapture').setAttribute('aria-disabled', profile.can_capture ? 'false' : 'true');
    renderSelect();
    renderOrders();
    await renderQueue();
    locate().catch(() => {});
    if (watchId === null && navigator.geolocation) {
      watchId = navigator.geolocation.watchPosition(updatePosition, () => {}, {enableHighAccuracy:true,maximumAge:15000,timeout:15000});
    }
  }
  function renderSelect() {
    // iOS native pickers may still be committing their input when visibility resumes.
    // Never reset the control underneath an open picker or an active camera session.
    if (document.activeElement === $('orderSelect') || cameraSelection || captureContext || taking) return;
    const selected = currentOrder?.id || initialOrder || localStorage.getItem(orderStorageKey());
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
    if (profile) localStorage.setItem(orderStorageKey(), String(currentOrder?.id || ''));
    if (previous !== currentOrder?.id) {
      locationNote = '';
      lastWarning = 0;
      farSamples = 0;
    }
  }
  function chosenOrder() {
    const ids = [$('orderSelect').value, currentOrder?.id, profile && localStorage.getItem(orderStorageKey())];
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
  async function openCamera(recognitionOnly = false) {
    if (!identityReady || !profile?.can_capture) return;
    if (!batch) { notice('请先选择“设备照片”或“非设备照片”。',true); return; }
    if (!$('systemTime').checked && !timeAuthorized) { notice('请先验证水印时间调整密码。',true); return; }
    if (batch.type === 'equipment' && !deviceSession && !recognitionOnly) { notice('请先识别或输入 Machine Number 并确认。',true); return; }
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
  async function makeContext(source, lockedSelection = null) {
    const resolved = chosenOrder();
    if (!identityReady || !profile?.can_capture || !resolved) throw new Error('请先在上方选择工单。');
    const selected = lockedSelection?.order || cameraSelection?.order || {...resolved};
    if (!batch || !deviceSession) throw new Error('请先确认本组照片类型和设备信息。');
    const selectedId = selected.id, selectedUser = lockedSelection?.userId || cameraSelection?.userId || profile.user.id;
    // Refresh before every capture. The snapshot will not change while uploading.
    await locate();
    if (!(await checkLocation(true, selected))) return null;
    if (!identityReady || profile?.user.id !== selectedUser) throw new Error('账号已改变，请重新拍照。');
    const timezoneName = $('timezoneName').value.trim() || 'UTC';
    new Intl.DateTimeFormat('en', {timeZone:timezoneName}).format();
    localStorage.setItem('field-timezone-' + profile.user.id, timezoneName);
    const actual = new Date();
    const watermark = $('systemTime').checked ? actual : new Date(new Date($('watermarkStart').value).getTime() + (actual.getTime() - batch.actual_start));
    if (Number.isNaN(watermark.getTime())) throw new Error('请选择有效的水印开始时间。');
    const technician = profile.technicians.find(person => String(person.id) === $('technicianSelect').value);
    if (!technician) throw new Error('请选择施工员。');
    return {client_id:key(),user_id:profile.user.id,employee_name:technician.name,technician_user_id:technician.id,
      order_id:selected.id,order_number:selected.order_number,site_name:selected.client_name,site_address:selected.site_address,
      captured_at:actual.toISOString(),watermark_at:watermark.toISOString(),batch_id:batch.id,photo_type:batch.type,timezone_name:timezoneName,latitude:position.latitude,longitude:position.longitude,accuracy:position.accuracy,
      note:$('photoNote').value.trim(),location_note:locationNote,source,error:'',equipment_number:deviceSession.equipment_number,
      position_number:deviceSession.position_number,container_number:deviceSession.container_number,
      pump_fuse_numbers:deviceSession.pump_fuse_numbers,
      equipment_session:deviceSession.id,no_equipment_number:deviceSession.no_equipment_number};
  }
  async function processedPhoto(source, context) {
    const width = source.videoWidth || source.naturalWidth || source.width;
    const height = source.videoHeight || source.naturalHeight || source.height;
    if (!width || !height) throw new Error('相机尚未准备好，请重试。');
    const scale = Math.min(1,1800/Math.max(width,height));
    const canvas = document.createElement('canvas'); canvas.width = Math.round(width*scale); canvas.height = Math.round(height*scale);
    const ctx = canvas.getContext('2d'); ctx.drawImage(source,0,0,canvas.width,canvas.height);
    if (context.watermark_source !== 'original') await window.PrasinosWatermark.draw(ctx,canvas.width,canvas.height,context);
    return new Promise((resolve,reject) => canvas.toBlob(blob => blob ? resolve(blob):reject(new Error('照片压缩失败')),'image/jpeg',.82));
  }
  async function keepCapture(source, context) {
    if (!context || context.user_id !== profile?.user.id || !identityReady) throw new Error('账号已改变，请重新拍照。');
    const photo = {...context,blob:await processedPhoto(source,context)};
    // Do not report success or clear the capture until the IDB transaction commits.
    await storePhoto(photo);
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = URL.createObjectURL(photo.blob); $('photoPreview').src = previewURL;
    if (!stream) { $('photoPreview').hidden = false; $('cameraPlaceholder').hidden = true; }
    notice('照片已保存为本机草稿：'+photo.order_number+'。完成本组后再统一上传。');
    await renderQueue();
  }
  $('takePhoto').addEventListener('click', async () => {
    if (taking) return; taking = true; $('takePhoto').disabled = true;
    try { const context = await makeContext('camera'); if (context) { context.watermark_source = 'system'; await keepCapture($('viewfinder'),context); } }
    catch (error) { notice('照片未保存：'+error.message,true); }
    finally { taking = false; $('takePhoto').disabled = false; }
  });
  $('photoFile').addEventListener('click', event => {
    const reject = message => { event.preventDefault(); captureContext = null; notice(message,true); };
    if (taking || !identityReady || !profile?.can_capture) { reject('照片功能尚未准备好，请稍后重试。'); return; }
    if (!batch) { reject('请先选择“设备照片”或“非设备照片”。'); return; }
    if (!$('existingWatermark').checked && !$('systemTime').checked && !timeAuthorized) { reject('请先验证水印时间调整密码。'); return; }
    if (!deviceSession) { reject('请先确认本组照片类型和设备信息。'); return; }
    const selected = chosenOrder();
    if (!selected) { reject('请先在上方选择工单。'); $('orderSelect').focus(); return; }
    // This handler runs on the native file input itself. iPhone/PWA therefore
    // receives a direct trusted user gesture instead of a scripted input click.
    captureContext = {order:{...selected}, userId:profile.user.id, watermarkSource:$('existingWatermark').checked ? 'original' : 'system'};
  });
  $('photoFile').addEventListener('change', async () => {
    const files = Array.from($('photoFile').files || []), selection = captureContext; captureContext = null;
    if (!files.length || !selection) return;
    taking = true;
    try {
      const baseContext = await makeContext('file', selection);
      if (!baseContext) return;
      baseContext.watermark_source = selection.watermarkSource;
      let saved = 0;
      for (const file of files) {
        const url = URL.createObjectURL(file);
        try {
          const image = new Image(); image.src = url; await image.decode();
          const context = {...baseContext, client_id:key(), captured_at:new Date().toISOString()};
          await keepCapture(image,context); saved++;
        } catch(error) { notice(`照片 ${file.name || saved + 1} 未保存：${error.message}。`,true); }
        finally { URL.revokeObjectURL(url); }
      }
      if (saved) notice(`已保存 ${saved} 张本机草稿，完成本组后统一上传。`);
    }
    catch(error) { notice('照片未保存：'+error.message+'。请保留原照片后重试。',true); }
    finally { taking = false; $('photoFile').value = ''; }
  });
  $('photoFile').addEventListener('cancel', () => { captureContext = null; });
  async function renderQueue() {
    try {
      const photos = await queued(); $('queueCount').textContent = String(photos.length); $('queueList').replaceChildren();
      $('draftCard').classList.toggle('has-drafts',photos.length > 0);
      photos.forEach(photo => {
        const row = textNode('div','','queue-item draft-item');
        const thumb=document.createElement('img'); const thumbURL=URL.createObjectURL(photo.blob); thumb.src=thumbURL; thumb.alt='草稿照片'; thumb.onload=()=>URL.revokeObjectURL(thumbURL);
        thumb.addEventListener('click',()=>openDraft(photo));
        row.append(thumb,textNode('strong',(photo.photo_type==='equipment' ? (photo.equipment_number||'N/A')+' · ' : '')+photo.order_number),textNode('small',new Date(photo.captured_at).toLocaleString()),textNode('span',photo.watermark_source === 'original' ? '保留原图水印' : '系统生成水印'),textNode('span',photo.error || '本机草稿'));
        const save = textNode('button','保存备份到手机'); save.type = 'button'; save.addEventListener('click', () => {
          const href = URL.createObjectURL(photo.blob), link = document.createElement('a'); link.href = href; link.download = photo.order_number+'-'+photo.client_id+'.jpg'; link.click(); setTimeout(() => URL.revokeObjectURL(href),10000);
        }); row.append(save); $('queueList').append(row);
      });
      if (!photos.length) $('queueList').append(textNode('p','没有待上传照片。','muted'));
      $('completeBatch').hidden = !(batch && photos.some(photo=>photo.batch_id===batch.id));
    } catch(error) { notice('无法读取本机照片存储：'+error.message,true); }
  }
  function openDraft(photo) {
    draftSelection=photo; if (previewURL) URL.revokeObjectURL(previewURL); previewURL=URL.createObjectURL(photo.blob);
    $('draftLarge').src=previewURL; $('draftDetail').textContent=(photo.equipment_number||'非设备照片')+' · '+new Date(photo.captured_at).toLocaleString()+' · '+(photo.watermark_source === 'original' ? '保留原图水印' : '系统生成水印'); $('draftDialog').showModal();
  }
  async function syncQueue(batchId = null) {
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
      for (const photo of await queued(batchId)) {
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
  async function completeBatch() {
    if (!batch) return;
    const id=batch.id; await syncQueue(id);
    if ((await queued(id)).length) { notice('部分照片尚未上传，请检查网络后重试。',true); return; }
    stopCamera(); batch=null; deviceSession=null; timeAuthorized=false; $('timeSettings').hidden=true; $('deviceSession').hidden=true; $('systemTime').checked=true; $('adjustedTimeFields').hidden=true; $('watermarkPassword').value='';
    $('equipmentKind').classList.remove('primary'); $('generalKind').classList.remove('primary'); $('kindStatus').textContent='请选择下一组照片类型。'; $('photoNote').value=''; $('existingWatermark').checked=false;
    notice('本组照片已全部上传，请选择下一组照片类型。'); await renderQueue();
  }
  async function loadLedger() {
    if (!identityReady) return;
    const params = new URLSearchParams(new FormData($('ledgerFilter')));
    $('exportLedger').href = '/api/field/photos.xlsx?'+params;
    $('repairReport').href = '/reports/field-repairs?'+params;
    try {
      const response = await requestAPI('/api/field/photos?'+params,{cache:'no-store'});
      if (response.status === 401 || response.status === 403) { lock('请重新登录后查询台账。'); return; }
      if (!response.ok) throw new Error('查询失败');
      const result = await response.json(); $('ledgerList').replaceChildren();
      $('ledgerSummary').textContent = `${result.rows.length} 张照片${result.truncated ? '，结果较多，请缩小日期范围':''}`;
      result.rows.forEach(photo => {
        const card = textNode('article','','photo-card'), link = document.createElement('a'), image = document.createElement('img');
        link.href = photo.preview; link.target = '_blank'; link.rel = 'noopener'; image.src = photo.thumbnail; image.alt = '工单照片'; image.loading = 'lazy'; link.append(image);
        const detail = document.createElement('div');
        const map=document.createElement('a'); map.href=`https://www.google.com/maps?q=${photo.latitude},${photo.longitude}`; map.target='_blank'; map.rel='noopener'; map.textContent=`坐标：${Number(photo.latitude).toFixed(5)}, ${Number(photo.longitude).toFixed(5)}`;
        const address=document.createElement('a'); address.href=map.href; address.target='_blank'; address.rel='noopener'; address.textContent='现场地址：'+(photo.site_address||photo.site_name);
        const deviceDetail = ['铭牌号：'+(photo.equipment_number || '无'), photo.position_number ? '位置号：'+photo.position_number : '', photo.container_number ? '集装箱号：'+photo.container_number : '', photo.pump_fuse_numbers ? '水泵保险：'+photo.pump_fuse_numbers : ''].filter(Boolean).join(' · ');
        const watermarkDetail = photo.watermark_source === 'original' ? '水印：保留原图水印' : '水印：系统生成 · '+(photo.watermark_at||photo.captured_at);
        detail.append(textNode('strong',photo.order_number+' · '+photo.site_name),textNode('p',deviceDetail),textNode('p','拍摄：'+photo.captured_at+' · '+watermarkDetail),textNode('p','施工员：'+(photo.technician_name||photo.employee_name)+' · 实际拍摄：'+photo.employee_name+' · 接收：'+photo.received_at),textNode('p',photo.note),address,textNode('br',''),map,textNode('p',photo.source === 'camera' ? '现场相机':'系统相机 / 选图'));
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
  $('recognizeDevice').addEventListener('click',recognizeDevice);
  $('nextDevice').addEventListener('click', async () => { if(batch && (await queued(batch.id)).length){notice('请先完成上传或删除当前组照片，再进入下一台设备。',true);return;} stopCamera(); resetDevice(); $('equipmentNumber').focus(); });
  $('noEquipmentNumber').addEventListener('change', () => { $('equipmentNumber').disabled = $('noEquipmentNumber').checked; if ($('noEquipmentNumber').checked) $('equipmentNumber').value=''; deviceSession=null; });
  $('equipmentNumber').addEventListener('input', () => { deviceSession=null; $('deviceStatus').textContent='编号已修改，请重新确认。'; });
  $('positionNumber').addEventListener('input', () => { deviceSession=null; $('deviceStatus').textContent='位置号已修改，请重新确认。'; });
  $('containerNumber').addEventListener('input', () => { deviceSession=null; $('deviceStatus').textContent='集装箱号已修改，请重新确认。'; });
  $('pumpFuseNumbers').addEventListener('input', () => { deviceSession=null; $('deviceStatus').textContent='水泵保险编号已修改，请重新确认。'; });
  $('orderSelect').addEventListener('input', () => chooseOrder($('orderSelect').value));
  $('orderSelect').addEventListener('change', () => chooseOrder($('orderSelect').value));
  $('orderSearch').addEventListener('input',renderOrders);
  $('refreshLocation').addEventListener('click', () => locate().catch(error => notice(error.message,true)));
  $('openCamera').addEventListener('click',()=>openCamera(false)); $('closeCamera').addEventListener('click',stopCamera);
  $('retryUpload').addEventListener('click',()=>syncQueue()); $('reloadOrders').addEventListener('click',bootstrap);
  $('equipmentKind').addEventListener('click',()=>chooseKind('equipment')); $('generalKind').addEventListener('click',()=>chooseKind('general'));
  $('systemTime').addEventListener('change',()=>{ const adjusted=!$('systemTime').checked; $('adjustedTimeFields').hidden=!adjusted; timeAuthorized=!adjusted; $('watermarkStart').disabled=adjusted; if(adjusted&&!$('watermarkStart').value){const d=new Date();d.setMinutes(d.getMinutes()-d.getTimezoneOffset());$('watermarkStart').value=d.toISOString().slice(0,16);} $('timeStatus').textContent=adjusted?'请输入密码并设置水印开始时间。':'使用当前系统时间。'; });
  $('verifyTimePassword').addEventListener('click',verifyTimePassword);
  $('completeBatch').addEventListener('click',completeBatch);
  $('closeDraft').addEventListener('click',()=>$('draftDialog').close());
  $('deleteDraft').addEventListener('click',async()=>{if(!draftSelection)return;await storePhoto(draftSelection,true);draftSelection=null;$('draftDialog').close();await renderQueue();notice('已删除本机草稿照片。');});
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
