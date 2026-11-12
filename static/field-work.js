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
  let repairStage = 'before', faultPhotos = [];
  let activeRepair = null, pendingRepairRows = [], repairStartId = null;
  let deviceSession = null, scanTimer = null, ocrTimer = null, ocrStartTimer = null, ocrBusy = false, detector = null;
  let batch = null, timeAuthorized = false, useSystemTime = true, draftSelection = null;
  let ledgerPhotos = [], ledgerPhotoIndex = 0, ledgerTouchStart = null;
  let captureMode = 'camera', deviceStatusText = '';
  let uploadWatermark = false, pendingUploadWatermark = false, wmItems = [], wmBusy = false;
  let deletingDrafts = false, deleteDraftSnapshot = null;
  let cameraStripSession = null;
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
    $('captureDetails').hidden = false;
    $('uploadHeader').hidden = mode !== 'upload';
    $('timeSettings').hidden = mode !== 'camera' || !batch?.type;
    $('captureNote').hidden = mode !== 'camera';
    if (mode !== 'upload') setUploadWatermark(false);
    refreshUploadControls();
    for (const value of ['camera', 'upload']) {
      $(value + 'Mode').classList.toggle('primary', value === mode);
      $(value + 'Mode').setAttribute('aria-pressed', String(value === mode));
    }
  }
  function resetDevice() {
    deviceSession = null;
    if (activeRepair) return;
    $('equipmentNumber').closest('fieldset').classList.remove('locked');
    $('equipmentNumber').value = ''; $('equipmentModel').value = '';
    $('positionNumber').value = '';
    $('containerNumber').value = '';
    $('noEquipmentNumber').checked = false;
    $('equipmentNumber').disabled = false;
    $('equipmentNumber').required = true;
    setFieldText('deviceStatus', '新设备：请扫描或输入编号，然后点击“打开相机”。');
  }
  function confirmDevice() {
    if (activeRepair && activeRepair.order_id === chosenOrder()?.id) {
      deviceSession = {...activeRepair, no_equipment_number:!activeRepair.equipment_number};
      return true;
    }
    const number = $('equipmentNumber').value.trim(), noNumber = $('noEquipmentNumber').checked;
    if (!number && !noNumber) { notice('请输入设备编号，或勾选“无编号”。', true); $('equipmentNumber').focus(); return false; }
    deviceSession = {id:key(), equipment_number:noNumber ? '' : number,
      position_number:$('positionNumber').value.trim().toUpperCase(), container_number:$('containerNumber').value.trim().toUpperCase(),
      no_equipment_number:noNumber};
    $('equipmentNumber').closest('fieldset').classList.add('locked');
    $('equipmentNumber').value = deviceSession.equipment_number;
    $('equipmentNumber').disabled = noNumber;
    setFieldText('deviceStatus', `已锁定：${number || '无铭牌号'}${deviceSession.position_number ? ' · 位置 '+deviceSession.position_number : ''}${deviceSession.container_number ? ' · 集装箱 '+deviceSession.container_number : ''}。后续照片沿用；换设备请点“清除”。`);
    return true;
  }

  function clearDeviceInputs() {
    if (activeRepair) return;
    // 新一组/换类型：清空设备输入框，避免残留上一张照片的设备信息。
    $('equipmentNumber').value = ''; $('equipmentModel').value = ''; $('positionNumber').value = ''; $('containerNumber').value = '';
    $('noEquipmentNumber').checked = false; $('equipmentNumber').disabled = false; $('equipmentNumber').required = true;
  }

  const KIND_LABEL = {equipment:'设备',arrival:'进场',departure:'离场',safety:'自检',general:'非设备',legacy:'历史'};
  let kindChangeGeneration = 0;
  function photoTypeLabel(t){return KIND_LABEL[t]||t||'-';}
  function setKindChoiceActive(type) {
    // 拍照与上传共用同一行照片类型分段按钮：进场 / 离场 / 自检 / 设备
    for (const kind of ['arrival','departure','safety','equipment']) $(kind+'Kind').setAttribute('aria-pressed',String(kind===type));
    if (typeof refreshUploadControls === 'function') refreshUploadControls();
  }
  async function chooseKind(type) {
    const generation = ++kindChangeGeneration;
    if (taking || processingFiles || syncing || captureContext) {
      notice('正在处理照片，请完成后再切换照片类型。',true);
      return;
    }
    // Reopening the non-equipment choices preserves an already selected subtype.
    if (type === 'general' && ['arrival','departure','safety'].includes(batch?.type)) {
      startKind(batch.type);
      return;
    }
    if (batch?.type && batch.type !== type) {
      const currentBatch = batch;
      let items;
      try { items = await queued(currentBatch.id); }
      catch (_) {
        if (generation === kindChangeGeneration) notice('无法检查待上传照片，照片类型未切换，请重试。',true);
        return;
      }
      if (generation !== kindChangeGeneration || batch !== currentBatch) return;
      if (items.length) {
        notice('当前仍为「'+photoTypeLabel(currentBatch.type)+'」照片；请先完成上传或删除当前组照片，再切换类型。',true);
        return;
      }
    }
    if (taking || processingFiles || syncing || captureContext) {
      notice('正在处理照片，请完成后再切换照片类型。',true);
      return;
    }
    // Update highlighted choices only after the change has been accepted.
    startKind(type);
  }
  function startKind(type) {
    const previousType = batch?.type;
    batch ||= {id:key(), type, actual_start:Date.now(), watermark_start:null};
    const typeChanged = previousType && previousType !== type;
    batch.type = type;
    if (typeChanged) clearDeviceInputs();  // 换组：不残留上一组的设备信息
    $(type === 'equipment' ? 'deviceNoteSlot' : 'generalNoteSlot').append($('captureNote'));
    $('deviceSession').hidden = type !== 'equipment';  // 非设备照片（进场/离场/自检）不填写设备信息，隐藏设备区域
    setKindChoiceActive(type);
    $('timeSettings').hidden = typeof captureMode !== 'undefined' && captureMode !== 'camera';
    const KIND_TEXT = {equipment:'设备照片：需确认 Machine Number。',arrival:'进场照片：记录到达现场时间。',departure:'离场照片：记录离开现场时间。',safety:'自检照片：安全自检记录。',general:'非设备照片：请先选择进场、离场或自检照片。'};
    setFieldText('kindStatus', KIND_TEXT[type] || KIND_TEXT.general);
    if (type !== 'equipment') deviceSession = null;  // 非设备照片不建立设备 session
    else if (previousType !== 'equipment') resetDevice();
    renderQueue();
  }
  async function verifyTimePassword() {
    const response = await requestAPI('/api/field/verify-watermark-password',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf},body:JSON.stringify({password:$('watermarkPassword').value})});
    timeAuthorized = response.ok;
    if (!timeAuthorized) {
      setFieldText('passwordStatus','密码错误，请重试。');
      notice('水印时间调整密码错误。',true);
      return;
    }
    $('watermarkPasswordDialog').close();
    if (pendingUploadWatermark) { pendingUploadWatermark = false; setUploadWatermark(true); return; }
    openWatermarkTimeDialog();
  }
  function openWatermarkDialog() {
    if (!batch?.type) { notice('请先选择本组照片类型。',true); return; }
    if (timeAuthorized) { openWatermarkTimeDialog(); return; }  // 本组已验证过密码：直接进入时间设定
    $('watermarkPassword').value = '';
    setFieldText('passwordStatus','请输入水印时间调整密码。');
    $('watermarkPasswordDialog').showModal();
    $('watermarkPassword').focus();
  }
  function openWatermarkTimeDialog() {
    if (!$('watermarkStart').value) {
      const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
      $('watermarkStart').value = d.toISOString().slice(0,16);  // 分钟精度，秒数由确认时随机生成
    }
    $('watermarkStart').disabled = false;
    setFieldText('timeStatus', useSystemTime ? '设置后本组照片使用该时间；上传完成后自动恢复系统时间。' : '密码已通过，可修改本组水印时间。');
    $('watermarkTimeDialog').showModal();
  }
  function confirmWatermarkTime() {
    const start = $('watermarkStart').value;
    if (!timeAuthorized) { notice('请先验证水印时间调整密码。',true); return; }
    const anchor = new Date(start);
    if (!start || Number.isNaN(anchor.getTime())) { notice('请选择有效的水印开始时间。',true); $('watermarkStart').focus(); return; }
    anchor.setSeconds(Math.floor(Math.random()*60), 0);  // 输入只到分钟，随机附加秒数避免每张都是 :00
    useSystemTime = false;
    batch.watermark_anchor_ms = anchor.getTime();
    batch.first_adjusted_shot_at = null;
    const display = start.replace('T',' ');
    $('watermarkModeText').textContent = '水印时间：' + display;
    setFieldText('timeStatus','水印时间已设置为 ' + display + '（秒数已随机），本组照片使用该时间；上传完成后恢复系统时间。');
    $('watermarkTimeDialog').close();
  }
  function cancelWatermarkTime() { $('watermarkTimeDialog').close(); }
  function cancelWatermarkPassword() { pendingUploadWatermark = false; $('watermarkPasswordDialog').close(); }
  async function scanDevice() {
    if (!stream || deviceSession || !('BarcodeDetector' in window)) return;
    try {
      detector ||= new BarcodeDetector();
      const results = await detector.detect($('viewfinder'));
      const value = results.map(item => item.rawValue?.trim()).find(Boolean);
      if (value) { $('equipmentNumber').value = value.slice(0,200); setFieldText('deviceStatus', '自动识别到：'+value+'。请核对后点击“打开相机”。'); }
    } catch (_) {}
  }
  async function recognizeDevice(automatic = false, faultMode = false) {
    if (automatic && (batch?.type !== 'equipment' || deviceSession || !document.body.classList.contains('recognition-mode'))) return;
    if (!stream || !$('viewfinder').videoWidth) {
      if (faultMode) { notice('相机尚未准备好，请稍后再试。', true); return; }
      await openCamera(true);
      if (stream) notice('请拍全铭牌，系统会识别设备编号和型号。');
      return;
    }
    if (ocrBusy) return;
    ocrBusy = true;
    const button = $(faultMode ? 'readRepairFault' : 'recognizeDevice'); button.disabled = true;
    const captureStream = stream;
    try {
      const video = $('viewfinder');
      const scale = Math.min(1, 2400 / Math.max(video.videoWidth, video.videoHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('无法读取相机画面')),'image/jpeg',.86));
      const data = new FormData(); data.append('photo',blob,'nameplate.jpg');
      if (faultMode) data.append('mode', 'controller_fault');
      if (!automatic) notice(faultMode ? '正在识别手操器屏幕…' : '正在识别设备编号和型号…');
      const response = await requestAPI('/api/field/recognize-equipment',{method:'POST',headers:{'X-Field-Token':profile.csrf},body:data});
      const result = await response.json().catch(()=>({}));
      if (stream !== captureStream) return;
      if (!response.ok) throw new Error(result.error || (faultMode ? '故障识别失败' : '铭牌识别失败'));
      if (faultMode) {
        if (repairStage !== 'before' || activeRepair || !confirmDevice()) return;
        const context = await makeContext('camera');
        if (!context || stream !== captureStream) return;
        const preview = URL.createObjectURL(blob);
        faultPhotos.push({canvas, context, preview});
        const thumbnail = new Image();
        thumbnail.src = preview;
        thumbnail.alt = '手操器故障照片';
        $('repairFaultPhotos').append(thumbnail);
        $('repairFaultPhotos').hidden = false;
        if (!result.fault_text) throw new Error('没有读到屏幕文字，请靠近、避开反光后重试。');
        const field = $('repairFault');
        const current = field.value.trim();
        const combined = current && !current.includes(result.fault_text) ? current + '\n' + result.fault_text : current || result.fault_text;
        if (combined.length > field.maxLength) throw new Error('故障栏空间不足，请精简已有内容后重试。');
        field.value = combined;
        stopCamera();
        field.focus();
        notice('识别结果已填入，请核对报码和提示文字。');
        return;
      }
      if (!result.candidates?.length && !result.equipment_model) {
        if (automatic) { notice('自动识别中，请保持整张铭牌清晰。'); return; }
        throw new Error('未识别到设备编号或型号，请重试或手工输入。');
      }
      stopCamera();
      $('recognizedNumber').textContent = result.candidates?.[0] || '';
      $('recognizedModel').textContent = result.equipment_model || '';
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
    // ArrayBuffer is structured-cloned into IDB instead of retaining an iOS File handle.
    // On failure keep the original draft intact, including its error message.
    let saved = photo;
    if (!remove && photo.blob instanceof Blob) {
      try { saved = {...photo, blob:await photo.blob.arrayBuffer(), blob_type:photo.blob.type}; }
      catch (error) { if (!photo.error) throw error; }
    }
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('photos', 'readwrite');
      const store = tx.objectStore('photos');
      if (remove) store.delete(photo.client_id); else store.put(saved);
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
      request.onsuccess = () => resolve(request.result.filter(item => item.user_id === profile.user.id && (!batchId || item.batch_id === batchId)).sort((a,b) => a.captured_at.localeCompare(b.captured_at)).map(item => item.blob instanceof ArrayBuffer ? {...item, blob:new Blob([item.blob], {type:item.blob_type || item.content_type || 'image/jpeg'})} : item));
      request.onerror = () => reject(request.error);
    });
  }
  function panel(name) {
    document.body.dataset.panel = name;
    document.querySelectorAll('[data-panel]').forEach(el => { el.hidden = el.dataset.panel !== name; });
    document.querySelectorAll('[data-tab]').forEach(el => el.setAttribute('aria-current', el.dataset.tab === name ? 'page':'false'));
    if (name !== 'camera') stopCamera();
    if (name === 'orders') renderOrders();
    if (name === 'ledger') { seedLedgerDateDefaults(); loadLedger(); }
  }
  // 台账不筛选直接查询会返回全部照片（图片多，手机端很卡）。
  // 进入台账时给日期填上「仅今天」的默认范围，只拉当天照片。
  // 只填空字段：用户自己填过的日期不覆盖。
  function seedLedgerDateDefaults() {
    const form = $('ledgerFilter');
    if (!form) return;
    const today = localDateString(new Date());
    const from = form.elements.date_from, to = form.elements.date_to;
    if (from && !from.value) from.value = today;
    if (to && !to.value) to.value = today;
  }
  // 用本地时区取 YYYY-MM-DD；toISOString() 是 UTC，跨时区会差一天。
  function localDateString(date) {
    const pad = value => String(value).padStart(2, '0');
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
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
      if (profile && profile.user.id !== next.user.id) { stopCamera(); clearPreview(); clearRecentCaptures(); currentOrder = null; captureContext = null; $('timezoneName').value = ''; $('ledgerList').replaceChildren(); }
      profile = next;
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(profile)); }
      catch (_) { notice('无法保存离线登录资料，请检查手机存储空间。',true); }
      setFieldText('networkStatus', '在线');
    } catch (error) {
      if (generation !== bootstrapGeneration) return;
      try { profile = JSON.parse(localStorage.getItem(SESSION_KEY)); } catch (_) { profile = null; }
      if (!profile) { lock('首次使用需要联网登录。'); return; }
      setFieldText('networkStatus', '离线暂存');
      notice('服务器暂未响应或连接不稳定。使用最近同步的工单，照片先保存在本机。');
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
    if (cameraSelection || taking || (faultPhotos.length && String(id) !== String(currentOrder?.id || ''))) { $('orderSelect').value = String(currentOrder?.id || ''); return; }
    const previous = currentOrder?.id;
    currentOrder = profile?.orders.find(order => String(order.id) === String(id)) || null;
    $('orderSelect').value = currentOrder ? String(currentOrder.id) : '';
    $('captureSource').hidden = !currentOrder;
    $('cameraOrder').textContent = currentOrder ? currentOrder.order_number + ' · ' + currentOrder.client_name : '';
    setFieldText('orderContext', currentOrder ? [currentOrder.customer_name,currentOrder.site_address].filter(Boolean).join(' · ') : '请先选择照片所属工单。');
    if (profile) localStorage.setItem(orderStorageKey(), String(currentOrder?.id || ''));
    if (previous !== currentOrder?.id) {
      selectRepairStage('before');
      clearFaultPhotos();
      $('repairFault').value = '';
      resetDevice();
      refreshPendingRepairs().catch(() => {});
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
    cameraStripSession = null;
    clearRecentCaptures();
    clearInterval(scanTimer); scanTimer = null;
    clearInterval(ocrTimer); ocrTimer = null; ocrBusy = false;
    clearTimeout(ocrStartTimer); ocrStartTimer = null;
    cameraSelection = null;
    document.body.classList.remove('camera-active');
    document.body.classList.remove('recognition-mode');
    $('ocrGuide').hidden = true;
    $('readRepairFault').hidden = true;
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    $('viewfinder').srcObject = null; $('viewfinder').hidden = true;
    $('openCamera').hidden = false; $('takePhoto').hidden = true; $('closeCamera').hidden = true;
    $('cameraStage').hidden = true;
    $('cameraView').hidden = captureMode !== 'camera';
  }
  async function openCamera(recognitionOnly = false, faultMode = false) {
    if (!identityReady || !profile?.can_capture) return;
    if (!recognitionOnly && captureMode !== 'camera') return;
    if (!batch?.type) { notice('请先选择“设备照片”或“非设备照片”。',true); return; }
    if (batch.type === 'general') { notice('请先选择进场、离场或自检照片。',true); return; }
    if (!useSystemTime && !timeAuthorized) { notice('请先验证水印时间调整密码。',true); return; }
    if (batch.type === 'equipment' && !recognitionOnly && !activeRepair) {
      notice(repairStage === 'after' ? '请先按位置号选择维修记录。' : '请先保存维修前状态。', true);
      return;
    }
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
      clearRecentCaptures();
      cameraStripSession = recognitionOnly ? null : {batchId:batch.id, ids:new Set()};
      document.body.classList.add('camera-active');
      document.body.classList.toggle('recognition-mode', recognitionOnly);
      window.scrollTo({left:0, top:window.scrollY, behavior:'instant'});
      $('ocrGuide').hidden = !recognitionOnly;
      $('ocrGuide').querySelector('span').textContent = faultMode ? '将手操器屏幕放入框内' : '将整张铭牌放入框内';
      $('readRepairFault').hidden = !faultMode;
      $('openCamera').hidden = true; $('takePhoto').hidden = recognitionOnly; $('closeCamera').hidden = false;
      await renderQueue();
      if (!faultMode) { scanDevice(); scanTimer = setInterval(scanDevice, 800); }
      if (recognitionOnly && !faultMode) { ocrStartTimer = setTimeout(() => recognizeDevice(true), 500); ocrTimer = setInterval(() => recognizeDevice(true), 2200); }
    } catch (error) { notice('无法打开相机，请检查相机权限，或切换到“上传”选择照片。',true); stopCamera(); }
  }
  // ---- 0.1.467 上传方式（不生成水印 / 生成水印）与 EXIF 时间批量调整 ----
  const UPLOAD_TYPES = ['arrival','departure','safety','equipment'];
  const UPLOAD_TYPE_TEXT = {equipment:'设备照片：需确认 Machine Number。',arrival:'进场照片：记录到达现场时间。',departure:'离场照片：记录离开现场时间。',safety:'自检照片：安全自检记录。'};
  const pad2 = n => String(n).padStart(2,'0');
  const localInput = d => `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const localText = d => `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const shortText = d => `${pad2(d.getMonth()+1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  function refreshUploadControls() {
    const typed = UPLOAD_TYPES.includes(batch?.type);
    $('fileCapture').classList.toggle('disabled', !typed || wmBusy);
    $('photoFile').disabled = !typed || wmBusy;
    $('kindStatus').classList.toggle('warn', !typed);
    setFieldText('kindStatus', typed ? UPLOAD_TYPE_TEXT[batch.type] : captureMode === 'upload' ? '请选择照片类型（两种上传方式均必填）' : '请选择照片类型。');
    $('uploadKeepMode').setAttribute('aria-pressed', String(!uploadWatermark));
    $('uploadWatermarkMode').setAttribute('aria-pressed', String(uploadWatermark));
    setFieldText('uploadModeNote', uploadWatermark ? '支持相册多选。按每张照片的 EXIF 拍摄时间生成水印，可整体调整时间。' : '支持相册多选。保留照片原样，点击完成后上传原文件，由服务器压缩归档。');
  }
  function setUploadWatermark(on) {
    uploadWatermark = Boolean(on);
    if (!uploadWatermark) clearWatermarkItems();
    refreshUploadControls();
  }
  function readExifDate(buffer) {
    try {
      const v = new DataView(buffer);
      if (v.byteLength < 4 || v.getUint16(0) !== 0xFFD8) return null;
      let o = 2;
      while (o + 4 <= v.byteLength) {
        const marker = v.getUint16(o);
        if ((marker & 0xFF00) !== 0xFF00 || marker === 0xFFDA) return null;
        const length = v.getUint16(o + 2);
        if (marker === 0xFFE1 && o + 10 <= v.byteLength && v.getUint32(o + 4) === 0x45786966) return exifTiffDate(v, o + 10);
        o += 2 + length;
      }
    } catch (_) {}
    return null;
  }
  function exifTiffDate(v, t) {
    const le = v.getUint16(t) === 0x4949;
    const u16 = p => v.getUint16(p, le), u32 = p => v.getUint32(p, le);
    const entries = ifd => {
      const out = {}, count = u16(ifd);
      for (let i = 0; i < count; i++) { const e = ifd + 2 + i * 12; out[u16(e)] = {count:u32(e + 4), at:e + 8}; }
      return out;
    };
    const text = entry => {
      if (!entry || entry.count < 19) return null;
      const start = entry.count > 4 ? t + u32(entry.at) : entry.at;
      let s = ''; for (let i = 0; i < 19; i++) s += String.fromCharCode(v.getUint8(start + i));
      const m = /^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(s);
      if (!m) return null;
      const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
      return Number.isNaN(d.getTime()) || d.getFullYear() < 2000 ? null : d;
    };
    const ifd0 = entries(t + u32(t + 4));
    let exif = {};
    if (ifd0[0x8769]) exif = entries(t + u32(ifd0[0x8769].at));
    return text(exif[0x9003]) || text(exif[0x9004]) || text(ifd0[0x0132]);
  }
  const isHeif = file => /\.(heic|heif)$/i.test(file.name || '') || /^image\/hei[cf]/i.test(file.type || '');
  function heifExifLocation(v) {
    // ISOBMFF: meta → iinf（找 Exif 项）+ iloc（该项在文件中的偏移和长度）
    const type = p => String.fromCharCode(v.getUint8(p), v.getUint8(p + 1), v.getUint8(p + 2), v.getUint8(p + 3));
    const boxes = (start, end) => {
      const out = [];
      for (let p = start; p + 8 <= end;) {
        let size = v.getUint32(p), header = 8;
        if (size === 1) { size = v.getUint32(p + 8) * 4294967296 + v.getUint32(p + 12); header = 16; }
        else if (size === 0) size = end - p;
        if (size < header) break;
        out.push({type:type(p + 4), body:p + header, end:Math.min(p + size, end)});
        p += size;
      }
      return out;
    };
    const meta = boxes(0, v.byteLength).find(box => box.type === 'meta');
    if (!meta) return null;
    const children = boxes(meta.body + 4, meta.end);
    const iinf = children.find(box => box.type === 'iinf'), iloc = children.find(box => box.type === 'iloc');
    if (!iinf || !iloc) return null;
    const iinfVersion = v.getUint8(iinf.body);
    let exifId = null;
    for (const infe of boxes(iinf.body + 4 + (iinfVersion === 0 ? 2 : 4), iinf.end)) {
      if (infe.type !== 'infe') continue;
      const version = v.getUint8(infe.body);
      if (version < 2) continue;
      const id = version === 2 ? v.getUint16(infe.body + 4) : v.getUint32(infe.body + 4);
      if (type(infe.body + 4 + (version === 2 ? 2 : 4) + 2) === 'Exif') { exifId = id; break; }
    }
    if (exifId == null) return null;
    let p = iloc.body;
    const version = v.getUint8(p); p += 4;
    const sizes = v.getUint16(p); p += 2;
    const offsetSize = sizes >> 12, lengthSize = (sizes >> 8) & 15, baseSize = (sizes >> 4) & 15, indexSize = version ? sizes & 15 : 0;
    const read = n => { let value = 0; for (let i = 0; i < n; i++) value = value * 256 + v.getUint8(p + i); p += n; return value; };
    const count = version < 2 ? read(2) : read(4);
    for (let i = 0; i < count; i++) {
      const id = version < 2 ? read(2) : read(4);
      const method = version ? read(2) & 15 : 0;
      read(2);
      const base = read(baseSize), extents = read(2);
      let first = null;
      for (let e = 0; e < extents; e++) { read(indexSize); const offset = read(offsetSize), length = read(lengthSize); first ||= {offset:base + offset, length}; }
      if (id === exifId) return method === 0 && first ? first : null;
    }
    return null;
  }
  async function readPhotoExifDate(file) {
    try {
      const head = await file.slice(0, 1024 * 1024).arrayBuffer();
      if (!isHeif(file)) return readExifDate(head);
      const location = heifExifLocation(new DataView(head));
      if (!location || !location.length || location.length > 4 * 1024 * 1024) return null;
      const exif = new DataView(await file.slice(location.offset, location.offset + location.length).arrayBuffer());
      const tiff = 4 + exif.getUint32(0);
      return tiff + 8 <= exif.byteLength ? exifTiffDate(exif, tiff) : null;
    } catch (_) { return null; }
  }
  let heicLoader = null;
  function loadHeicDecoder() {
    heicLoader ||= new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/vendor/heic2any/heic2any.min.js';
      script.onload = () => window.heic2any ? resolve(window.heic2any) : reject(new Error('HEIC 解码器加载失败'));
      script.onerror = () => { heicLoader = null; reject(new Error('HEIC 解码器加载失败，请检查网络')); };
      document.head.append(script);
    });
    return heicLoader;
  }
  async function decodableUrl(file) {
    // Safari 可直接解码 HEIC；其他浏览器转为 JPEG 后用于缩略图和绘制水印。
    const url = URL.createObjectURL(file);
    if (!isHeif(file)) return url;
    try { const probe = new Image(); probe.src = url; await probe.decode(); return url; }
    catch (_) { URL.revokeObjectURL(url); }
    const heic2any = await loadHeicDecoder();
    const converted = await heic2any({blob:file, toType:'image/jpeg', quality:0.92});
    return URL.createObjectURL(Array.isArray(converted) ? converted[0] : converted);
  }
  function clearWatermarkItems() {
    for (const item of wmItems) URL.revokeObjectURL(item.url);
    wmItems = [];
    $('wmAdjust').value = '';
    $('wmProgress').hidden = true;
    renderWatermarkPanel();
  }
  async function stageWatermarkFiles(files) {
    clearWatermarkItems();
    setFieldText('photoProcessStatus', `正在读取 ${files.length} 张照片的 EXIF 拍摄时间…`);
    const items = [];
    let index = 0;
    for (const file of files) {
      index++;
      const exif = await readPhotoExifDate(file);
      let url;
      try {
        if (isHeif(file)) setFieldText('photoProcessStatus', `正在转换 HEIC 照片 ${index}/${files.length}…`);
        url = await decodableUrl(file);
      } catch (error) { notice(`照片 ${file.name || index} 无法读取：${error.message}`,true); continue; }
      items.push({id:key(), file, exif, url, state:'pending'});
    }
    items.sort((a, b) => (a.exif ? a.exif.getTime() : Infinity) - (b.exif ? b.exif.getTime() : Infinity));
    wmItems = items;
    const base = wmBase();
    $('wmAdjust').value = base ? localInput(base) : localInput(new Date());
    setFieldText('photoProcessStatus', `已选择 ${items.length} 张照片，请确认水印时间后上传。`);
    renderWatermarkPanel();
  }
  const wmBase = () => wmItems.find(item => item.exif)?.exif || null;
  const wmManual = () => wmItems.length === 1 && !wmItems[0].exif;
  function wmOffsetMs() {
    const base = wmBase(), adjusted = new Date($('wmAdjust').value);
    if (!base || Number.isNaN(adjusted.getTime())) return 0;
    const baseMinute = new Date(base); baseMinute.setSeconds(0, 0);
    return adjusted.getTime() - baseMinute.getTime();
  }
  function offsetText(ms) {
    if (!ms) return '时间不调整';
    const minutes = Math.round(Math.abs(ms) / 60000);
    return `整体${ms < 0 ? '提前' : '推后'} ${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
  }
  function renderWatermarkPanel() {
    $('wmPanel').hidden = !uploadWatermark || !wmItems.length;
    if ($('wmPanel').hidden) return;
    const base = wmBase(), manual = wmManual(), offset = wmOffsetMs();
    const missing = manual ? 0 : wmItems.filter(item => !item.exif).length;
    setFieldText('wmCount', `· 已选 ${wmItems.length} 张`);
    setFieldText('wmBaseLabel', manual ? '该照片没有 EXIF 拍摄时间' : '基准照片（最早 EXIF 拍摄时间）');
    setFieldText('wmBaseTime', base ? localText(base) : '请手动输入水印时间');
    setFieldText('wmAdjustLabel', manual ? '水印时间' : '调整为');
    $('wmAdjust').disabled = wmBusy || (!base && !manual);
    $('wmOffset').parentElement.hidden = manual || !base;
    setFieldText('wmOffset', offsetText(offset));
    const grid = $('wmGrid'); grid.replaceChildren();
    wmItems.forEach((item, index) => {
      const card = document.createElement('div');
      card.className = 'wm-thumb' + (!item.exif && !manual ? ' no-exif' : '');
      card.dataset.state = item.state;
      const img = document.createElement('img'); img.src = item.url; img.alt = item.file.name || ''; img.loading = 'lazy';
      card.append(img);
      if (index === 0 && base && item.exif) card.append(textNode('span', '基准', 'wm-tag'));
      if (item.state !== 'pending') card.append(textNode('span', item.state === 'done' ? '✓' : item.state === 'error' ? '!' : '…', 'wm-state'));
      const meta = document.createElement('div'); meta.className = 'wm-meta';
      if (item.exif) {
        meta.append(textNode('span','原始 '), textNode('s', shortText(item.exif)), textNode('span',' → '), textNode('b', shortText(new Date(item.exif.getTime() + offset))));
      } else if (manual) {
        const value = new Date($('wmAdjust').value);
        meta.append(textNode('span','无 EXIF · 水印 '), textNode('b', Number.isNaN(value.getTime()) ? '-' : shortText(value)));
      } else {
        meta.append(textNode('span','⚠ 无 EXIF 时间'));
        const remove = textNode('button','剔除','wm-remove'); remove.type = 'button'; remove.disabled = wmBusy;
        remove.addEventListener('click', () => { URL.revokeObjectURL(item.url); wmItems = wmItems.filter(other => other !== item); if (!wmItems.length) clearWatermarkItems(); else renderWatermarkPanel(); });
        meta.append(remove);
      }
      card.append(meta); grid.append(card);
    });
    $('wmWarn').hidden = !missing;
    if (missing) setFieldText('wmWarn', `还有 ${missing} 张照片缺少 EXIF 时间，请先剔除`);
    const ready = wmItems.length - missing;
    const validTime = !Number.isNaN(new Date($('wmAdjust').value).getTime());
    $('wmUpload').disabled = wmBusy || Boolean(missing) || !ready || !validTime;
    setFieldText('wmUpload', wmBusy ? '上传中，请勿关闭页面' : `上传 ${ready} 张并生成水印`);
  }
  function setWatermarkProgress(text, done, total) {
    $('wmProgress').hidden = false;
    setFieldText('wmProgressText', text);
    setFieldText('wmProgressCount', total ? `${done} / ${total}` : '');
    $('wmBar').style.width = total ? `${Math.round(done / total * 100)}%` : '100%';
  }
  async function uploadWatermarkItems() {
    if (wmBusy || taking || processingFiles || syncing) return;
    if (!timeAuthorized) { notice('请先验证水印时间调整密码。',true); return; }
    if (!UPLOAD_TYPES.includes(batch?.type)) { notice('请先选择照片类型。',true); return; }
    const manual = wmManual();
    if (!manual && wmItems.some(item => !item.exif)) { notice('还有照片缺少 EXIF 时间，请先剔除。',true); return; }
    const adjusted = new Date($('wmAdjust').value);
    if (Number.isNaN(adjusted.getTime())) { notice('请选择有效的水印时间。',true); $('wmAdjust').focus(); return; }
    if (batch.type === 'equipment' && !deviceSession && !confirmDevice()) return;
    const selected = chosenOrder();
    if (!selected) { notice('请先在上方选择工单。',true); return; }
    const items = wmItems.slice(), offset = manual ? 0 : wmOffsetMs();
    wmBusy = true; taking = true; processingFiles = true; processingTotal = items.length; processingDone = 0;
    refreshUploadControls(); renderWatermarkPanel();
    let saved = 0;
    try {
      setWatermarkProgress('正在准备照片…', 0, items.length);
      const baseContext = await makeContext('file', {order:{...selected}, userId:profile.user.id, watermarkSource:'system'});
      if (baseContext) baseContext.watermark_source = 'system';
      for (const item of baseContext ? items : []) {
        item.state = 'processing'; renderWatermarkPanel();
        setWatermarkProgress('正在生成水印…', processingDone, items.length);
        try {
          const shot = manual ? new Date() : item.exif;
          const watermark = manual ? new Date(adjusted.getTime() + Math.floor(Math.random() * 60) * 1000) : new Date(item.exif.getTime() + offset);
          const context = {...baseContext, client_id:key(), captured_at:shot.toISOString(), watermark_at:watermark.toISOString(),
            watermark_offset_minutes:Math.round(offset / 60000)};
          const image = new Image(); image.src = item.url; await image.decode();
          await keepCapture(image, context);
          item.state = 'done'; saved++;
        } catch (error) { item.state = 'error'; notice(`照片 ${item.file.name || processingDone + 1} 未保存：${error.message}。`,true); }
        processingDone++;
        renderWatermarkPanel();
      }
      if (saved) setWatermarkProgress('已生成水印，正在上传…', saved, items.length);
    } catch (error) {
      notice('照片未保存：'+error.message+'。请保留原照片后重试。',true);
    } finally {
      taking = false; processingFiles = false; processingTotal = 0; processingDone = 0;
    }
    try {
      if (saved) {
        await completeBatch();
        const remaining = batch ? (await queued(batch.id)).length : 0;
        if (wmItems.length) setWatermarkProgress(remaining ? `已上传 ${saved - remaining} 张，${remaining} 张待重试` : `已上传 ${saved} 张并生成水印`, saved - remaining, saved);
      }
    } finally {
      wmBusy = false;
      // 已保存为草稿的照片不再留在面板中，避免重复生成；未上传的草稿用「重试上传」继续。
      for (const item of items) if (item.state === 'done') URL.revokeObjectURL(item.url);
      wmItems = wmItems.filter(item => item.state !== 'done');
      for (const item of wmItems) item.state = 'pending';
      if (!wmItems.length) $('wmProgress').hidden = true;
      refreshUploadControls(); renderWatermarkPanel(); await renderQueue();
    }
  }
  async function makeContext(source, lockedSelection = null) {
    const resolved = chosenOrder();
    if (!identityReady || !profile?.can_capture || !resolved) throw new Error('请先在上方选择工单。');
    const selected = lockedSelection?.order || cameraSelection?.order || {...resolved};
    if (source === 'file' && lockedSelection?.watermarkSource === 'original') {
      if (!batch || lockedSelection.userId !== profile.user.id) throw new Error('照片功能尚未准备好，请稍后重试。');
      if (!UPLOAD_TYPES.includes(batch.type)) throw new Error('请先选择照片类型。');
      const originalDevice = batch.type === 'equipment' ? (deviceSession ? {...deviceSession} : null) : null;
      if (batch.type === 'equipment' && !originalDevice) throw new Error('请先确认设备信息。');
      const originalTech = profile.technicians?.find(person => String(person.id) === $('technicianSelect').value) || {id:profile.user.id, name:profile.user.name};
      const actual = new Date().toISOString();
      const timezoneName = $('timezoneName').value.trim() || 'UTC';
      new Intl.DateTimeFormat('en', {timeZone:timezoneName}).format();
      return {client_id:key(),user_id:profile.user.id,employee_name:originalTech.name,technician_user_id:originalTech.id,
        order_id:selected.id,order_number:selected.order_number,site_name:selected.client_name,site_address:selected.site_address,
        captured_at:actual,watermark_at:actual,batch_id:batch.id,photo_type:batch.type,timezone_name:timezoneName,
        latitude:0,longitude:0,accuracy:100000,location_verified:false,note:'',
        location_note:'原图已有水印，未检查拍摄位置。',source,error:'',
        ...(originalDevice ? {equipment_number:originalDevice.equipment_number,position_number:originalDevice.position_number,
        container_number:originalDevice.container_number,equipment_session:originalDevice.id,repair_phase:originalDevice.repair_phase || '',no_equipment_number:originalDevice.no_equipment_number} : {})};
    }
    if (!batch) throw new Error('请先确认本组照片类型。');
    if (batch.type === 'general') throw new Error('请先选择进场、离场或自检照片。');
    // Freeze the shutter-time metadata before asynchronous location checks.
    const photoType = batch.type, captureBatchId = batch.id;
    const captureDevice = deviceSession ? {...deviceSession} : null;
    if (photoType === 'equipment' && !captureDevice) throw new Error('请先确认本组照片类型和设备信息。');
    const selectedId = selected.id, selectedUser = lockedSelection?.userId || cameraSelection?.userId || profile.user.id;
    const keepsOriginalWatermark = source === 'file' && lockedSelection?.watermarkSource === 'original';
    // Current phone location does not prove where an imported photo was taken:
    // every selected-file upload (keep original or generate watermark) skips the location check.
    const skipsLocation = source === 'file';
    if (!skipsLocation) {
      await locate();
      if (!(await checkLocation(true, selected))) return null;
    }
    if (!identityReady || profile?.user.id !== selectedUser) throw new Error('账号已改变，请重新拍照。');
    const timezoneName = $('timezoneName').value.trim() || 'UTC';
    new Intl.DateTimeFormat('en', {timeZone:timezoneName}).format();
    localStorage.setItem('field-timezone-' + profile.user.id, timezoneName);
    const actual = new Date();
    let watermark;
    if (useSystemTime) {
      watermark = actual;
    } else {
      if (batch.first_adjusted_shot_at == null) batch.first_adjusted_shot_at = actual.getTime();
      const anchorMs = batch.watermark_anchor_ms ?? new Date($('watermarkStart').value).getTime();
      watermark = new Date(anchorMs + (actual.getTime() - batch.first_adjusted_shot_at));
    }
    if (Number.isNaN(watermark.getTime())) throw new Error('请选择有效的水印开始时间。');
    const technician = profile.technicians.find(person => String(person.id) === $('technicianSelect').value);
    if (!technician) throw new Error('请选择施工员。');
    return {client_id:key(),user_id:profile.user.id,employee_name:technician.name,technician_user_id:technician.id,
      order_id:selected.id,order_number:selected.order_number,site_name:selected.client_name,site_address:selected.site_address,
      captured_at:actual.toISOString(),watermark_at:watermark.toISOString(),batch_id:captureBatchId,photo_type:photoType,timezone_name:timezoneName,
      latitude:skipsLocation ? 0 : position.latitude,longitude:skipsLocation ? 0 : position.longitude,accuracy:skipsLocation ? 100000 : position.accuracy,
      location_verified:!skipsLocation,note:$('photoNote').value.trim(),location_note:keepsOriginalWatermark ? '原图已有水印，未检查拍摄位置。' : skipsLocation ? '选图上传，未检查拍摄位置。' : locationNote,source,error:'',
      ...(photoType === 'equipment' ? {equipment_number:captureDevice.equipment_number,
      position_number:captureDevice.position_number,container_number:captureDevice.container_number,
      equipment_session:captureDevice.id,repair_phase:captureDevice.repair_phase || '',no_equipment_number:captureDevice.no_equipment_number} : {})};
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
    const stripSession = cameraStripSession;
    const photo = {...context,blob:await processedPhoto(source,context)};
    // Do not report success or clear the capture until the IDB transaction commits.
    await storePhoto(photo);
    if (stripSession && stripSession === cameraStripSession && photo.source === 'camera' && photo.batch_id === stripSession.batchId) stripSession.ids.add(photo.client_id);
    notice('照片已保存为本机草稿：'+photo.order_number+'。完成本组后再统一上传。');
    await renderQueue();
  }
  async function keepOriginalFile(file, context) {
    if (!context || context.user_id !== profile?.user.id || !identityReady) throw new Error('账号已改变，请重新选择照片。');
    const bytes = await file.arrayBuffer();
    if (!bytes.byteLength || bytes.byteLength !== file.size) throw new Error('无法完整读取照片，请从相册重新选择');
    const photo = {...context, blob:new Blob([bytes], {type:file.type || 'application/octet-stream'}), original_filename:file.name || '', content_type:file.type || 'application/octet-stream'};
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
    if (!UPLOAD_TYPES.includes(batch?.type)) { reject('请先选择照片类型：进场、离场、自检或设备。'); return; }
    if (wmBusy) { reject('正在上传，请稍候。'); return; }
    if (batch.type === 'equipment' && !activeRepair) { reject(repairStage === 'after' ? '请先选择维修记录。' : '请先保存维修前状态。'); return; }
    if (batch.type === 'equipment' && !deviceSession && !confirmDevice()) { event.preventDefault(); captureContext = null; return; }
    if (uploadWatermark && !timeAuthorized) { reject('请先验证水印时间调整密码。'); return; }
    captureContext = {order:{...selected}, userId:profile.user.id, watermarkSource:uploadWatermark ? 'system' : 'original'};
  });
  $('photoFile').addEventListener('change', async () => {
    const files = Array.from($('photoFile').files || []), selection = captureContext; captureContext = null;
    if (!files.length || !selection) return;
    if (selection.watermarkSource === 'system') { $('photoFile').value = ''; await stageWatermarkFiles(files); return; }
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
    clearRecentCaptures();
    queueRenderGeneration++;
    for (const view of queueViews.values()) URL.revokeObjectURL(view.url);
    queueViews.clear();
    $('queueList').replaceChildren();
  }
  // Only photos saved during this camera opening and in its locked batch belong here.
  const recentCaptureViews = new Map();
  function clearRecentCaptures() {
    for (const view of recentCaptureViews.values()) URL.revokeObjectURL(view.url);
    recentCaptureViews.clear();
    $('recentCaptures').replaceChildren();
    $('recentCaptures').hidden = true;
  }
  function renderRecentCaptures(photos) {
    if (!cameraStripSession) { clearRecentCaptures(); return; }
    photos = photos.filter(photo => cameraStripSession.ids.has(photo.client_id) && photo.batch_id === cameraStripSession.batchId);
    const pending = new Map(photos.map(photo => [photo.client_id, photo]));
    for (const [id, view] of recentCaptureViews) {
      if (view.photo.user_id !== profile?.user.id || (!pending.has(id) && !view.uploaded)) {
        URL.revokeObjectURL(view.url); recentCaptureViews.delete(id);
      }
    }
    for (const photo of photos.filter(photo => photo.source === 'camera')) {
      if (!recentCaptureViews.has(photo.client_id)) {
        recentCaptureViews.set(photo.client_id, {photo, url:URL.createObjectURL(photo.blob), uploaded:false});
      } else recentCaptureViews.get(photo.client_id).photo = photo;
    }
    const ordered = [...recentCaptureViews.entries()].sort((a,b) => a[1].photo.captured_at.localeCompare(b[1].photo.captured_at));
    const strip = $('recentCaptures'), previousLast = strip.lastElementChild?.dataset.photoId, previousScroll = strip.scrollLeft;
    strip.replaceChildren();
    const orderId = chosenOrder()?.id;
    for (const [id, view] of ordered) {
      if (String(view.photo.order_id) !== String(orderId)) continue;
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'recent-capture'; button.dataset.photoId = id;
      const image = document.createElement('img'); image.src = view.url; image.alt = fieldText('照片预览');
      const status = textNode('span', view.uploaded ? '已上传' : uploadingPhotoId === id ? '正在上传' : view.photo.error ? '上传失败' : '本机草稿');
      status.textContent = fieldText(status.textContent);
      button.title = fieldText(view.photo.error || status.textContent);
      button.append(image, status);
      image.onerror = () => { image.hidden = true; };
      button.addEventListener('click', () => openDraft(view.photo, view.uploaded));
      strip.append(button);
    }
    strip.hidden = !strip.children.length;
    strip.scrollLeft = previousLast !== strip.lastElementChild?.dataset.photoId ? strip.scrollWidth : previousScroll;
  }
  async function renderQueue() {
    const generation = ++queueRenderGeneration;
    try {
      const photos = await queued();
      if (generation !== queueRenderGeneration) return;
      renderRecentCaptures(photos);
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
        update(view.title,photoTypeLabel(photo.photo_type)+' · '+(photo.photo_type==='equipment' ? (photo.equipment_number||'N/A')+' · ' : '')+photo.order_number);
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
  function openDraft(photo, uploaded = false) {
    $('deleteDraft').hidden = uploaded;
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
      if (!sessionResponse.ok) throw new Error('登录状态检查失败（HTTP '+sessionResponse.status+'），请稍后重试。');
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
          // Rebuild legacy IDB File/Blob records from readable bytes before multipart encoding.
          let uploadBytes;
          try {
            uploadBytes = await photo.blob.arrayBuffer();
            if (!uploadBytes.byteLength || uploadBytes.byteLength !== photo.blob.size) throw new Error('incomplete');
          } catch (_) {
            throw new Error('无法读取本机照片文件，请从相册重新选择这张照片。原草稿仍保留。');
          }
          photo.blob = new Blob([uploadBytes], {type:photo.blob.type || photo.content_type || 'application/octet-stream'});
          photo.error = '';
          await storePhoto(photo);
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
          if (!response.ok || !result.ok) throw new Error((result.error || '上传未成功，照片仍保留')+' [HTTP '+response.status+' / '+(response.headers.get('X-Field-Version') || '未知服务版本')+']');
          await storePhoto(photo,true);
          const recent = recentCaptureViews.get(photo.client_id);
          if (recent) { recent.uploaded = true; recent.photo = photo; }
          notice('照片已上传到 '+photo.order_number+'，系统已按拍摄日期归档。');
        } catch(error) {
          photo.error = error.message; await storePhoto(photo);
          if (!navigator.onLine || error instanceof TypeError) break;
        }
        uploadingPhotoId = null;
        await renderQueue();
      }
    } catch(error) { notice('暂时无法上传：'+error.message+' 照片仍保留在本机。',true); }
    finally { syncing = false; uploadingPhotoId = null; await renderQueue(); }
  }
  async function completeBatch() {
    if (!batch) return;
    const id=batch.id; await syncQueue(id);
    if ((await queued(id)).length) { notice('部分照片尚未上传，请检查网络后重试。',true); return; }
    stopCamera(); batch=null; deviceSession=null; timeAuthorized=false; useSystemTime=true; $('timeSettings').hidden=true; $('deviceSession').hidden=true; $('watermarkModeText').textContent='使用当前系统时间'; $('watermarkStart').value=''; $('watermarkPassword').value=''; setFieldText('passwordStatus',''); if ($('watermarkTimeDialog').open) $('watermarkTimeDialog').close(); if ($('watermarkPasswordDialog').open) $('watermarkPasswordDialog').close();
    clearDeviceInputs();  // 本组结束：清空设备输入框，下一组不得残留本组设备信息
    setKindChoiceActive(null); setFieldText('kindStatus','请选择下一组照片类型。'); $('photoNote').value=''; setUploadWatermark(false);
    notice('本组照片已全部上传，请选择下一组照片类型。'); await renderQueue();
  }
  async function loadLedger() {
    if (!identityReady) return;
    $('photoBatchDownload').hidden = true;
    const params = new URLSearchParams(new FormData($('ledgerFilter')));
    $('exportLedger').href = '/api/field/photos.xlsx?'+params;
    $('repairReport').href = '/reports/field-repairs?'+params;
    try {
      const response = await requestAPI('/api/field/photos?'+params,{cache:'no-store'});
      if (response.status === 401 || response.status === 403) { lock('请重新登录后查询台账。'); return; }
      if (!response.ok) throw new Error('查询失败');
      const result = await response.json(); ledgerPhotos = result.rows; $('ledgerList').replaceChildren();
      $('photoBatchDownload').dataset.photoQuery = params.toString();
      $('photoBatchDownload').querySelector('[data-photo-zip]').href = '/api/field/photos.zip?'+params;
      $('photoBatchDownload').hidden = !result.rows.length || result.truncated;
      const table = document.createElement('table'); table.className = 'ledger-table';
      const thead = document.createElement('thead'), headerRow = document.createElement('tr');
      ['照片','照片类型','工单','客户','站点','铭牌号','位置号','集装箱号','施工员','实际拍摄账号','拍摄时间','接收时间','水印','现场位置','备注','来源'].forEach(label => headerRow.append(textNode('th',fieldText(label))));
      thead.append(headerRow); const tbody = document.createElement('tbody'); table.append(thead,tbody); $('ledgerList').append(table);
      result.rows.forEach((photo, photoIndex) => {
        const captureTime=window.formatPhotoTime(photo.captured_at,photo.timezone_name);
        const receiveTime=window.formatPhotoTime(photo.received_at,photo.timezone_name);
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
        const deviceDetail = ['铭牌号：'+(photo.equipment_number || '无'), photo.equipment_model ? '型号：'+photo.equipment_model : '', photo.position_number ? '位置号：'+photo.position_number : '', photo.container_number ? '集装箱号：'+photo.container_number : '', photo.pump_fuse_numbers ? '水泵保险：'+photo.pump_fuse_numbers : ''].filter(Boolean).join(' · ');
        const watermarkDetail = photo.watermark_source === 'original' ? '水印：保留原图水印' : '水印：系统生成 · '+window.formatPhotoTime(photo.watermark_at||photo.captured_at,photo.timezone_name);
        detail.append(textNode('strong',photo.order_number+' · '+photo.site_name),textNode('p','照片类型：'+photoTypeLabel(photo.photo_type)),textNode('p',deviceDetail),textNode('p','拍摄：'+window.formatPhotoTime(photo.captured_at,photo.timezone_name)+' · '+watermarkDetail),textNode('p','施工员：'+(photo.technician_name||photo.employee_name)+' · 实际拍摄：'+photo.employee_name+' · 接收：'+receiveTime),textNode('p',photo.note),address,textNode('br',''),map,textNode('p',photo.source === 'camera' ? '现场相机':'系统相机 / 选图'));
        card.append(link,detail); $('ledgerList').append(card);
        const row = document.createElement('tr');
        const imageCell = document.createElement('td'), tableLink = link.cloneNode(false), tableImage = image.cloneNode(false);
        tableLink.append(tableImage); tableLink.addEventListener('click',event=>{event.preventDefault();openLedgerPhoto(photoIndex);}); imageCell.append(tableLink); row.append(imageCell);
        const values = [photoTypeLabel(photo.photo_type), photo.order_number, photo.customer_name||'—', photo.site_name, photo.equipment_number||'—',photo.position_number||'—',photo.container_number||'—',
          photo.technician_name||photo.employee_name,photo.employee_name,
          captureTime,receiveTime, watermarkDetail,
          (photo.site_address||photo.site_name)+(photo.location_verified ? `\n${Number(photo.latitude).toFixed(5)}, ${Number(photo.longitude).toFixed(5)}` : '\n未检查坐标'),
          photo.note || '', photo.source === 'camera' ? '现场相机':'系统相机 / 选图'];
        values.forEach((value,index) => { const cell=document.createElement('td'); cell.textContent=fieldText(value);
          if(index===10 && photo.location_verified){const locationLink=document.createElement('a');locationLink.href=`https://www.google.com/maps?q=${photo.latitude},${photo.longitude}`;locationLink.target='_blank';locationLink.rel='noopener';locationLink.textContent=cell.textContent;cell.replaceChildren(locationLink);} row.append(cell); });
        tbody.append(row);
      });
    } catch(error) { setFieldText('ledgerList', '台账需要联网查看。待上传照片请到“拍照”页面查看。'); }
  }
  function openLedgerPhoto(index) {
    if (!ledgerPhotos.length) return;
    ledgerPhotoIndex = (index + ledgerPhotos.length) % ledgerPhotos.length;
    const photo = ledgerPhotos[ledgerPhotoIndex];
    $('ledgerPhotoImage').src = photo.preview;
    setImageAttachmentPreviewFit();
    $('ledgerPhotoTitle').textContent = photo.order_number+' · '+photo.site_name;
    $('ledgerPhotoCounter').textContent = `${ledgerPhotoIndex + 1} / ${ledgerPhotos.length}`;
    $('ledgerPhotoDetail').textContent = [photo.repair_phase === 'before' ? '维修前' : photo.repair_phase === 'after' ? '维修后' : '', photo.equipment_model ? '型号：'+photo.equipment_model : '', photo.equipment_number ? '铭牌号：'+photo.equipment_number : '', photo.position_number ? '位置号：'+photo.position_number : '', photo.container_number ? '集装箱号：'+photo.container_number : '', '拍摄：'+window.formatPhotoTime(photo.captured_at,photo.timezone_name), '施工员：'+(photo.technician_name||photo.employee_name), photo.note||''].filter(Boolean).map(fieldText).join(' · ');
    const typeSelect = $('ledgerPhotoTypeSelect');
    typeSelect.replaceChildren();
    const currentType = photo.photo_type;
    if (!['equipment','arrival','departure','safety'].includes(currentType)) {
      const placeholder = document.createElement('option');
      placeholder.value = ''; placeholder.disabled = true; placeholder.selected = true;
      placeholder.textContent = fieldText(photoTypeLabel(currentType) || '未分类');
      typeSelect.append(placeholder);
    }
    ['equipment','arrival','departure','safety'].forEach(t => {
      const option = document.createElement('option');
      option.value = t; option.textContent = fieldText(photoTypeLabel(t));
      if (t === currentType) option.selected = true;
      typeSelect.append(option);
    });
    $('ledgerPhotoTypeEdit').hidden = false;
    $('saveLedgerPhotoType').disabled = false;
    $('deleteLedgerPhoto').hidden = !photo.can_delete;
    $('deleteLedgerPhoto').disabled = false;
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
  function showRepair(repair, phase = 'before') {
    activeRepair = repair ? {...repair, repair_phase:phase} : null;
    repairStartId = null;
    deviceSession = null;
    $('repairResultFields').hidden = repairStage !== 'after' || !repair;
    $('repairOriginalFault').textContent = repair?.fault || '无已记录故障';
    $('startRepair').disabled = Boolean(repair);
    $('scanRepairFault').disabled = Boolean(repair);
    for (const id of ['equipmentNumber','equipmentModel','positionNumber','containerNumber','repairFault']) {
      $(id).readOnly = Boolean(repair);
    }
    $('noEquipmentNumber').disabled = Boolean(repair);
    $('recognizeDevice').disabled = Boolean(repair);
    $('repairStage').textContent = phase === 'before' ? '当前拍摄：维修前' : '当前拍摄：维修后';
    $('completeRepair').disabled = phase === 'before';
    $('repairWork').value = '';
    $('repairResult').value = '';
    if (repair && phase === 'before') $('repairFault').value = repair.fault || '';
    if (repair) {
      $('equipmentNumber').closest('fieldset').classList.remove('locked');
      $('equipmentNumber').value = repair.equipment_number;
      $('equipmentModel').value = repair.equipment_model;
      $('positionNumber').value = repair.position_number;
      $('containerNumber').value = repair.container_number;
      $('noEquipmentNumber').checked = !repair.equipment_number;
      $('equipmentNumber').disabled = false;
    }
  }
  function clearFaultPhotos() {
    for (const photo of faultPhotos) URL.revokeObjectURL(photo.preview);
    faultPhotos = [];
    $('repairFaultPhotos').replaceChildren();
    $('repairFaultPhotos').hidden = true;
  }
  function selectRepairStage(stage) {
    if (taking || processingFiles || captureContext || syncing || ocrBusy) return false;
    if (stage !== repairStage && faultPhotos.length) {
      notice('请先保存已拍摄的维修前照片，或点清除后再切换。', true);
      return false;
    }
    stopCamera();
    repairStage = stage;
    showRepair(null);
    $('pendingRepairs').value = '';
    for (const [id, current] of [['repairBefore','before'], ['repairAfter','after']]) {
      $(id).classList.toggle('primary', stage === current);
      $(id).setAttribute('aria-pressed', String(stage === current));
    }
    $('repairControls').hidden = stage !== 'before';
    $('repairPending').hidden = stage !== 'after';
    if (stage === 'after') refreshPendingRepairs().catch(error=>notice(error.message,true));
    return true;
  }
  $('repairBefore').addEventListener('click', () => { if (repairStage !== 'before' && selectRepairStage('before')) resetDevice(); });
  $('repairAfter').addEventListener('click', () => { if (repairStage !== 'after') selectRepairStage('after'); });
  $('repairAfterPhoto').addEventListener('click', () => { setCaptureMode('camera'); openCamera(); });
  async function repairRequest(order, body) {
    const options = body ? {method:'POST', headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf},
      body:JSON.stringify(body)} : {};
    const response = await requestAPI('/api/field/orders/'+order.id+'/repair-records', options);
    const result = await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(result.error || '无法保存或读取维修记录，请检查网络。');
    return result;
  }
  async function refreshPendingRepairs() {
    const order = chosenOrder();
    const selected = activeRepair?.repair_phase === 'after' ? activeRepair.id : '';
    pendingRepairRows = [];
    $('pendingRepairs').replaceChildren(new Option('选择维修记录',''));
    if (!order || !profile) return;
    const result = await repairRequest(order);
    if (chosenOrder()?.id !== order.id) return;
    pendingRepairRows = result.rows;
    for (const row of pendingRepairRows) {
      $('pendingRepairs').add(new Option([row.position_number,row.equipment_number,row.container_number].filter(Boolean).join(' · ') || row.id.slice(0,8),row.id));
    }
    if (selected && pendingRepairRows.some(row => row.id === selected)) $('pendingRepairs').value = selected;
    else if (selected && activeRepair?.id === selected) { showRepair(null); resetDevice(); }
  }
  $('loadPendingRepairs').addEventListener('click', () => refreshPendingRepairs().catch(error=>notice(error.message,true)));
  $('pendingRepairs').addEventListener('change', () => {
    if (taking || processingFiles || captureContext || syncing) return;
    const repair = pendingRepairRows.find(row=>row.id === $('pendingRepairs').value);
    stopCamera();
    showRepair(repair ? {...repair,order_id:chosenOrder().id} : null, 'after');
    if (!repair) resetDevice();
  });
  $('startRepair').addEventListener('click', async () => {
    const order = chosenOrder();
    if (!order || !profile || activeRepair || taking || processingFiles || captureContext || syncing) return;
    const repair = {id:repairStartId || (repairStartId = key()), order_id:order.id,
      equipment_number:$('equipmentNumber').value.trim(), equipment_model:$('equipmentModel').value.trim(),
      position_number:$('positionNumber').value.trim(), container_number:$('containerNumber').value.trim(),
      fault:$('repairFault').value.trim(), no_equipment_number:$('noEquipmentNumber').checked};
    if (!repair.fault) { notice('请先记录故障现象。',true); return; }
    const button = $('startRepair'); button.disabled = true;
    try {
      await repairRequest(order, repair);
      if (chosenOrder()?.id !== order.id) return;
      stopCamera();
      for (const photo of faultPhotos) {
        await keepCapture(photo.canvas, {...photo.context, equipment_session:repair.id,
          equipment_number:repair.equipment_number, no_equipment_number:repair.no_equipment_number,
          position_number:repair.position_number, container_number:repair.container_number,
          repair_phase:'before', watermark_source:'system'});
      }
      clearFaultPhotos();
      showRepair(repair);
      await syncQueue();
      notice('已保存维修前状态。可继续拍摄维修前照片。');
      await refreshPendingRepairs();
    } catch(error) { notice(error.message,true); }
    finally { button.disabled = Boolean(activeRepair); }
  });
  $('completeRepair').addEventListener('click', async () => {
    const order = chosenOrder(), repair = activeRepair;
    if (!order || !repair || taking || processingFiles || captureContext || syncing) return;
    const work = $('repairWork').value.trim(), result = $('repairResult').value.trim();
    if (!work) { notice('请填写本次工作内容。',true); return; }
    const button = $('completeRepair'); button.disabled = true;
    try {
      await syncQueue();
      await repairRequest(order, {id:repair.id, action:'complete', work_content:work, result});
      if (activeRepair?.id !== repair.id) return;
      stopCamera(); showRepair(null); resetDevice(); $('repairFault').value='';
      notice('已保存维修结果。');
      await refreshPendingRepairs();
    } catch(error) { notice(error.message,true); }
    finally { button.disabled = activeRepair?.repair_phase !== 'after'; }
  });
  $('scanRepairFault').addEventListener('click', () => openCamera(true, true));
  $('readRepairFault').addEventListener('click', () => recognizeDevice(false, true));
  $('recognizeDevice').addEventListener('click',() => recognizeDevice(false));
  $('confirmRecognizedNumber').addEventListener('click', () => {
    const value = $('recognizedNumber').textContent.trim();
    if (value) {
      $('equipmentNumber').disabled = false; $('noEquipmentNumber').checked = false;
      $('equipmentNumber').value = value;
    }
    const model = $('recognizedModel').textContent.trim();
    if (model) $('equipmentModel').value = model;
    deviceSession = null; $('recognitionDialog').close();
    notice(value && model ? '已填入设备编号和型号，请核对。' : '已填入识别结果，请补充另一项。');
  });
  $('retryRecognition').addEventListener('click', async () => { $('recognitionDialog').close(); await openCamera(true); if (stream) notice('请拍全铭牌，系统会识别设备编号和型号。'); });
  $('manualRecognition').addEventListener('click', () => { $('recognitionDialog').close(); $('equipmentNumber').focus(); });
  $('saveLedgerPhotoType').addEventListener('click', async () => {
    const photo = ledgerPhotos[ledgerPhotoIndex];
    const select = $('ledgerPhotoTypeSelect');
    const value = select.value;
    if (!photo || !photo.id || !value) return;
    const button = $('saveLedgerPhotoType'); button.disabled = true;
    try {
      const payload = {};
      payload.photo_type = value;
      const response = await requestAPI('/api/field/photos/' + photo.id + '/type', {method:'POST', headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf}, body:JSON.stringify(payload)});
      if (response.status === 401 || response.status === 403) { notice('没有权限修改照片类型。', true); return; }
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || '保存失败，请稍后重试。'); }
      const data = await response.json();
      photo.photo_type = data.photo_type;
      notice('照片类型已更新为「' + (data.photo_type_label || photoTypeLabel(data.photo_type)) + '」。');
      openLedgerPhoto(ledgerPhotoIndex);
      loadLedger();
    } catch (error) { notice(error.message || '保存失败，请稍后重试。', true); }
    finally { button.disabled = false; }
  });
  $('deleteLedgerPhoto').addEventListener('click', async () => {
    const photo = ledgerPhotos[ledgerPhotoIndex];
    if (!photo || !photo.can_delete || !photo.delete_token) return;
    if (!window.confirm('确定删除这张照片吗？删除后不可恢复。')) return;
    const button = $('deleteLedgerPhoto'); button.disabled = true;
    try {
      const payload = {};
      payload.token = photo.delete_token;
      const response = await requestAPI('/api/field/photos/delete', {method:'POST', headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf}, body:JSON.stringify(payload)});
      if (response.status === 401 || response.status === 403) { notice('没有权限删除照片。', true); return; }
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || '删除失败，请稍后重试。'); }
      ledgerPhotos.splice(ledgerPhotoIndex, 1);
      notice('照片已删除。');
      if (!ledgerPhotos.length) { $('ledgerPhotoDialog').close(); }
      else { openLedgerPhoto(ledgerPhotoIndex); }
      loadLedger();
    } catch (error) { notice(error.message || '删除失败，请稍后重试。', true); }
    finally { button.disabled = false; }
  });
  $('nextDevice').addEventListener('click', async () => { if(batch && (await queued(batch.id)).length){notice('请先完成上传或删除当前组照片，再清除设备信息。',true);return;} if(taking || processingFiles || captureContext || syncing) return; if(faultPhotos.length && !window.confirm('清除尚未保存的维修前照片？')) return; stopCamera(); showRepair(null); clearFaultPhotos(); $('repairFault').value=''; resetDevice(); $('photoNote').value=''; $('photoNote').style.height=''; $('equipmentNumber').focus(); });
  $('noEquipmentNumber').addEventListener('change', () => { $('equipmentNumber').disabled = $('noEquipmentNumber').checked; $('equipmentNumber').required = !$('noEquipmentNumber').checked; if ($('noEquipmentNumber').checked) $('equipmentNumber').value=''; deviceSession=null; });
  $('photoNote').addEventListener('input', () => { $('photoNote').style.height='auto'; $('photoNote').style.height=Math.max(44,$('photoNote').scrollHeight)+'px'; });
  $('equipmentNumber').addEventListener('input', () => { deviceSession=null; setFieldText('deviceStatus','编号已修改，请点击“打开相机”确认。'); });
  $('positionNumber').addEventListener('input', () => { deviceSession=null; setFieldText('deviceStatus','位置号已修改，请点击“打开相机”确认。'); });
  $('containerNumber').addEventListener('input', () => { deviceSession=null; setFieldText('deviceStatus','集装箱号已修改，请点击“打开相机”确认。'); });
  $('orderSelect').addEventListener('input', () => chooseOrder($('orderSelect').value));
  $('orderSelect').addEventListener('change', () => chooseOrder($('orderSelect').value));
  $('orderSearch').addEventListener('input',renderOrders);
  $('refreshLocation').addEventListener('click', () => locate().catch(error => notice(error.message,true)));
  $('cameraMode').addEventListener('click', () => setCaptureMode('camera'));
  $('uploadMode').addEventListener('click', () => setCaptureMode('upload'));
  $('uploadKeepMode').addEventListener('click', () => { if (!wmBusy) setUploadWatermark(false); });
  $('uploadWatermarkMode').addEventListener('click', () => {
    if (wmBusy || taking || processingFiles) return;
    if (timeAuthorized) { setUploadWatermark(true); return; }
    pendingUploadWatermark = true;
    $('watermarkPassword').value = '';
    setFieldText('passwordStatus','请输入水印时间调整密码。');
    $('watermarkPasswordDialog').showModal();
    $('watermarkPassword').focus();
  });
  $('wmAdjust').addEventListener('input', renderWatermarkPanel);
  $('wmClear').addEventListener('click', () => { if (!wmBusy) clearWatermarkItems(); });
  $('wmUpload').addEventListener('click', uploadWatermarkItems);
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
  $('equipmentKind').addEventListener('click',()=>chooseKind('equipment'));
  $('arrivalKind').addEventListener('click',()=>chooseKind('arrival')); $('departureKind').addEventListener('click',()=>chooseKind('departure')); $('safetyKind').addEventListener('click',()=>chooseKind('safety'));
  $('adjustWatermarkTime').addEventListener('click',openWatermarkDialog);
  $('verifyTimePassword').addEventListener('click',verifyTimePassword);
  $('confirmWatermarkTime').addEventListener('click',confirmWatermarkTime);
  $('watermarkPasswordForm').addEventListener('submit',event=>{event.preventDefault();verifyTimePassword();});
  $('cancelWatermarkPassword').addEventListener('click',cancelWatermarkPassword);
  $('watermarkPasswordDialog').addEventListener('cancel',event=>{event.preventDefault();cancelWatermarkPassword();});
  $('watermarkTimeForm').addEventListener('submit',event=>{event.preventDefault();});
  $('cancelWatermarkTime').addEventListener('click',cancelWatermarkTime);
  $('watermarkTimeDialog').addEventListener('cancel',event=>{event.preventDefault();cancelWatermarkTime();});
  $('completeBatch').addEventListener('click',completeBatch);
  $('closeDraft').addEventListener('click',()=>$('draftDialog').close());
  $('deleteDraft').addEventListener('click',async()=>{if(!draftSelection || $('deleteDraft').hidden)return;await storePhoto(draftSelection,true);draftSelection=null;$('draftDialog').close();await renderQueue();notice('已删除本机草稿照片。');});
  $('closeLedgerPhoto').addEventListener('click',()=>$('ledgerPhotoDialog').close());
  $('previousLedgerPhoto').addEventListener('click',()=>openLedgerPhoto(ledgerPhotoIndex-1));
  $('nextLedgerPhoto').addEventListener('click',()=>openLedgerPhoto(ledgerPhotoIndex+1));
  $('ledgerPhotoStage').addEventListener('touchstart',event=>{ledgerTouchStart=event.changedTouches[0].clientX;},{passive:true});
  $('ledgerPhotoStage').addEventListener('touchend',event=>{if(ledgerTouchStart===null)return;if(imageAttachmentPreviewMode!=='fit'){ledgerTouchStart=null;return;}const distance=event.changedTouches[0].clientX-ledgerTouchStart;ledgerTouchStart=null;if(Math.abs(distance)>45)openLedgerPhoto(ledgerPhotoIndex+(distance<0?1:-1));},{passive:true});
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
