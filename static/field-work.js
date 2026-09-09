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
  let deviceSession = null, scanTimer = null, ocrTimer = null, ocrStartTimer = null, ocrBusy = false, detector = null;
  let batch = null, timeAuthorized = false, draftSelection = null;
  let ledgerPhotos = [], ledgerPhotoIndex = 0, ledgerTouchStart = null;
  let captureMode = 'camera', deviceStatusText = '';
  let deletingDrafts = false, deleteDraftSnapshot = null;
  const queueViews = new Map();
  let queueRenderGeneration = 0, uploadingPhotoId = null, uploadIndex = 0, uploadTotal = 0;
  let processingFiles = false, processingTotal = 0, processingDone = 0;
  const fieldText = value => window.fieldTranslate ? window.fieldTranslate(value) : value;
  async function requestAPI(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.method === 'POST' ? 60000 : 15000);
    try {
      if (options.body instanceof FormData) {
        // Materialize multipart bytes once, including their matching boundary.
        // This avoids relying on a restored iOS File during fetch serialization.
        const encoded = new Response(options.body);
        const headers = new Headers(options.headers);
        headers.set('Content-Type', encoded.headers.get('Content-Type'));
        options = {...options, headers, body:await encoded.blob()};
      }
      return await fetch(url, {...options, signal:controller.signal});
    }
    finally { clearTimeout(timer); }
  }
  const notice = (text, error = false) => { $('notice').textContent = fieldText(text); $('notice').classList.toggle('error', error); };
  const textNode = (tag, text, className = '') => { const el = document.createElement(tag); el.textContent = fieldText(text); el.className = className; return el; };
  const setFieldText = (id, text) => {
    if (id === 'deviceStatus') {
      deviceStatusText = text;
      if (captureMode === 'upload') text = text.replaceAll('打开相机', '选择照片上传');
    }
    $(id).textContent = fieldText(text);
  };
  const key = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
  const orderStorageKey = () => 'field-order-' + profile.user.id + '-' + new Date().toLocaleDateString('en-CA');

  function setCaptureMode(mode) {
    if (taking || processingFiles || captureContext || syncing) return;
    stopCamera();
    captureMode = mode;
    if (deviceStatusText) setFieldText('deviceStatus', deviceStatusText);
    $('cameraView').hidden = mode !== 'camera';
    $('uploadControls').hidden = mode !== 'upload';
    $('captureDetails').hidden = mode !== 'camera';
    $('captureNote').hidden = mode !== 'camera';
    $('existingWatermark').checked = true;
    for (const value of ['camera', 'upload']) {
      $(value + 'Mode').classList.toggle('primary', value === mode);
      $(value + 'Mode').setAttribute('aria-pressed', String(value === mode));
    }
  }
  function resetDevice() {
    deviceSession = null;
    $('equipmentNumber').closest('fieldset').classList.remove('locked');
    $('equipmentNumber').value = '';
    $('positionNumber').value = '';
    $('containerNumber').value = '';
    $('noEquipmentNumber').checked = false;
    $('equipmentNumber').disabled = false;
    $('equipmentNumber').required = true;
    setFieldText('deviceStatus', '新设备：请扫描或输入编号，然后点击“打开相机”。');
  }
  function confirmDevice() {
    const number = $('equipmentNumber').value.trim(), noNumber = $('noEquipmentNumber').checked;
    if (!number && !noNumber) { notice('请输入设备编号，或勾选“此设备没有编号”。', true); $('equipmentNumber').focus(); return false; }
    deviceSession = {id:key(), equipment_number:noNumber ? '' : number,
      position_number:$('positionNumber').value.trim(), container_number:$('containerNumber').value.trim(),
      no_equipment_number:noNumber};
    $('equipmentNumber').closest('fieldset').classList.add('locked');
    $('equipmentNumber').value = deviceSession.equipment_number;
    $('equipmentNumber').disabled = noNumber;
    setFieldText('deviceStatus', `已锁定：${number || '无铭牌号'}${deviceSession.position_number ? ' · 位置 '+deviceSession.position_number : ''}${deviceSession.container_number ? ' · 集装箱 '+deviceSession.container_number : ''}。后续照片沿用；换设备请点“下一台设备”。`);
    return true;
  }
  function chooseKind(type) {
    if (batch?.type && batch.type !== type) {
      queued(batch.id).then(items => { if (items.length) notice('当前组已有照片，请先完成上传或删除后再更换类型。',true); else startKind(type); });
    } else startKind(type);
  }
  function startKind(type) {
    const previousType = batch?.type;
    batch ||= {id:key(), type, actual_start:Date.now(), watermark_start:null};
    batch.type = type;
    $(type === 'equipment' ? 'deviceNoteSlot' : 'generalNoteSlot').append($('captureNote'));
    $('deviceSession').hidden = type !== 'equipment';
    $('timeSettings').hidden = false;
    setFieldText('kindStatus', type === 'equipment' ? '设备照片：需确认 Machine Number。' : '非设备照片：不显示铭牌号、位置号和集装箱号。');
    $('equipmentKind').classList.toggle('primary',type==='equipment'); $('generalKind').classList.toggle('primary',type==='general');
    if (type === 'general') deviceSession = {id:batch.id,equipment_number:'',position_number:'',container_number:'',no_equipment_number:true};
    else if (previousType !== 'equipment') resetDevice();
    renderQueue();
  }
  async function verifyTimePassword() {
    const response = await requestAPI('/api/field/verify-watermark-password',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf},body:JSON.stringify({password:$('watermarkPassword').value})});
    timeAuthorized = response.ok;
    $('watermarkStart').disabled = !timeAuthorized;
    setFieldText('timeStatus', timeAuthorized ? '密码正确，可以调整本组水印时间。' : '密码错误。');
    if (!timeAuthorized) notice('水印时间调整密码错误。',true);
  }
  async function scanDevice() {
    if (!stream || deviceSession || !('BarcodeDetector' in window)) return;
    try {
      detector ||= new BarcodeDetector();
      const results = await detector.detect($('viewfinder'));
      const value = results.map(item => item.rawValue?.trim()).find(Boolean);
      if (value) { $('equipmentNumber').value = value.slice(0,200); setFieldText('deviceStatus', '自动识别到：'+value+'。请核对后点击“打开相机”。'); }
    } catch (_) {}
  }
  async function recognizeDevice(automatic = false) {
    if (automatic && (batch?.type !== 'equipment' || deviceSession || !document.body.classList.contains('recognition-mode'))) return;
    if (!stream || !$('viewfinder').videoWidth) { await openCamera(true); if (stream) notice('请将13位铭牌号对准取景框，系统将自动识别。'); return; }
    if (ocrBusy) return;
    ocrBusy = true;
    const button = $('recognizeDevice'); button.disabled = true;
    try {
      const video = $('viewfinder'), cropWidth = Math.round(video.videoWidth * .82), cropHeight = Math.round(video.videoHeight * .28);
      const cropX = Math.round((video.videoWidth - cropWidth) / 2), cropY = Math.round((video.videoHeight - cropHeight) / 2);
      const scale = Math.min(3, Math.max(1, 1800 / cropWidth));
      const canvas = document.createElement('canvas'); canvas.width = Math.round(cropWidth * scale); canvas.height = Math.round(cropHeight * scale);
      canvas.getContext('2d').drawImage(video, cropX, cropY, cropWidth, cropHeight, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('无法读取相机画面')),'image/jpeg',.86));
      const data = new FormData(); data.append('photo',blob,'nameplate.jpg');
      if (!automatic) notice('正在识别铭牌，请保持相机对准设备编号…');
      const response = await requestAPI('/api/field/recognize-equipment',{method:'POST',headers:{'X-Field-Token':profile.csrf},body:data});
      const result = await response.json().catch(()=>({}));
      if (!response.ok) throw new Error(result.error || '铭牌识别失败');
      if (!result.candidates?.length) {
        if (automatic) { notice('自动识别中，请保持13位数字清晰并完整位于框内。'); return; }
        throw new Error('没有识别到 Machine Number，请靠近铭牌重试或手工输入。');
      }
      stopCamera();
      $('recognizedNumber').textContent = result.candidates[0];
      $('recognitionDialog').showModal();
    } catch(error) { if (!automatic) notice(error.message,true); }
    finally { ocrBusy = false; button.disabled = false; }
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
    document.body.dataset.panel = name;
    document.querySelectorAll('[data-panel]').forEach(el => { el.hidden = el.dataset.panel !== name; });
    document.querySelectorAll('[data-tab]').forEach(el => el.setAttribute('aria-current', el.dataset.tab === name ? 'page':'false'));
    if (name !== 'camera') stopCamera();
    if (name === 'orders') renderOrders();
    if (name === 'ledger') loadLedger();
  }
  function lock(message) {
    bootstrapGeneration++;
    if ($('captureDateDialog').open) finishCaptureDate(null);
    deleteDraftSnapshot = null; $('deleteAllDraftsDialog').close();
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
    clearQueueViews();
    $('ledgerList').replaceChildren();
    setFieldText('networkStatus', '请登录');
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
      setFieldText('networkStatus', '在线');
    } catch (error) {
      if (generation !== bootstrapGeneration) return;
      try { profile = JSON.parse(localStorage.getItem(SESSION_KEY)); } catch (_) { profile = null; }
      if (!profile) { lock('首次使用需要联网登录。'); return; }
      setFieldText('networkStatus', '离线暂存');
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
    setFieldText('versionText', '系统版本 V' + profile.version);
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
    const options = [['', fieldText('请选择工单')], ...profile.orders.map(order => [String(order.id), order.order_number + ' · ' + order.client_name])];
    const existing = Array.from($('orderSelect').options, option => [option.value, option.text]);
    // Foreground refresh must not rebuild a native phone picker while it is open.
    if (JSON.stringify(existing) !== JSON.stringify(options)) {
      $('orderSelect').replaceChildren(...options.map(([value, label]) => new Option(label, value)));
    }
    $('orderSelect').value = String(selected || '');
    chooseOrder($('orderSelect').value);
    const ledgerValue = $('ledgerOrder').value;
    $('ledgerOrder').replaceChildren(new Option(fieldText('全部工单'), ''), ...(profile.ledger_orders||profile.orders).map(order => new Option(order.order_number + ' · ' + order.client_name, String(order.id))));
    $('ledgerOrder').value = ledgerValue;
  }
  function chooseOrder(id) {
    if (cameraSelection || taking) { $('orderSelect').value = String(currentOrder?.id || ''); return; }
    const previous = currentOrder?.id;
    currentOrder = profile?.orders.find(order => String(order.id) === String(id)) || null;
    $('orderSelect').value = currentOrder ? String(currentOrder.id) : '';
    $('captureSource').hidden = !currentOrder;
    $('cameraOrder').textContent = currentOrder ? currentOrder.order_number + ' · ' + currentOrder.client_name : '';
    setFieldText('orderContext', currentOrder ? [currentOrder.customer_name,currentOrder.site_address].filter(Boolean).join(' · ') : '请先选择照片所属工单。');
    if (profile) localStorage.setItem(orderStorageKey(), String(currentOrder?.id || ''));
    if (previous !== currentOrder?.id) {
      setCaptureMode('camera');
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
    setFieldText('locationStatus', '正在检查位置…');
    return new Promise((resolve,reject) => navigator.geolocation.getCurrentPosition(result => {
      updatePosition(result); resolve(position);
    }, () => { position = null; setFieldText('locationStatus', '定位失败，请允许定位后重试'); reject(new Error('请允许定位，并重新检查位置。')); }, {enableHighAccuracy:true,timeout:15000,maximumAge:0}));
  }
  async function checkLocation(forCapture, selected = chosenOrder()) {
    if (!selected) return false;
    if (!position || Date.now()-position.timestamp > 30000) await locate();
    const d = distance(selected);
    const warning = position.accuracy > 100 ? fieldText(`当前定位精度较低（±${Math.round(position.accuracy)}米）。`) : d === null ? fieldText('当前工单的站点还没有坐标。') : d-position.accuracy > profile.distance_limit ? fieldText(`当前位置距离所选工单站点约 ${(d/1000).toFixed(2)} 公里。`) : '';
    if (!warning) { locationNote = ''; return true; }
    lastWarning = Date.now();
    locationNote = warning + ' 系统保留员工明确选择的工单。';
    notice(warning + fieldText(' 照片仍保存到 ') + selected.order_number + fieldText('；如需更换，请先关闭相机。'));
    return true;
  }
  function finishWarning(result) { $('locationDialog').close(); const resolve = warningResolve; warningResolve = null; resolve?.(result); }
  $('keepOrder').addEventListener('click', () => { const reason = $('locationReason').value.trim(); if (!reason) { $('locationReason').focus(); $('locationReason').setCustomValidity(fieldText('请填写确认原因')); $('locationReason').reportValidity(); return; } locationNote = $('locationWarning').textContent + ' ' + reason; finishWarning(true); });
  $('locationReason').addEventListener('input', () => $('locationReason').setCustomValidity(''));
  $('switchOrder').addEventListener('click', () => { finishWarning(false); panel('orders'); });
  $('cancelLocation').addEventListener('click', () => finishWarning(false));
  $('locationDialog').addEventListener('cancel', event => { event.preventDefault(); finishWarning(false); });

  function clearPreview() {
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
  }
  function stopCamera() {
    clearInterval(scanTimer); scanTimer = null;
    clearInterval(ocrTimer); ocrTimer = null; ocrBusy = false;
    clearTimeout(ocrStartTimer); ocrStartTimer = null;
    cameraSelection = null;
    document.body.classList.remove('camera-active');
    document.body.classList.remove('recognition-mode');
    $('ocrGuide').hidden = true;
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    $('viewfinder').srcObject = null; $('viewfinder').hidden = true;
    $('openCamera').hidden = false; $('takePhoto').hidden = true; $('closeCamera').hidden = true;
    $('cameraStage').hidden = true;
    $('cameraView').hidden = captureMode !== 'camera';
  }
  async function openCamera(recognitionOnly = false) {
    if (!identityReady || !profile?.can_capture) return;
    if (!recognitionOnly && captureMode !== 'camera') return;
    if (!batch?.type) { notice('请先选择“设备照片”或“非设备照片”。',true); return; }
    if (!$('systemTime').checked && !timeAuthorized) { notice('请先验证水印时间调整密码。',true); return; }
    if (batch.type === 'equipment' && !deviceSession && !recognitionOnly && !confirmDevice()) return;
    const selected = chosenOrder();
    if (!selected) { notice('请先在上方选择工单。',true); $('orderSelect').focus(); return; }
    try {
      currentOrder = selected;
      cameraSelection = {order:{...selected}, userId:profile.user.id};
      stream?.getTracks().forEach(track => track.stop());
      stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1440}},audio:false});
      $('cameraView').hidden = false;
      $('cameraStage').hidden = false;
      $('viewfinder').srcObject = stream; $('viewfinder').hidden = false; await $('viewfinder').play();
      document.body.classList.add('camera-active');
      document.body.classList.toggle('recognition-mode', recognitionOnly);
      window.scrollTo({left:0, top:window.scrollY, behavior:'instant'});
      $('ocrGuide').hidden = !recognitionOnly;
      $('openCamera').hidden = true; $('takePhoto').hidden = recognitionOnly; $('closeCamera').hidden = false;
      scanDevice(); scanTimer = setInterval(scanDevice, 800);
      if (recognitionOnly) { ocrStartTimer = setTimeout(() => recognizeDevice(true), 500); ocrTimer = setInterval(() => recognizeDevice(true), 2200); }
    } catch (error) { notice('无法打开相机，请检查相机权限，或切换到“上传”选择照片。',true); stopCamera(); }
  }
  async function makeContext(source, lockedSelection = null) {
    const resolved = chosenOrder();
    if (!identityReady || !profile?.can_capture || !resolved) throw new Error('请先在上方选择工单。');
    const selected = lockedSelection?.order || cameraSelection?.order || {...resolved};
    if (source === 'file' && lockedSelection?.watermarkSource === 'original') {
      if (!batch || lockedSelection.userId !== profile.user.id) throw new Error('照片功能尚未准备好，请稍后重试。');
      const actual = new Date().toISOString();
      const timezoneName = $('timezoneName').value.trim() || 'UTC';
      new Intl.DateTimeFormat('en', {timeZone:timezoneName}).format();
      return {client_id:key(),user_id:profile.user.id,employee_name:profile.user.name,technician_user_id:profile.user.id,
        order_id:selected.id,order_number:selected.order_number,site_name:selected.client_name,site_address:selected.site_address,
        captured_at:actual,watermark_at:actual,batch_id:batch.id,photo_type:'legacy',timezone_name:timezoneName,
        latitude:0,longitude:0,accuracy:100000,location_verified:false,note:'',
        location_note:'原图已有水印，未检查拍摄位置。',source,error:''};
    }
    if (!batch || !deviceSession) throw new Error('请先确认本组照片类型和设备信息。');
    const selectedId = selected.id, selectedUser = lockedSelection?.userId || cameraSelection?.userId || profile.user.id;
    const keepsOriginalWatermark = source === 'file' && lockedSelection?.watermarkSource === 'original';
    // Current phone location does not prove where an imported photo was taken.
    if (!keepsOriginalWatermark) {
      await locate();
      if (!(await checkLocation(true, selected))) return null;
    }
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
      captured_at:actual.toISOString(),watermark_at:watermark.toISOString(),batch_id:batch.id,photo_type:batch.type,timezone_name:timezoneName,
      latitude:keepsOriginalWatermark ? 0 : position.latitude,longitude:keepsOriginalWatermark ? 0 : position.longitude,accuracy:keepsOriginalWatermark ? 100000 : position.accuracy,
      location_verified:!keepsOriginalWatermark,note:$('photoNote').value.trim(),location_note:keepsOriginalWatermark ? '原图已有水印，未检查拍摄位置。' : locationNote,source,error:'',equipment_number:deviceSession.equipment_number,
      position_number:deviceSession.position_number,container_number:deviceSession.container_number,
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
    notice('照片已保存为本机草稿：'+photo.order_number+'。完成本组后再统一上传。');
    await renderQueue();
  }
  async function keepOriginalFile(file, context) {
    if (!context || context.user_id !== profile?.user.id || !identityReady) throw new Error('账号已改变，请重新选择照片。');
    const photo = {...context, blob:file, original_filename:file.name || '', content_type:file.type || 'application/octet-stream'};
    await storePhoto(photo);
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
    if (captureMode !== 'upload') { event.preventDefault(); return; }
    if (taking || !identityReady || !profile?.can_capture) { reject('照片功能尚未准备好，请稍后重试。'); return; }

    const selected = chosenOrder();
    if (!selected) { reject('请先在上方选择工单。'); $('orderSelect').focus(); return; }
    // This handler runs on the native file input itself. iPhone/PWA therefore
    // receives a direct trusted user gesture instead of a scripted input click.
    batch ||= {id:key(),type:null,actual_start:Date.now(),watermark_start:null};
    captureContext = {order:{...selected}, userId:profile.user.id, watermarkSource:'original'};
  });
  $('photoFile').addEventListener('change', async () => {
    const files = Array.from($('photoFile').files || []), selection = captureContext; captureContext = null;
    if (!files.length || !selection) return;
    taking = true; processingFiles = true; processingTotal = files.length; processingDone = 0;
    setFieldText('photoProcessStatus', selection.watermarkSource === 'original'
      ? `已跳过位置检查，正在准备处理 ${files.length} 张照片…`
      : `正在检查位置并准备处理 ${files.length} 张照片…`);
    await renderQueue();
    try {
      const baseContext = await makeContext('file', selection);
      if (!baseContext) return;
      baseContext.watermark_source = selection.watermarkSource;
      let saved = 0;
      for (const file of files) {
        setFieldText('photoProcessStatus', `正在处理第 ${processingDone + 1}/${processingTotal} 张：${file.name || '照片'}…`);
        const url = URL.createObjectURL(file);
        try {
          const context = {...baseContext, client_id:key(), captured_at:new Date().toISOString()};
          if (selection.watermarkSource === 'original') await keepOriginalFile(file,context);
          else { const image = new Image(); image.src = url; await image.decode(); await keepCapture(image,context); }
          saved++;
        } catch(error) { const message=`照片 ${file.name || processingDone + 1} 未保存：${error.message}。`; notice(message,true); setFieldText('photoProcessStatus', message); }
        finally { URL.revokeObjectURL(url); }
        processingDone++;
      }
      if (saved) { const message=`已保存 ${saved}/${files.length} 张本机草稿，请点击下方完成按钮上传。`; notice(message); setFieldText('photoProcessStatus', message); }
    }
    catch(error) { const message='照片未保存：'+error.message+'。请保留原照片后重试。'; notice(message,true); setFieldText('photoProcessStatus', message); }
    finally { taking = false; processingFiles = false; processingTotal = 0; processingDone = 0; $('photoFile').value = ''; await renderQueue(); }
  });
  $('photoFile').addEventListener('cancel', () => { captureContext = null; });
  function clearQueueViews() {
    queueRenderGeneration++;
    for (const view of queueViews.values()) URL.revokeObjectURL(view.url);
    queueViews.clear();
    $('queueList').replaceChildren();
  }
  async function renderQueue() {
    const generation = ++queueRenderGeneration;
    try {
      const photos = await queued();
      if (generation !== queueRenderGeneration) return;
      $('queueCount').textContent = String(photos.length);
      const ids = new Set(photos.map(photo => photo.client_id));
      for (const [id, view] of queueViews) {
        if (!ids.has(id)) { view.row.remove(); URL.revokeObjectURL(view.url); queueViews.delete(id); }
      }
      $('draftCard').classList.toggle('has-drafts',photos.length > 0);
      photos.forEach((photo, index) => {
        let view = queueViews.get(photo.client_id);
        if (!view) {
          const row = textNode('div','','queue-item draft-item');
          const thumb = document.createElement('img'), url = URL.createObjectURL(photo.blob);
          view = {row, url, photo, title:textNode('strong',''), date:textNode('small',''), watermark:textNode('span',''), status:textNode('span','')};
          queueViews.set(photo.client_id, view);
          thumb.src = url; thumb.alt = fieldText('草稿照片');
          thumb.onerror = () => {
            const fallback = textNode('div','原图','original-photo-placeholder');
            fallback.addEventListener('click',()=>openDraft(view.photo)); thumb.replaceWith(fallback);
          };
          thumb.addEventListener('click',()=>openDraft(view.photo));
          const save = textNode('button','保存到手机相册'); save.type = 'button';
          save.addEventListener('click',()=>savePhotoToAlbum(view.photo));
          row.append(thumb,view.title,view.date,view.watermark,view.status,save);
        }
        view.photo = photo;
        const update = (node, text) => { const translated = fieldText(text); if (node.textContent !== translated) node.textContent = translated; };
        update(view.title,(photo.photo_type==='equipment' ? (photo.equipment_number||'N/A')+' · ' : '')+photo.order_number);
        update(view.date,photo.source === 'file' || photo.watermark_source === 'original' ? (photo.manual_capture_date || '上传时识别拍摄日期') : new Date(photo.captured_at).toLocaleString());
        update(view.watermark,photo.watermark_source === 'original' ? '保留原图水印' : '系统生成水印');
        update(view.status,uploadingPhotoId === photo.client_id ? `${fieldText('正在上传')} ${uploadIndex}/${uploadTotal}` : photo.error || '本机草稿');
        const current = $('queueList').children[index];
        if (current !== view.row) $('queueList').insertBefore(view.row,current || null);
      });
      $('draftCard').hidden = photos.length === 0;
      $('retryUpload').disabled = syncing || deletingDrafts;
      $('deleteAllDrafts').disabled = syncing || taking || processingFiles || deletingDrafts;
      setFieldText('retryUpload', syncing ? `${fieldText('正在上传')} ${uploadIndex}/${uploadTotal}` : '重试上传');
      const batchCount = batch ? photos.filter(photo=>photo.batch_id===batch.id).length : 0;
      $('completeBatch').hidden = batchCount === 0 && !processingFiles;
      $('completeBatch').disabled = deletingDrafts || syncing || processingFiles || batchCount === 0;
      setFieldText('completeBatch', processingFiles ? `正在处理照片（${processingDone}/${processingTotal}）` : batchCount ? `完成并上传本组照片（${batchCount} 张）` : '请先拍照或选择照片');
    } catch(error) { notice('无法读取本机照片存储：'+error.message,true); }
  }
  async function savePhotoToAlbum(photo) {
    const name = photo.order_number+'-'+photo.client_id+'.jpg';
    const file = new File([photo.blob], name, {type:photo.blob.type || 'image/jpeg'});
    try {
      if (navigator.share && (!navigator.canShare || navigator.canShare({files:[file]}))) {
        await navigator.share({files:[file], title:name});
        notice('请在系统菜单中选择“存储图像”或“保存到照片”。');
        return;
      }
      const href = URL.createObjectURL(photo.blob), link = document.createElement('a');
      link.href = href; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(href),10000);
      notice('照片已交给手机保存；请在“下载”中确认并移入相册。');
    } catch(error) { if (error.name !== 'AbortError') notice('无法打开手机保存菜单：'+error.message,true); }
  }
  function openDraft(photo) {
    draftSelection=photo; if (previewURL) URL.revokeObjectURL(previewURL); previewURL=URL.createObjectURL(photo.blob);
    $('draftLarge').src=previewURL; setFieldText('draftDetail',(photo.equipment_number||'非设备照片')+' · '+new Date(photo.captured_at).toLocaleString()+' · '+(photo.watermark_source === 'original' ? '保留原图水印' : '系统生成水印')); $('draftDialog').showModal();
  }
  let captureDateResolve = null, captureDateURL = null;
  function finishCaptureDate(value) {
    const resolve = captureDateResolve; captureDateResolve = null;
    $('captureDateDialog').close();
    if (captureDateURL) URL.revokeObjectURL(captureDateURL);
    captureDateURL = null; $('captureDateImage').removeAttribute('src');
    resolve?.(value);
  }
  function chooseCaptureDate(photo, candidates) {
    captureDateURL = URL.createObjectURL(photo.blob);
    $('captureDateImage').src = captureDateURL;
    $('captureDateFilename').textContent = photo.original_filename || photo.order_number;
    $('captureDateCandidates').textContent = (candidates || []).join(' / ');
    $('captureDateInput').value = photo.manual_capture_date || '';
    $('captureDateDialog').showModal();
    return new Promise(resolve => { captureDateResolve = resolve; });
  }
  $('captureDateForm').addEventListener('submit', event => { event.preventDefault(); if ($('captureDateForm').reportValidity()) finishCaptureDate($('captureDateInput').value); });
  $('cancelCaptureDate').addEventListener('click', () => finishCaptureDate(null));
  $('captureDateDialog').addEventListener('cancel', event => { event.preventDefault(); finishCaptureDate(null); });
  async function syncQueue(batchId = null) {
    if (deletingDrafts || $('deleteAllDraftsDialog').open || syncing || !navigator.onLine || !profile || !identityReady) return;
    syncing = true; uploadIndex = 0; uploadTotal = 0;
    await renderQueue();
    try {
      const sessionResponse = await requestAPI('/api/field/session',{cache:'no-store'});
      if (sessionResponse.status === 401 || sessionResponse.status === 403) { lock('登录已过期或权限已改变。照片仍留在本机，请重新登录拍摄账号。'); return; }
      if (!sessionResponse.ok) return;
      const live = await sessionResponse.json();
      if (live.user.id !== profile.user.id) { lock('账号已改变，已暂停原账号的照片上传。'); return; }
      profile.csrf = live.csrf;
      setFieldText('networkStatus', '在线');
      const pending = await queued(batchId); uploadTotal = pending.length;
      for (const photo of pending) {
        if (!identityReady || photo.user_id !== profile?.user.id) break;
        uploadingPhotoId = photo.client_id; uploadIndex++;
        try {
          if (photo.watermark_source === 'original' && photo.source !== 'file') {
            photo.source = 'file'; await storePhoto(photo);
          }
          await renderQueue();
          const data = new FormData();
          Object.entries(photo).forEach(([k,v]) => { if (k !== 'blob' && k !== 'error') data.append(k,String(v)); });
          data.append('photo',photo.blob,photo.original_filename || photo.client_id+'.jpg');
          if (photo.source === 'file') notice('正在识别水印和 EXIF 拍摄日期…');
          let response = await requestAPI('/api/field/photos',{method:'POST',headers:{'X-Field-Token':profile.csrf},body:data});
          if (response.status === 401) { lock('登录已过期，照片仍留在本机。请重新登录后补传。'); break; }
          let result = await response.json().catch(() => ({}));
          if (result.needs_capture_date) {
            const selectedDate = await chooseCaptureDate(photo, result.date_candidates);
            if (!selectedDate) { photo.error = fieldText('请选择照片的拍摄日期。'); await storePhoto(photo); break; }
            if (!identityReady || photo.user_id !== profile?.user.id) break;
            photo.manual_capture_date = selectedDate;
            await storePhoto(photo);
            data.set('manual_capture_date', selectedDate);
            response = await requestAPI('/api/field/photos',{method:'POST',headers:{'X-Field-Token':profile.csrf},body:data});
            result = await response.json().catch(() => ({}));
          }
          if (!response.ok || !result.ok) throw new Error(result.error || '上传未成功（'+response.status+'），照片仍保留');
          await storePhoto(photo,true);
          notice('照片已上传到 '+photo.order_number+'，系统已按拍摄日期归档。');
        } catch(error) {
          photo.error = error.message; await storePhoto(photo);
          if (!navigator.onLine || error instanceof TypeError) break;
        }
        uploadingPhotoId = null;
        await renderQueue();
      }
    } catch(error) { notice('暂时无法上传，照片仍保留在本机。'); }
    finally { syncing = false; uploadingPhotoId = null; await renderQueue(); }
  }
  async function completeBatch() {
    if (!batch) return;
    const id=batch.id; await syncQueue(id);
    if ((await queued(id)).length) { notice('部分照片尚未上传，请检查网络后重试。',true); return; }
    stopCamera(); batch=null; deviceSession=null; timeAuthorized=false; $('timeSettings').hidden=true; $('deviceSession').hidden=true; $('systemTime').checked=true; $('adjustedTimeFields').hidden=true; $('watermarkPassword').value='';
    $('equipmentKind').classList.remove('primary'); $('generalKind').classList.remove('primary'); setFieldText('kindStatus','请选择下一组照片类型。'); $('photoNote').value=''; $('existingWatermark').checked=true;
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
      const result = await response.json(); ledgerPhotos = result.rows; $('ledgerList').replaceChildren();
      setFieldText('ledgerSummary', `${result.rows.length} 张照片${result.truncated ? '，结果较多，请缩小日期范围':''}`);
      const table = document.createElement('table'); table.className = 'ledger-table';
      const thead = document.createElement('thead'), headerRow = document.createElement('tr');
      ['照片','工单 / 站点','设备信息','施工员 / 拍摄账号','拍摄时间','水印','现场位置','备注','来源'].forEach(label => headerRow.append(textNode('th',label)));
      thead.append(headerRow); const tbody = document.createElement('tbody'); table.append(thead,tbody); $('ledgerList').append(table);
      result.rows.forEach((photo, photoIndex) => {
        const card = textNode('article','','photo-card'), link = document.createElement('a'), image = document.createElement('img');
        link.href = photo.preview; image.src = photo.thumbnail; image.alt = fieldText('工单照片'); image.loading = 'lazy'; link.append(image);
        link.addEventListener('click',event=>{event.preventDefault();openLedgerPhoto(photoIndex);});
        const detail = document.createElement('div');
        const map=document.createElement(photo.location_verified ? 'a' : 'span');
        if (photo.location_verified) { map.href=`https://www.google.com/maps?q=${photo.latitude},${photo.longitude}`; map.target='_blank'; map.rel='noopener'; map.textContent=fieldText(`坐标：${Number(photo.latitude).toFixed(5)}, ${Number(photo.longitude).toFixed(5)}`); }
        else map.textContent=fieldText('坐标：未检查');
        const address=document.createElement(photo.location_verified ? 'a' : 'span');
        if (photo.location_verified) { address.href=map.href; address.target='_blank'; address.rel='noopener'; }
        address.textContent=fieldText('现场地址：')+(photo.site_address||photo.site_name);
        const deviceDetail = ['铭牌号：'+(photo.equipment_number || '无'), photo.position_number ? '位置号：'+photo.position_number : '', photo.container_number ? '集装箱号：'+photo.container_number : '', photo.pump_fuse_numbers ? '水泵保险：'+photo.pump_fuse_numbers : ''].filter(Boolean).join(' · ');
        const watermarkDetail = photo.watermark_source === 'original' ? '水印：保留原图水印' : '水印：系统生成 · '+(photo.watermark_at||photo.captured_at);
        detail.append(textNode('strong',photo.order_number+' · '+photo.site_name),textNode('p',deviceDetail),textNode('p','拍摄：'+photo.captured_at+' · '+watermarkDetail),textNode('p','施工员：'+(photo.technician_name||photo.employee_name)+' · 实际拍摄：'+photo.employee_name+' · 接收：'+photo.received_at),textNode('p',photo.note),address,textNode('br',''),map,textNode('p',photo.source === 'camera' ? '现场相机':'系统相机 / 选图'));
        card.append(link,detail); $('ledgerList').append(card);
        const row = document.createElement('tr');
        const imageCell = document.createElement('td'), tableLink = link.cloneNode(false), tableImage = image.cloneNode(false);
        tableLink.append(tableImage); tableLink.addEventListener('click',event=>{event.preventDefault();openLedgerPhoto(photoIndex);}); imageCell.append(tableLink); row.append(imageCell);
        const values = [photo.order_number+'\n'+photo.site_name, deviceDetail,
          (photo.technician_name||photo.employee_name)+'\n拍摄账号：'+photo.employee_name,
          photo.captured_at+'\n接收：'+photo.received_at, watermarkDetail,
          (photo.site_address||photo.site_name)+(photo.location_verified ? `\n${Number(photo.latitude).toFixed(5)}, ${Number(photo.longitude).toFixed(5)}` : '\n未检查坐标'),
          photo.note || '', photo.source === 'camera' ? '现场相机':'系统相机 / 选图'];
        values.forEach((value,index) => { const cell=document.createElement('td'); cell.textContent=fieldText(value);
          if(index===5 && photo.location_verified){const locationLink=document.createElement('a');locationLink.href=`https://www.google.com/maps?q=${photo.latitude},${photo.longitude}`;locationLink.target='_blank';locationLink.rel='noopener';locationLink.textContent=cell.textContent;cell.replaceChildren(locationLink);} row.append(cell); });
        tbody.append(row);
      });
    } catch(error) { setFieldText('ledgerSummary', '台账需要联网查看。待上传照片请到“拍照”页面查看。'); }
  }
  function openLedgerPhoto(index) {
    if (!ledgerPhotos.length) return;
    ledgerPhotoIndex = (index + ledgerPhotos.length) % ledgerPhotos.length;
    const photo = ledgerPhotos[ledgerPhotoIndex];
    $('ledgerPhotoImage').src = photo.preview;
    $('ledgerPhotoTitle').textContent = photo.order_number+' · '+photo.site_name;
    $('ledgerPhotoCounter').textContent = `${ledgerPhotoIndex + 1} / ${ledgerPhotos.length}`;
    $('ledgerPhotoDetail').textContent = [photo.equipment_number ? '铭牌号：'+photo.equipment_number : '', photo.position_number ? '位置号：'+photo.position_number : '', photo.container_number ? '集装箱号：'+photo.container_number : '', '拍摄：'+photo.captured_at, '施工员：'+(photo.technician_name||photo.employee_name), photo.note||''].filter(Boolean).map(fieldText).join(' · ');
    $('previousLedgerPhoto').disabled = ledgerPhotos.length < 2; $('nextLedgerPhoto').disabled = ledgerPhotos.length < 2;
    if (!$('ledgerPhotoDialog').open) $('ledgerPhotoDialog').showModal();
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
  $('recognizeDevice').addEventListener('click',() => recognizeDevice(false));
  $('confirmRecognizedNumber').addEventListener('click', () => {
    const value = $('recognizedNumber').textContent.trim();
    $('equipmentNumber').disabled = false; $('noEquipmentNumber').checked = false;
    $('equipmentNumber').value = value; deviceSession = null; $('recognitionDialog').close();
    setFieldText('deviceStatus', '识别到：'+value+'。请核对后点击“打开相机”。');
    notice('已识别设备编号 '+value+'，请核对后打开相机。');
  });
  $('retryRecognition').addEventListener('click', async () => { $('recognitionDialog').close(); await openCamera(true); if (stream) notice('请将13位铭牌号对准取景框，系统将自动识别。'); });
  $('manualRecognition').addEventListener('click', () => { $('recognitionDialog').close(); $('equipmentNumber').focus(); });
  $('nextDevice').addEventListener('click', async () => { if(batch && (await queued(batch.id)).length){notice('请先完成上传或删除当前组照片，再进入下一台设备。',true);return;} stopCamera(); resetDevice(); $('equipmentNumber').focus(); });
  $('noEquipmentNumber').addEventListener('change', () => { $('equipmentNumber').disabled = $('noEquipmentNumber').checked; $('equipmentNumber').required = !$('noEquipmentNumber').checked; if ($('noEquipmentNumber').checked) $('equipmentNumber').value=''; deviceSession=null; });
  $('equipmentNumber').addEventListener('input', () => { deviceSession=null; setFieldText('deviceStatus','编号已修改，请点击“打开相机”确认。'); });
  $('positionNumber').addEventListener('input', () => { deviceSession=null; setFieldText('deviceStatus','位置号已修改，请点击“打开相机”确认。'); });
  $('containerNumber').addEventListener('input', () => { deviceSession=null; setFieldText('deviceStatus','集装箱号已修改，请点击“打开相机”确认。'); });
  $('orderSelect').addEventListener('input', () => chooseOrder($('orderSelect').value));
  $('orderSelect').addEventListener('change', () => chooseOrder($('orderSelect').value));
  $('orderSearch').addEventListener('input',renderOrders);
  $('refreshLocation').addEventListener('click', () => locate().catch(error => notice(error.message,true)));
  $('cameraMode').addEventListener('click', () => setCaptureMode('camera'));
  $('uploadMode').addEventListener('click', () => setCaptureMode('upload'));
  $('openCamera').addEventListener('click',()=>openCamera(false)); $('closeCamera').addEventListener('click',stopCamera);
  $('deleteAllDrafts').addEventListener('click', async () => {
    if (syncing || taking || processingFiles || deletingDrafts || !identityReady) return;
    const userId = profile.user.id;
    try {
      const photos = await queued();
      if (!photos.length || !identityReady || profile?.user.id !== userId || syncing || taking || processingFiles) return;
      deleteDraftSnapshot = {userId, ids:new Set(photos.map(photo => photo.client_id))};
      $('deleteAllDraftsCount').textContent = String(photos.length);
      $('confirmDeleteAllDrafts').disabled = false;
      $('deleteAllDraftsDialog').showModal();
    } catch(error) { notice('无法读取本机照片存储：'+error.message,true); }
  });
  $('cancelDeleteAllDrafts').addEventListener('click', () => { deleteDraftSnapshot = null; $('deleteAllDraftsDialog').close(); });
  $('deleteAllDraftsDialog').addEventListener('cancel', () => { deleteDraftSnapshot = null; });
  $('confirmDeleteAllDrafts').addEventListener('click', async () => {
    const snapshot = deleteDraftSnapshot;
    if (!snapshot || deletingDrafts || syncing || taking || processingFiles || !identityReady || profile?.user.id !== snapshot.userId) return;
    deletingDrafts = true; $('confirmDeleteAllDrafts').disabled = true;
    try {
      const db = await openDB();
      if (!identityReady || profile?.user.id !== snapshot.userId) throw new Error('账号已改变，请重试。');
      await new Promise((resolve, reject) => {
        const tx = db.transaction('photos','readwrite'), storage = tx.objectStore('photos');
        const request = storage.openCursor();
        request.onsuccess = () => {
          const cursor = request.result;
          if (!cursor) return;
          if (cursor.value.user_id === snapshot.userId && snapshot.ids.has(cursor.value.client_id)) cursor.delete();
          cursor.continue();
        };
        tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error || new Error('删除失败'));
      });
      deleteDraftSnapshot = null; $('deleteAllDraftsDialog').close();
      if (draftSelection && snapshot.ids.has(draftSelection.client_id)) { draftSelection = null; $('draftDialog').close(); clearPreview(); }
      notice('已删除当前账号的全部待上传草稿。');
    } catch(error) { notice('删除失败：'+error.message,true); $('confirmDeleteAllDrafts').disabled = false; }
    finally { deletingDrafts = false; await renderQueue(); }
  });
  $('retryUpload').addEventListener('click',()=>syncQueue()); $('reloadOrders').addEventListener('click',bootstrap);
  $('equipmentKind').addEventListener('click',()=>chooseKind('equipment')); $('generalKind').addEventListener('click',()=>chooseKind('general'));
  $('systemTime').addEventListener('change',()=>{ const adjusted=!$('systemTime').checked; $('adjustedTimeFields').hidden=!adjusted; timeAuthorized=!adjusted; $('watermarkStart').disabled=adjusted; if(adjusted&&!$('watermarkStart').value){const d=new Date();d.setMinutes(d.getMinutes()-d.getTimezoneOffset());$('watermarkStart').value=d.toISOString().slice(0,16);} setFieldText('timeStatus',adjusted?'请输入密码并设置水印开始时间。':'使用当前系统时间。'); });
  $('verifyTimePassword').addEventListener('click',verifyTimePassword);
  $('completeBatch').addEventListener('click',completeBatch);
  $('closeDraft').addEventListener('click',()=>$('draftDialog').close());
  $('deleteDraft').addEventListener('click',async()=>{if(!draftSelection)return;await storePhoto(draftSelection,true);draftSelection=null;$('draftDialog').close();await renderQueue();notice('已删除本机草稿照片。');});
  $('refreshLedger').addEventListener('click',loadLedger);
  $('closeLedgerPhoto').addEventListener('click',()=>$('ledgerPhotoDialog').close());
  $('previousLedgerPhoto').addEventListener('click',()=>openLedgerPhoto(ledgerPhotoIndex-1));
  $('nextLedgerPhoto').addEventListener('click',()=>openLedgerPhoto(ledgerPhotoIndex+1));
  $('ledgerPhotoStage').addEventListener('touchstart',event=>{ledgerTouchStart=event.changedTouches[0].clientX;},{passive:true});
  $('ledgerPhotoStage').addEventListener('touchend',event=>{if(ledgerTouchStart===null)return;const distance=event.changedTouches[0].clientX-ledgerTouchStart;ledgerTouchStart=null;if(Math.abs(distance)>45)openLedgerPhoto(ledgerPhotoIndex+(distance<0?1:-1));},{passive:true});
  document.addEventListener('keydown',event=>{if(!$('ledgerPhotoDialog').open)return;if(event.key==='ArrowLeft')openLedgerPhoto(ledgerPhotoIndex-1);if(event.key==='ArrowRight')openLedgerPhoto(ledgerPhotoIndex+1);});
  $('ledgerFilter').addEventListener('submit', event => { event.preventDefault(); loadLedger(); });
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => panel(button.dataset.tab)));
  $('fieldLogout').addEventListener('click', () => { localStorage.removeItem(SESSION_KEY); identityReady = false; stopCamera(); });
  window.addEventListener('storage', event => { if (event.key === SESSION_KEY && event.newValue === null) lock('账号已退出，请重新登录。'); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopCamera(); else if (!taking) bootstrap(); });
  window.addEventListener('online',bootstrap); window.addEventListener('offline', () => { setFieldText('networkStatus', '离线暂存'); });
  window.addEventListener('pagehide',stopCamera);
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; $('installApp').hidden = false; });
  $('installApp').addEventListener('click', async () => { await installPrompt?.prompt(); installPrompt = null; $('installApp').hidden = true; });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/field/sw.js').catch(() => notice('离线界面尚未准备好，请联网重新打开一次。',true));
  navigator.storage?.persist?.().catch(() => {});
  bootstrap().catch(error => notice('无法准备现场工作界面：'+error.message,true));
})();
