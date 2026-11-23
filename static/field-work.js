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
  let cameraFacing = 'environment', switchingCamera = false;
  let repairStage = 'before', faultPhotos = [], nameplateBlob = null, faultBlob = null, nameplatePreview = null, nameplateDrawnUrl = '';
  let recognizeAbort = null, recognizeTimer = null, recognizeKind = 'nameplate', recognizeCancelled = false, captureLockSnapshot = null;
  const captureControls = ['recognizeDevice','scanRepairFault','openCamera','shutterButton','cameraAlbumButton','cameraBarAlbumButton','albumFileWithWatermark','albumFileWithoutWatermark','repairAfterPhoto','repairAlbumButton','takePhoto','readRepairFault'];
  let activeRepair = null, pendingRepairRows = [], repairStartId = null;
  let deviceSession = null, scanTimer = null, ocrTimer = null, ocrStartTimer = null, ocrBusy = false, detector = null;
  let batch = null, timeAuthorized = false, useSystemTime = true, draftSelection = null;
  let ledgerPhotos = [], ledgerPhotoIndex = 0, ledgerTouchStart = null;
  let captureMode = 'camera', deviceStatusText = '', groupTimeResolve = null;
  let uploadWatermark = false, pendingUploadWatermark = false, wmItems = [], wmBusy = false;
  let deletingDrafts = false, deleteDraftSnapshot = null;
  let cameraStripSession = null;
  const queueViews = new Map();
  let queueRenderGeneration = 0, uploadingPhotoId = null, uploadIndex = 0, uploadTotal = 0;
  let processingFiles = false, processingTotal = 0, processingDone = 0;
  const fieldText = value => window.fieldTranslate ? window.fieldTranslate(value) : value;
  const FAULT_TEXT_LIMIT = Number($('repairFault').dataset.maxChars) || 4000;
  function iso6346Ok(value) {
    const token = String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (!/^[A-Z]{4}\d{7}$/.test(token)) return false;
    const values = {};
    let next = 10;
    for (const letter of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') {
      values[letter] = next;
      next += 1;
      if (next % 11 === 0) next += 1;
    }
    let total = 0;
    for (let index = 0; index < 10; index += 1) {
      const character = token[index];
      total += (values[character] ?? Number(character)) * (1 << index);
    }
    const remainder = total % 11;
    return token[10] === (remainder === 10 ? '0' : String(remainder));
  }
  let photoRoleResolve = null, groupConfirmResolve = null;
  function limitFaultField() {
    const field = $('repairFault');
    // Rewriting value while an IME is composing cancels the Chinese character.
    if (field.dataset.composing === '1') return;
    if (field.value.length > FAULT_TEXT_LIMIT) field.value = field.value.slice(0, FAULT_TEXT_LIMIT);
    field.style.height = 'auto';
    field.style.height = Math.min(Math.max(field.scrollHeight, 136), 360) + 'px';
  }
  async function requestAPI(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeoutMs || (options.method === 'POST' ? 60000 : 15000));
    if (options.signal) {
      if (options.signal.aborted) controller.abort();
      else options.signal.addEventListener('abort', () => controller.abort(), {once:true});
    }
    const request = {...options};
    delete request.signal;
    delete request.timeoutMs;
    try {
      if (request.body instanceof FormData) {
        // Materialize multipart bytes once, including their matching boundary.
        // This avoids relying on a restored iOS File during fetch serialization.
        const encoded = new Response(request.body);
        const headers = new Headers(request.headers);
        headers.set('Content-Type', encoded.headers.get('Content-Type'));
        request.headers = headers;
        request.body = await encoded.blob();
      }
      return await fetch(url, {...request, signal:controller.signal});
    }
    finally { clearTimeout(timer); }
  }
  let noticeTimer = null;
  function clearDistanceNotice() {
    const el = $('notice');
    if (!el.classList.contains('distance-banner')) return;
    el.textContent = '';
    el.classList.remove('distance-banner', 'error');
  }
  const notice = (text, error = false) => {
    const el = $('notice');
    el.textContent = fieldText(text);
    el.classList.toggle('error', error);
    const distance = /公里|定位精度|还没有坐标|距离所选工单/.test(String(text || ''));
    el.classList.toggle('distance-banner', distance);
    clearTimeout(noticeTimer);
    if (distance) noticeTimer = setTimeout(clearDistanceNotice, 4500);
  };
  $('notice').addEventListener('click', clearDistanceNotice);
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
    $('timeSettings').hidden = mode !== 'camera';
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
  function completeDeviceFields(device) {
    return Boolean(device?.equipment_number && device.equipment_model && device.position_number && device.container_number);
  }
  function confirmDevice() {
    if (activeRepair && activeRepair.order_id === chosenOrder()?.id) {
      deviceSession = {...activeRepair, no_equipment_number:!activeRepair.equipment_number};
      renderCameraDeviceInfo();
      return true;
    }
    const number = $('equipmentNumber').value.trim(), noNumberChecked = $('noEquipmentNumber').checked;
    // 现场先拍后补：未填编号且未勾无编号时，按无编号会话先锁定，信息后续补录。
    const noNumber = noNumberChecked || !number;
    if (!number && !noNumberChecked) notice('设备编号暂未填，先上传待后续补录。', false);
    deviceSession = {id:key(), equipment_number:noNumber ? '' : number,
      position_number:$('positionNumber').value.trim().toUpperCase(), container_number:$('containerNumber').value.trim().toUpperCase(),
      no_equipment_number:noNumber};
    $('equipmentNumber').closest('fieldset').classList.add('locked');
    $('equipmentNumber').value = deviceSession.equipment_number;
    $('equipmentNumber').disabled = noNumber;
    setFieldText('deviceStatus', `已锁定：${number || '无铭牌号'}${deviceSession.position_number ? ' · 位置 '+deviceSession.position_number : ''}${deviceSession.container_number ? ' · 集装箱 '+deviceSession.container_number : ''}。后续照片沿用；换设备请点“清除”。`);
    renderCameraDeviceInfo();
    return true;
  }
  function applyRecognizedDevice(number, model) {
    const num = String(number || '').trim(), mdl = String(model || '').trim();
    if (num) { $('equipmentNumber').disabled = false; $('noEquipmentNumber').checked = false; $('equipmentNumber').value = num; }
    if (mdl) $('equipmentModel').value = mdl;
    deviceSession = null;
    renderCameraDeviceInfo();
  }
  function requireDeviceFields() {
    if (batch?.type !== 'equipment') return true;
    if (activeRepair && activeRepair.order_id === chosenOrder()?.id) return true;
    const number = $('equipmentNumber').value.trim(), noNumber = $('noEquipmentNumber').checked;
    const model = $('equipmentModel').value.trim();
    // 现场先拍后补：编号/型号缺失不再硬拦，照片先按待补批次入库，后续按设备归并补录并重绘水印。
    if (!number && !noNumber && !model) { notice('设备编号、型号暂未填，先上传待后续补录。', false); }
    else if (!number && !noNumber) { notice('设备编号暂未填，先上传待后续补录。', false); }
    else if (!model) { notice('设备型号暂未填，先上传待后续补录。', false); }
    return true;
  }

  function clearDeviceInputs() {
    if (activeRepair) return;
    // 新一组/换类型：清空设备输入框，避免残留上一张照片的设备信息。
    $('equipmentNumber').value = ''; $('equipmentModel').value = ''; $('positionNumber').value = ''; $('containerNumber').value = '';
    $('noEquipmentNumber').checked = false; $('equipmentNumber').disabled = false; $('equipmentNumber').required = true;
  }
  function currentDeviceInfoText() {
    const src = (activeRepair && activeRepair.order_id === chosenOrder()?.id) ? activeRepair : (deviceSession || {});
    const pairs = [
      ['编号', src.equipment_number || $('equipmentNumber').value.trim()],
      ['型号', src.equipment_model || $('equipmentModel').value.trim()],
      ['集装箱', src.container_number || $('containerNumber').value.trim()],
    ];
    return pairs.filter(([, value]) => value).map(([label, value]) => label + ' ' + value).join(' · ');
  }
  function renderCameraDeviceInfo() {
    const el = $('cameraDeviceInfo');
    if (!el) return;
    const text = batch && batch.type === 'equipment' ? currentDeviceInfoText() : '';
    el.textContent = fieldText(text);
    el.hidden = !text;
    renderPreviewWatermark();
  }
  let previewWatermarkGen = 0;
  // 预览用的水印上下文：取值与 makeContext 一致，但跳过定位与校验，供取景窗实时核对设备信息。
  function previewContext() {
    const selected = chosenOrder();
    const tech = profile?.technicians?.find(p => String(p.id) === $('technicianSelect').value) || {id: profile?.user.id, name: profile?.user.name};
    const device = activeRepair?.order_id === selected?.id ? activeRepair : deviceSession;
    const group = batch?.pending || {};
    const now = new Date();
    const watermark = captureWatermarkTime(now);
    const tz = $('timezoneName').value.trim() || 'UTC';
    const photoType = batch?.type || 'equipment';
    const loc = position || {};
    const ctx = {
      employee_name: tech.name || '', technician_user_id: tech.id,
      order_id: selected?.id, order_number: selected?.order_number,
      site_name: selected?.client_name, site_address: selected?.site_address,
      captured_at: now.toISOString(), watermark_at: Number.isNaN(watermark.getTime()) ? now.toISOString() : watermark.toISOString(),
      batch_id: batch?.id, photo_type: photoType, timezone_name: tz,
      latitude: Number(loc.latitude) || 0, longitude: Number(loc.longitude) || 0, accuracy: Number(loc.accuracy) || 100000,
      location_verified: false, note: $('photoNote').value.trim(), location_note: '', source: 'camera', error: '',
    };
    if (photoType === 'equipment') {
      Object.assign(ctx, {
        equipment_number: device?.equipment_number || group.equipment_number || $('equipmentNumber').value.trim(),
        equipment_model: device?.equipment_model || group.equipment_model || $('equipmentModel').value.trim(),
        position_number: device?.position_number || group.position_number || $('positionNumber').value.trim(),
        container_number: device?.container_number || group.container_number || $('containerNumber').value.trim(),
        fault_note: group.fault || device?.fault || $('repairFault').value.trim() || '',
        equipment_session: device?.id || batch?.id,
        repair_phase: repairStage || device?.repair_phase || '',
        work_content: '',
        no_equipment_number: device ? !!device.no_equipment_number : !group.equipment_number,
      });
    }
    return ctx;
  }
  async function renderPreviewWatermark() {
    const stage = $('cameraStage'), canvas = $('previewWatermark');
    if (!stage || !canvas) return;
    if (stage.hidden || document.body.classList.contains('recognition-mode')) {
      canvas.hidden = true;
      const c = canvas.getContext('2d'); if (c) c.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
    canvas.hidden = false;
    const gen = ++previewWatermarkGen;
    const c2 = canvas.getContext('2d');
    c2.setTransform(dpr, 0, 0, dpr, 0, 0);
    c2.clearRect(0, 0, w, h);
    await window.PrasinosWatermark.draw(c2, w, h, previewContext());
    if (gen !== previewWatermarkGen) return;
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
    if (typeChanged && stream) stopCamera();
    if (typeChanged && !(deviceSession && type === 'equipment')) clearDeviceInputs();  // 换组：不残留上一组的设备信息
    $('deviceNoteSlot').append($('captureNote'));
    // 只有设备照片需要设备信息与维修字段；进场 / 离场 / 自检 不显示这一块。
    $('deviceSession').hidden = type !== 'equipment';
    setKindChoiceActive(type);
    $('timeSettings').hidden = typeof captureMode !== 'undefined' && captureMode !== 'camera';
    const KIND_TEXT = {equipment:'设备照片：先拍，保存时再确认编号。',arrival:'进场照片：记录到达现场时间。',departure:'离场照片：记录离开现场时间。',safety:'自检照片：安全自检记录。',general:'非设备照片：请先选择进场、离场或自检照片。'};
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
    if (groupTimeResolve) { const resolve = groupTimeResolve; groupTimeResolve = null; resolve(true); return; }
    if (pendingUploadWatermark) { pendingUploadWatermark = false; setUploadWatermark(true); return; }
    openWatermarkTimeDialog();
  }
  function openWatermarkDialog() {
    ensureSession();
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
  function cancelWatermarkPassword() {
    pendingUploadWatermark = false;
    $('watermarkPasswordDialog').close();
    if (groupTimeResolve) { const resolve = groupTimeResolve; groupTimeResolve = null; resolve(false); }
  }
  function requestTimePassword() {
    if (timeAuthorized) return Promise.resolve(true);
    return new Promise(resolve => {
      groupTimeResolve = resolve;
      $('watermarkPassword').value = '';
      setFieldText('passwordStatus','修改水印时间需要验证密码。');
      $('watermarkPasswordDialog').showModal();
      $('watermarkPassword').focus();
    });
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
  function canvasStill(video, aspectRatio = null) {
    const canvas = document.createElement('canvas');
    const width = video.videoWidth || video.width;
    const height = video.videoHeight || video.height;
    const cropWidth = aspectRatio ? Math.round(Math.min(width, height * aspectRatio)) : width;
    const cropHeight = aspectRatio ? Math.round(Math.min(height, width / aspectRatio)) : height;
    canvas.width = cropWidth;
    canvas.height = cropHeight;
    canvas.getContext('2d').drawImage(video, (width - cropWidth) / 2, (height - cropHeight) / 2,
      cropWidth, cropHeight, 0, 0, cropWidth, cropHeight);
    return new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('无法读取相机画面')), 'image/jpeg', 0.92));
  }
  async function captureStill(video) {
    const track = stream?.getVideoTracks?.()[0];
    if (track && typeof ImageCapture === 'function') {
      try {
        const photo = await new ImageCapture(track).takePhoto();
        if (photo && photo.size && photo.size <= 10 * 1024 * 1024) return photo;
        if (photo && photo.size) {
          const bitmap = await createImageBitmap(photo);
          const still = await canvasStill(bitmap);
          bitmap.close?.();
          return still;
        }
      } catch (_) {}
    }
    return canvasStill(video);
  }
  async function uprightDrawable(file) {
    const draw = async (source, width, height, close) => {
      const canvas = document.createElement('canvas');
      canvas.width = width; canvas.height = height;
      canvas.getContext('2d').drawImage(source, 0, 0, width, height);
      close?.();
      const blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('无法读取照片')), 'image/jpeg', 0.92));
      const url = URL.createObjectURL(blob);
      const image = new Image(); image.src = url; await image.decode();
      return {image, url, blob};
    };
    if (typeof createImageBitmap === 'function') {
      try {
        const bitmap = await createImageBitmap(file, {imageOrientation: 'from-image'});
        return await draw(bitmap, bitmap.width, bitmap.height, () => bitmap.close?.());
      } catch (_) {}
    }
    const url = URL.createObjectURL(file);
    try {
      const image = new Image(); image.src = url; await image.decode();
      return await draw(image, image.naturalWidth, image.naturalHeight, () => URL.revokeObjectURL(url));
    } catch (_) {
      URL.revokeObjectURL(url);
      throw new Error('无法读取这张照片。iPhone 相册里的 HEIC 请直接选择，或先导出为 JPEG。');
    }
  }
  function chosenFiles(input) {
    const files = Array.from(input.files || []);
    input.value = '';
    return files;
  }
  function lockCapture(locked) {
    if (locked) {
      if (captureLockSnapshot) return;
      captureLockSnapshot = captureControls.map(id => [id, Boolean($(id)?.disabled)]);
      for (const id of captureControls) { const el = $(id); if (el) el.disabled = true; }
      return;
    }
    if (!captureLockSnapshot) return;
    for (const [id, was] of captureLockSnapshot) { const el = $(id); if (el) el.disabled = was; }
    captureLockSnapshot = null;
  }
  function openDialogHost() {
    const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
    return dialogs.length ? dialogs[dialogs.length - 1] : null;
  }
  function dialogRecognizeBar() { return $('recognizeDialogBar'); }
  function buildDialogRecognizeBar(host) {
    let bar = dialogRecognizeBar();
    if (bar && bar.parentElement === host) return bar;
    bar?.remove();
    bar = document.createElement('div');
    bar.id = 'recognizeDialogBar';
    bar.className = 'recognize-dialog-bar';
    bar.setAttribute('role', 'status');
    bar.setAttribute('aria-live', 'polite');
    const spinner = document.createElement('span');
    spinner.className = 'recognize-spinner';
    spinner.setAttribute('aria-hidden', 'true');
    const body = document.createElement('div');
    body.className = 'recognize-dialog-body';
    const text = document.createElement('strong');
    text.className = 'recognize-dialog-text';
    const elapsed = document.createElement('span');
    elapsed.className = 'recognize-dialog-elapsed';
    body.append(text, elapsed);
    const action = document.createElement('button');
    action.type = 'button';
    action.className = 'recognize-dialog-action';
    action.textContent = '取消';
    action.addEventListener('click', () => {
      if (bar.dataset.state === 'busy') { recognizeCancelled = true; recognizeAbort?.abort(); hideRecognizeProgress(); }
      else { bar.remove(); }
    });
    bar.append(spinner, body, action);
    host.append(bar);
    return bar;
  }
  function removeDialogRecognizeBar() { dialogRecognizeBar()?.remove(); }
  function showRecognizeProgress(kind, previewUrl) {
    recognizeKind = kind;
    recognizeCancelled = false;
    const progressText = {fault:'正在识别手操器屏幕，请稍候…', container:'正在识别集装箱号，请稍候…', position:'正在识别位置号，请稍候…'};
    const message = progressText[kind] || '正在识别铭牌，请稍候…';
    const started = Date.now();
    const tick = () => {
      const elapsedText = '已等待 ' + Math.floor((Date.now() - started) / 1000) + ' 秒';
      if (!$('recognizeOverlay').hidden) $('recognizeOverlayElapsed').textContent = elapsedText;
      const bar = dialogRecognizeBar();
      if (bar) bar.querySelector('.recognize-dialog-elapsed').textContent = elapsedText;
    };
    lockCapture(true);
    clearInterval(recognizeTimer);
    recognizeTimer = setInterval(tick, 250);
    const host = openDialogHost();
    if (host) {
      // A native modal dialog sits in the top layer, so the page overlay would be hidden behind it.
      $('recognizeOverlay').hidden = true;
      const bar = buildDialogRecognizeBar(host);
      bar.dataset.state = 'busy';
      bar.querySelector('.recognize-spinner').hidden = false;
      bar.querySelector('.recognize-dialog-text').textContent = message;
      bar.querySelector('.recognize-dialog-elapsed').hidden = false;
      bar.querySelector('.recognize-dialog-action').textContent = '取消';
      bar.hidden = false;
      tick();
      return;
    }
    $('recognizeOverlayThumb').src = previewUrl || '';
    $('recognizeOverlayText').textContent = message;
    $('recognizeOverlayError').hidden = true;
    $('recognizeSpinner').hidden = false;
    $('recognizeOverlayElapsed').hidden = false;
    $('overlayRetry').hidden = true;
    $('overlayManual').hidden = true;
    $('cancelRecognize').hidden = false;
    $('cancelRecognize').textContent = '取消';
    $('recognizeOverlayElapsed').textContent = '已等待 0 秒';
    $('recognizeOverlay').hidden = false;
  }
  function showRecognizeFailure(message) {
    clearInterval(recognizeTimer); recognizeTimer = null;
    lockCapture(false);
    const bar = dialogRecognizeBar();
    if (bar && bar.closest('dialog')?.open) {
      bar.dataset.state = 'error';
      bar.querySelector('.recognize-spinner').hidden = true;
      bar.querySelector('.recognize-dialog-elapsed').hidden = true;
      bar.querySelector('.recognize-dialog-text').textContent = (recognizeKind === 'fault' ? '手操器屏幕没有识别出来：' : '铭牌没有识别出来：') + message;
      bar.querySelector('.recognize-dialog-action').textContent = '关闭';
      bar.hidden = false;
      return;
    }
    $('recognizeSpinner').hidden = true;
    $('recognizeOverlayElapsed').hidden = true;
    $('recognizeOverlayText').textContent = recognizeKind === 'fault' ? '故障屏幕没有识别出来' : '铭牌没有识别出来';
    $('recognizeOverlayError').hidden = false;
    $('recognizeOverlayError').textContent = message;
    $('overlayRetry').hidden = false;
    $('overlayManual').hidden = false;
    $('cancelRecognize').textContent = '关闭';
    $('recognizeOverlay').hidden = false;
  }
  function hideRecognizeProgress() {
    clearInterval(recognizeTimer); recognizeTimer = null;
    recognizeAbort = null;
    $('recognizeOverlay').hidden = true;
    removeDialogRecognizeBar();
    lockCapture(false);
  }
  function recognitionFailure(error) {
    if (recognizeCancelled) return;
    const timedOut = error?.name === 'AbortError';
    showRecognizeFailure(timedOut ? '识别超时，请换一张更清晰的照片，或手工输入。' : (error?.message || '识别失败'));
  }
  const nameplateDrafts = new WeakSet();
  async function keepNameplateDraft(file, source, context) {
    if (!context || nameplateDrafts.has(file)) return;
    const draft = {...context, photo_role:'nameplate', pending_device:true};
    if (draft.watermark_opt_out) await keepOriginalFile(file, draft);
    else await keepCapture(source, draft);
    nameplateDrafts.add(file);
  }
  async function recognizeNameplateFile(file, addWatermark = true) {
    if (ocrBusy) return;
    ocrBusy = true;
    nameplateBlob = file;
    if (nameplatePreview) URL.revokeObjectURL(nameplatePreview);
    nameplatePreview = URL.createObjectURL(file);
    $('nameplateThumb').src = nameplatePreview;
    $('nameplatePending').hidden = false;
    $('nameplateStatus').textContent = '正在识别铭牌，请稍候…';
    showRecognizeProgress('nameplate', nameplatePreview);
    stopCamera();
    ocrBusy = true;
    recognizeAbort = new AbortController();
    try {
      const drawn = await uprightDrawable(file);
      if (nameplateDrawnUrl) URL.revokeObjectURL(nameplateDrawnUrl);
      nameplateDrawnUrl = drawn.url;
      $('recognizeOverlayThumb').src = drawn.url;
      $('nameplateThumb').src = drawn.url;
      const context = await makeContext('file', {order:{...chosenOrder()}, userId:profile.user.id, watermarkSource:'system'});
      const exif = await readPhotoExifDate(file);
      if (context && exif) {
        context.captured_at = exif.toISOString();
        context.filename_time = photoFilenameTime(exif);
        if (useSystemTime) context.watermark_at = context.captured_at;
      }
      if (context && !addWatermark) Object.assign(context, originalAlbumContext());
      await keepNameplateDraft(file, drawn.image, context);
      const data = new FormData(); data.append('photo', drawn.blob, 'nameplate.jpg');
      const response = await requestAPI('/api/field/recognize-equipment', {method:'POST', headers:{'X-Field-Token':profile.csrf}, body:data, signal:recognizeAbort.signal});
      const result = await response.json().catch(()=>({}));
      if (recognizeCancelled) return;
      if (!response.ok) throw new Error(result.error || '铭牌识别失败');
      if (!result.candidates?.length && !result.equipment_model) throw new Error('未识别到设备编号或型号，请重试或手工输入。');
      $('recognizedNumber').textContent = result.candidates?.[0] || '';
      $('recognizedModel').textContent = result.equipment_model || '';
      applyRecognizedDevice(result.candidates?.[0], result.equipment_model);
      $('nameplateStatus').textContent = '识别完成，请核对。';
      $('nameplatePendingConfirm').hidden = false;
      hideRecognizeProgress();
      if (!$('recognitionDialog').open) $('recognitionDialog').showModal();
    } catch (error) {
      $('nameplateStatus').textContent = error.message || '铭牌识别失败';
      $('nameplatePendingConfirm').hidden = true;
      recognitionFailure(error);
    } finally { ocrBusy = false; }
  }
  function showFaultAlbumStatus(message) {
    const status = $('faultAlbumStatus');
    status.textContent = message;
    status.hidden = !message;
  }
  function readyForController() {
    const missing = ['equipmentNumber','equipmentModel','positionNumber','containerNumber'].find(id => !$(id).value.trim());
    if (missing) {
      const message = '请先填齐设备编号、型号、位置号和集装箱号。';
      showFaultAlbumStatus(message);
      notice(message,true);
      $(missing).focus();
      return false;
    }
    return confirmDevice();
  }
  function addFaultPhoto(drawn, context, file) {
    const photo = {file, canvas:drawn.image, context, preview:drawn.url, blob:drawn.blob};
    faultPhotos.push(photo);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'fault-thumbnail';
    button.setAttribute('aria-label', fieldText('放大屏幕照片'));
    const thumbnail = new Image();
    thumbnail.src = drawn.url;
    thumbnail.alt = fieldText('手操器故障照片');
    button.append(thumbnail);
    button.addEventListener('click', () => openDraft({...context, blob:photo.blob, stagedFault:photo}, true));
    $('repairFaultPhotos').append(button);
    $('repairFaultPhotos').hidden = false;
    return photo;
  }
  function appendFaultRecognition(photo, text) {
    const field = $('repairFault');
    const current = field.value.trimEnd();
    const result = String(text || '').trim();
    if (!result || (photo.recognizedFault === result && current.includes(result))) return;
    const offset = photo.faultOffset;
    if (Number.isInteger(offset) && current.slice(offset, offset + photo.recognizedFault.length) === photo.recognizedFault) {
      field.value = current.slice(0, offset) + result + current.slice(offset + photo.recognizedFault.length);
      const shift = result.length - photo.recognizedFault.length;
      for (const other of faultPhotos) {
        if (other.faultOffset > offset) other.faultOffset += shift;
      }
    } else {
      photo.faultOffset = current ? current.length + 2 : 0;
      field.value = [current, result].filter(Boolean).join('\n\n');
    }
    photo.recognizedFault = result;
    limitFaultField();
    if (batch?.pending) batch.pending.fault = field.value;
  }
  $('clearRepairFault').addEventListener('click', () => {
    if (activeRepair || ocrBusy) return;
    $('repairFault').value = '';
    if (batch?.pending) batch.pending.fault = '';
    showFaultAlbumStatus('');
    limitFaultField();
    $('repairFault').focus();
  });
  let visionIssueTimer = null;
  function showControllerVisionIssue(result) {
    const message = {
      vision_timeout: '视觉模型调用超时',
      vision_call_error: '视觉模型调用报错'
    }[result.reason];
    if (!message) return;
    const dialog = $('controllerVisionIssue');
    setFieldText('controllerVisionMessage', message);
    setFieldText('controllerVisionFallback', result.fault_text
      ? '已使用备用文字识别，请核对结果。'
      : '备用文字识别未读出可靠结果，请重拍或手工填写。');
    if (!dialog.open) dialog.showModal();
    clearTimeout(visionIssueTimer);
    visionIssueTimer = setTimeout(() => { if (dialog.open) dialog.close(); }, 3000);
  }
  $('controllerVisionIssue').addEventListener('close', () => {
    if (!$('controllerVisionIssue').open) clearTimeout(visionIssueTimer);
  });
  async function recognizeFaultFile(file, addWatermark = true) {
    if (ocrBusy) { showFaultAlbumStatus('正在识别另一张照片，请稍候。'); return; }
    if (repairStage !== 'before' || activeRepair) { showFaultAlbumStatus('请在保存维修前记录之前选择故障照片。'); return; }
    if (!readyForController()) return;
    showFaultAlbumStatus('已选照片，正在识别手操器屏幕…');
    ocrBusy = true;
    faultBlob = file;
    const preview = URL.createObjectURL(file);
    showRecognizeProgress('fault', preview);
    stopCamera();
    ocrBusy = true;
    recognizeAbort = new AbortController();
    try {
      const exif = await readPhotoExifDate(file);
      const existing = faultPhotos.find(photo => photo.file === file);
      const drawn = existing ? {blob:existing.blob, image:existing.canvas, url:existing.preview} : await uprightDrawable(file);
      $('recognizeOverlayThumb').src = drawn.url;
      const context = existing?.context || await makeContext('file', {order:{...chosenOrder()}, userId:profile.user.id, watermarkSource:'system'});
      if (!context) throw new Error('照片还不能入队，请先选择工单和施工员。');
      if (exif) {
        context.captured_at = exif.toISOString();
        context.filename_time = photoFilenameTime(exif);
        if (useSystemTime) context.watermark_at = context.captured_at;
      }
      if (!existing) Object.assign(context, addWatermark ? {watermark_source:'system'} : originalAlbumContext());
      const photo = existing || addFaultPhoto(drawn, context, file);
      const data = new FormData(); data.append('photo', drawn.blob, 'fault.jpg'); data.append('mode', 'controller_fault');
      const response = await requestAPI('/api/field/recognize-equipment', {method:'POST', headers:{'X-Field-Token':profile.csrf}, body:data, signal:recognizeAbort.signal});
      const result = await response.json().catch(()=>({}));
      if (recognizeCancelled) {
        showFaultAlbumStatus('已取消识别，照片已保留，可手工填写故障信息。');
        return;
      }
      showControllerVisionIssue(result);
      if (!response.ok) throw new Error(result.error || '故障识别失败');
      if (!result.fault_text) throw new Error('没有读到屏幕文字，请换一张更清晰的照片，或手工输入。');
      const field = $('repairFault');
      const truncated = field.value.length + result.fault_text.length + 2 > FAULT_TEXT_LIMIT;
      appendFaultRecognition(photo, result.fault_text);
      hideRecognizeProgress();
      field.focus();
      showFaultAlbumStatus('已识别，请对照缩略图核对故障代码和告警内容。');
      notice(truncated ? '识别结果较长，已保留前 4000 字，请核对。' : '识别结果已填入，请核对报码和提示文字。');
    } catch (error) {
      showFaultAlbumStatus(error.message || '无法识别照片，请重选或手工填写。');
      recognitionFailure(error);
    }
    finally { URL.revokeObjectURL(preview); ocrBusy = false; }
  }
  function originalAlbumContext() {
    return {watermark_source:'original', watermark_opt_out:true};
  }
  async function saveAlbumLikeCamera(files, addWatermark = true) {
    if (!files.length || taking) return;
    ensureSession();
    if (!UPLOAD_TYPES.includes(batch?.type)) { notice('请先选择照片类型：进场、离场、自检或设备。', true); return; }
    if (batch.type === 'equipment' && repairStage === 'after' && !activeRepair) { notice('请先选择维修记录。', true); return; }
    if (!requireDeviceFields()) return;
    taking = true;
    try {
      const base = await makeContext('file', {order:{...chosenOrder()}, userId:profile.user.id, watermarkSource:'system'});
      if (!base) return;
      base.watermark_source = 'system';
      for (const file of files) {
        const exif = await readPhotoExifDate(file);
        const context = {...base, client_id:key()};
        if (exif) {
          context.captured_at = exif.toISOString();
          context.filename_time = photoFilenameTime(exif);
          if (useSystemTime) context.watermark_at = exif.toISOString();
        }
        if (!addWatermark) {
          Object.assign(context, originalAlbumContext());
          applyPhotoRole(context, batch.type === 'equipment' ? 'other' : batch.type);
          await keepOriginalFile(file, context);
          continue;
        }
        const drawn = await uprightDrawable(file);
        try { await captureRawAndClassify(drawn.image, context); }
        finally { URL.revokeObjectURL(drawn.url); }
      }
    } catch (error) { notice('照片未保存：'+error.message, true); }
    finally { taking = false; await renderQueue(); }
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
    let preview = '';
    try {
      const video = $('viewfinder');
      const takenAt = new Date();
      const blob = faultMode ? await canvasStill(video, 16 / 9) : await captureStill(video);
      if (faultMode) faultBlob = blob; else nameplateBlob = blob;
      preview = URL.createObjectURL(blob);
      if (!automatic) showRecognizeProgress(faultMode ? 'fault' : 'nameplate', preview);
      recognizeAbort = new AbortController();
      const data = new FormData(); data.append('photo',blob,'nameplate.jpg');
      if (faultMode) data.append('mode', 'controller_fault');
      const response = await requestAPI('/api/field/recognize-equipment',{method:'POST',headers:{'X-Field-Token':profile.csrf},body:data,signal:recognizeAbort.signal});
      const result = await response.json().catch(()=>({}));
      if (recognizeCancelled || stream !== captureStream) { hideRecognizeProgress(); return; }
      if (!response.ok && !faultMode) throw new Error(result.error || '铭牌识别失败');
      if (faultMode) {
        if (repairStage !== 'before' || activeRepair || !readyForController()) { hideRecognizeProgress(); return; }
        const context = await makeContext('camera', null, takenAt);
        if (!context || stream !== captureStream) { hideRecognizeProgress(); return; }
        const drawn = await uprightDrawable(blob);
        if (stream !== captureStream) { URL.revokeObjectURL(drawn.url); hideRecognizeProgress(); return; }
        const photo = addFaultPhoto(drawn, context, blob);
        showFaultAlbumStatus('');
        showControllerVisionIssue(result);
        if (!response.ok) throw new Error(result.error || '故障识别失败，请核对照片并手工填写。');
        if (!result.fault_text) throw new Error('没有读到屏幕文字，请换一张更清晰的照片，或手工输入。');
        const field = $('repairFault');
        const truncated = field.value.length + result.fault_text.length + 2 > FAULT_TEXT_LIMIT;
        appendFaultRecognition(photo, result.fault_text);
        hideRecognizeProgress();
        stopCamera();
        field.focus();
        notice(truncated ? '识别结果较长，已保留前 4000 字，请核对。' : '识别结果已填入，请核对报码和提示文字。');
        return;
      }
      if (!result.candidates?.length && !result.equipment_model) {
        if (automatic) { hideRecognizeProgress(); notice('自动识别中，请保持整张铭牌清晰。'); return; }
        throw new Error('未识别到设备编号或型号，请重试或手工输入。');
      }
      const context = await makeContext('camera', null, takenAt);
      if (!context || stream !== captureStream) { hideRecognizeProgress(); return; }
      const drawn = await uprightDrawable(blob);
      try { await keepNameplateDraft(blob, drawn.image, context); }
      finally { URL.revokeObjectURL(drawn.url); }
      hideRecognizeProgress();
      stopCamera();
      $('recognizedNumber').textContent = result.candidates?.[0] || '';
      $('recognizedModel').textContent = result.equipment_model || '';
      applyRecognizedDevice(result.candidates?.[0], result.equipment_model);
      $('recognitionDialog').showModal();
    } catch(error) { if (!automatic) recognitionFailure(error); }
    finally { if (preview) URL.revokeObjectURL(preview); ocrBusy = false; button.disabled = false; }
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
      try {
        saved = {...photo, blob:await photo.blob.arrayBuffer(), blob_type:photo.blob.type};
        if (photo.original_blob instanceof Blob) {
          saved.original_blob = await photo.original_blob.arrayBuffer();
          saved.original_blob_type = photo.original_blob.type;
        }
      }
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
      request.onsuccess = () => resolve(request.result.filter(item => item.user_id === profile.user.id && (!batchId || item.batch_id === batchId)).sort((a,b) => a.captured_at.localeCompare(b.captured_at)).map(item => {
        const photo = item.blob instanceof ArrayBuffer ? {...item, blob:new Blob([item.blob], {type:item.blob_type || item.content_type || 'image/jpeg'})} : {...item};
        if (photo.original_blob instanceof ArrayBuffer) photo.original_blob = new Blob([photo.original_blob], {type:photo.original_blob_type || 'image/jpeg'});
        return photo;
      }));
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
    $('headerVersion').textContent = profile.version ? 'V' + profile.version : '';
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
    $('shutterButton').disabled = !profile.can_capture;
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
    $('captureSource').hidden = true;
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
  let cameraClockTimer = null;
  function captureWatermarkTime(actual, commit = false) {
    if (useSystemTime) return actual;
    const firstShot = batch.first_adjusted_shot_at ?? actual.getTime();
    if (commit) batch.first_adjusted_shot_at = firstShot;
    const anchor = batch.watermark_anchor_ms ?? new Date($('watermarkStart').value).getTime();
    return new Date(anchor + actual.getTime() - firstShot);
  }
  function showCameraClock() {
    let displayTime;
    try {
      displayTime = new Intl.DateTimeFormat('zh-CN', {
        timeZone:$('timezoneName').value.trim() || 'UTC',
        year:'numeric',month:'2-digit',day:'2-digit',
        hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'
      });
    } catch (_) {
      displayTime = new Intl.DateTimeFormat('zh-CN', {dateStyle:'short',timeStyle:'medium'});
    }
    const clock = $('cameraWatermarkClock'), time = $('cameraClockTime');
    const stage = $('cameraStage');
    if (document.body.classList.contains('nameplate-camera')) $('ocrGuide').append(clock);
    else stage.before(clock);
    clock.hidden = false;
    clock.classList.toggle('camera-clock-adjusted', !useSystemTime);
    setFieldText('cameraClockMode', useSystemTime ? '水印 · 系统时间' : '水印 · 已调整');
    let lastPreviewDraw = 0;
    const tick = () => {
      const shown = captureWatermarkTime(new Date());
      if (Number.isNaN(shown.getTime())) {
        time.removeAttribute('datetime');
        setFieldText('cameraClockTime','请检查水印时间');
      } else {
        time.dateTime = shown.toISOString();
        time.textContent = displayTime.format(shown);
      }
      const nowMs = Date.now();
      if (nowMs - lastPreviewDraw >= 1000) { lastPreviewDraw = nowMs; renderPreviewWatermark(); }
    };
    clearInterval(cameraClockTimer);
    tick();
    cameraClockTimer = setInterval(tick, 250);
  }
  function stopCamera() {
    clearInterval(cameraClockTimer);
    cameraClockTimer = null;
    $('cameraWatermarkClock').hidden = true;
    const deviceInfo = $('cameraDeviceInfo'); if (deviceInfo) { deviceInfo.hidden = true; deviceInfo.textContent = ''; }
    previewWatermarkGen++;
    const previewCanvas = $('previewWatermark'); if (previewCanvas) { previewCanvas.hidden = true; const pc = previewCanvas.getContext('2d'); if (pc) pc.clearRect(0, 0, previewCanvas.width, previewCanvas.height); }
    cameraStripSession = null;
    clearInterval(scanTimer); scanTimer = null;
    clearInterval(ocrTimer); ocrTimer = null; ocrBusy = false;
    clearTimeout(ocrStartTimer); ocrStartTimer = null;
    cameraSelection = null;
    $('flipSafetyCamera').hidden = true;
    $('flipSafetyCamera').disabled = false;
    document.body.classList.remove('camera-active');
    document.body.classList.remove('recognition-mode', 'controller-camera', 'nameplate-camera');
    $('ocrGuide').hidden = true;
    $('readRepairFault').hidden = true;
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    $('viewfinder').srcObject = null; $('viewfinder').hidden = true;
    $('openCamera').hidden = true; $('cameraBar').hidden = true; $('takePhoto').hidden = true; $('closeCamera').hidden = true;
    $('cameraStage').hidden = true;
    $('cameraView').hidden = captureMode !== 'camera';
  }
  async function attachCamera(selection, facingMode) {
    const next = await navigator.mediaDevices.getUserMedia({
      video:{facingMode,width:{ideal:3840},height:{ideal:2160}},audio:false
    });
    if (cameraSelection !== selection) {
      next.getTracks().forEach(track => track.stop());
      return false;
    }
    stream = next;
    $('viewfinder').srcObject = next;
    await $('viewfinder').play();
    return cameraSelection === selection;
  }
  async function flipSafetyCamera() {
    const button = $('flipSafetyCamera'), selection = cameraSelection;
    if (button.hidden || !selection || !stream || switchingCamera || taking || ocrBusy || syncing || processingFiles) return;
    const shutter = $('takePhoto');
    const previous = cameraFacing;
    const next = previous === 'environment' ? 'user' : 'environment';
    switchingCamera = true;
    button.disabled = shutter.disabled = true;
    try {
      // iOS may only allow access to one camera at a time.
      stream.getTracks().forEach(track => track.stop());
      stream = null;
      if (await attachCamera(selection, {exact:next})) {
        cameraFacing = next;
        button.textContent = next === 'user' ? '后置' : '前置';
        button.setAttribute('aria-label', next === 'user' ? '切换到后置摄像头' : '切换到前置摄像头');
      }
    } catch (error) {
      if (cameraSelection !== selection) return;
      stream?.getTracks().forEach(track => track.stop());
      stream = null;
      try {
        if (await attachCamera(selection, {ideal:previous})) notice('无法切换摄像头，已恢复原摄像头。',true);
      } catch (restoreError) {
        if (cameraSelection === selection) {
          stopCamera();
          notice('无法切换摄像头，请重新打开相机。',true);
        }
      }
    } finally {
      switchingCamera = false;
      button.disabled = shutter.disabled = false;
    }
  }
  async function openCamera(recognitionOnly = false, faultMode = false) {
    if (!identityReady || !profile?.can_capture || taking || ocrBusy || syncing || switchingCamera) return;
    if (!recognitionOnly && captureMode !== 'camera') return;
    ensureSession();
    if (!useSystemTime && !timeAuthorized) { notice('请先验证水印时间调整密码。',true); return; }
    if (batch.type === 'equipment' && repairStage === 'after' && !recognitionOnly && !requireAfterWork()) return;
    const selected = chosenOrder();
    if (!selected) { notice('请先在上方选择工单。',true); $('orderSelect').focus(); return; }
    const selection = {order:{...selected}, userId:profile.user.id};
    try {
      currentOrder = selected;
      cameraSelection = selection;
      stream?.getTracks().forEach(track => track.stop());
      stream = null;
      $('cameraView').hidden = false;
      $('cameraStage').hidden = false;
      $('viewfinder').hidden = false;
      if (!(await attachCamera(selection, {ideal:'environment'}))) return;
      cameraFacing = stream.getVideoTracks()[0]?.getSettings?.().facingMode || 'environment';
      const locationNumber = activeRepair?.position_number || $('positionNumber').value.trim();
      if (batch.type === 'equipment' && locationNumber) {
        const phase = activeRepair?.repair_phase === 'after' ? '维修后' : '维修前';
        $('cameraOrder').textContent = `${locationNumber} · ${phase} · ${selected.order_number}`;
      } else {
        $('cameraOrder').textContent = selected.order_number + ' · ' + selected.client_name;
      }
      renderCameraDeviceInfo();
      const flip = $('flipSafetyCamera');
      flip.hidden = recognitionOnly || batch.type !== 'safety';
      flip.textContent = cameraFacing === 'user' ? '后置' : '前置';
      flip.setAttribute('aria-label', cameraFacing === 'user' ? '切换到后置摄像头' : '切换到前置摄像头');
      clearRecentCaptures();
      cameraStripSession = recognitionOnly ? null : {batchId:batch.id, ids:new Set()};
      document.body.classList.add('camera-active');
      document.body.classList.toggle('recognition-mode', recognitionOnly);
      document.body.classList.toggle('controller-camera', faultMode);
      const nameplate = recognitionOnly && !faultMode;
      document.body.classList.toggle('nameplate-camera', nameplate);
      if (nameplate && !$('notice').matches('.error, .distance-banner')) notice('');
      window.scrollTo({left:0, top:window.scrollY, behavior:'instant'});
      $('ocrGuide').hidden = !recognitionOnly;
      $('ocrGuide').querySelector('span').textContent = faultMode ? '对齐屏幕四角' : '将整张铭牌放入框内';
      $('readRepairFault').hidden = !faultMode;
      showCameraClock();
      $('openCamera').hidden = true; $('cameraBar').hidden = false; $('takePhoto').hidden = faultMode; $('closeCamera').hidden = false;
      await renderQueue();
      if (cameraSelection === selection && !faultMode) { scanDevice(); scanTimer = setInterval(scanDevice, 800); }
    } catch (error) {
      if (cameraSelection === selection) {
        notice('无法打开相机，请检查相机权限，或切换到“上传”选择照片。',true);
        stopCamera();
      }
    }
  }
  // ---- 0.1.467 上传方式（不生成水印 / 生成水印）与 EXIF 时间批量调整 ----
  const UPLOAD_TYPES = ['arrival','departure','safety','equipment'];
  const UPLOAD_TYPE_TEXT = {equipment:'设备照片：先拍，保存时再确认编号。',arrival:'进场照片：记录到达现场时间。',departure:'离场照片：记录离开现场时间。',safety:'自检照片：安全自检记录。'};
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
  function photoFilenameTime(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString();
    return local.slice(0, 19).replace(/[-:]/g, '').replace('T', '_');
  }
  function readExifDate(buffer) {
    try {
      const v = new DataView(buffer);
      if (v.byteLength >= 12 && v.getUint32(0) === 0x89504E47 && v.getUint32(4) === 0x0D0A1A0A) {
        for (let o = 8; o + 12 <= v.byteLength;) {
          const length = v.getUint32(o);
          if (o + 12 + length > v.byteLength) return null;
          if (v.getUint32(o + 4) === 0x65584966) return exifTiffDate(v, o + 8);
          o += 12 + length;
        }
      }
      if (v.byteLength >= 12 && v.getUint32(0) === 0x52494646 && v.getUint32(8) === 0x57454250) {
        for (let o = 12; o + 8 <= v.byteLength;) {
          const length = v.getUint32(o + 4, true);
          if (o + 8 + length > v.byteLength) return null;
          if (v.getUint32(o) === 0x45584946 && length >= 8) {
            const start = o + 8;
            return exifTiffDate(v, start + (v.getUint32(start) === 0x45786966 ? 6 : 0));
          }
          o += 8 + length + (length % 2);
        }
      }
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
      return d.getFullYear() >= 2000 && photoFilenameTime(d) === m.slice(1, 4).join('') + '_' + m.slice(4).join('') ? d : null;
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
    if (batch.type === 'equipment' && (!requireDeviceFields() || (!deviceSession && !confirmDevice()))) return;
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
            filename_time:item.exif ? photoFilenameTime(item.exif) : '',
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
  async function makeContext(source, lockedSelection = null, actual = new Date()) {
    const resolved = chosenOrder();
    if (!identityReady || !profile?.can_capture || !resolved) throw new Error('请先在上方选择工单。');
    const selected = lockedSelection?.order || cameraSelection?.order || {...resolved};
    const afterWork = batch?.type === 'equipment' && repairStage === 'after' ? requireAfterWork() : '';
    if (afterWork === null) throw new Error('请先填写工作内容，再拍摄或选择维修后照片。');
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
    if (!batch) batch = {id:key(), type:'equipment', actual_start:Date.now(), watermark_start:null, session_id:null};
    if (!batch.session_id) batch.session_id = batch.id;
    if (batch.type === 'general') throw new Error('请先选择进场、离场或自检照片。');
    // Freeze the shutter-time metadata before asynchronous location checks.
    const photoType = batch.type, captureBatchId = batch.id;
    const device = activeRepair?.order_id === selected.id ? activeRepair : deviceSession;
    const captureDevice = device ? {...device} : null;
    const group = batch?.pending || {};
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
    const watermark = captureWatermarkTime(actual, true);
    if (Number.isNaN(watermark.getTime())) throw new Error('请选择有效的水印开始时间。');
    const technician = profile.technicians.find(person => String(person.id) === $('technicianSelect').value);
    if (!technician) throw new Error('请选择施工员。');
    return {client_id:key(),user_id:profile.user.id,employee_name:technician.name,technician_user_id:technician.id,
      order_id:selected.id,order_number:selected.order_number,site_name:selected.client_name,site_address:selected.site_address,
      filename_time:source === 'camera' ? photoFilenameTime(actual) : '',
      captured_at:actual.toISOString(),watermark_at:watermark.toISOString(),batch_id:captureBatchId,photo_type:photoType,timezone_name:timezoneName,
      latitude:skipsLocation ? 0 : position.latitude,longitude:skipsLocation ? 0 : position.longitude,accuracy:skipsLocation ? 100000 : position.accuracy,
      location_verified:!skipsLocation,note:afterWork ? '' : $('photoNote').value.trim(),location_note:keepsOriginalWatermark ? '原图已有水印，未检查拍摄位置。' : skipsLocation ? '选图上传，未检查拍摄位置。' : locationNote,source,error:'',
      ...(photoType === 'equipment' ? {equipment_number:captureDevice?.equipment_number || group.equipment_number || $('equipmentNumber').value.trim(),
      equipment_model:captureDevice?.equipment_model || group.equipment_model || $('equipmentModel').value.trim(), position_number:captureDevice?.position_number || group.position_number || $('positionNumber').value.trim(),
      container_number:captureDevice?.container_number || group.container_number || $('containerNumber').value.trim(), fault_note:group.fault || captureDevice?.fault || $('repairFault').value.trim() || '',
      equipment_session:captureDevice?.id || captureBatchId, repair_phase:repairStage || captureDevice?.repair_phase || '',
      work_content:afterWork,
      no_equipment_number:captureDevice ? !!captureDevice.no_equipment_number : !group.equipment_number} : {})};
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
    if (context.watermark_source !== 'original') {
      photo.original_blob = await processedPhoto(source,{...context,watermark_source:'original'});
    }
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
  function ensureGroupPending() {
    if (!batch.pending) batch.pending = {equipment_number:'', equipment_model:'', container_number:'', position_number:'', fault:'', sources:{}};
    return batch.pending;
  }
  const ROLE_PHOTO_TYPE = {arrival:'arrival', departure:'departure', safety:'safety', nameplate:'equipment', container:'equipment', position:'equipment', controller:'equipment', other:'equipment', none:'equipment'};
  const ROLE_LABEL = {arrival:'进场', departure:'离场', safety:'自检', nameplate:'铭牌', container:'集装箱号', position:'位置号', controller:'手操器', other:'其他设备', none:'其他设备'};
  const RECOGNIZE_ROLES = new Set(['nameplate', 'container', 'position', 'controller']);
  function ensureSession() {
    if (!batch) batch = {id:key(), type:'equipment', actual_start:Date.now(), watermark_start:null};
    if (!batch.session_id) batch.session_id = batch.id;
    return batch;
  }
  function roleToPhotoType(role) { return ROLE_PHOTO_TYPE[role] || 'equipment'; }
  function applyPhotoRole(photo, role) {
    const session = ensureSession();
    photo.photo_role = role === 'none' ? 'other' : role;
    photo.photo_type = roleToPhotoType(photo.photo_role);
    photo.capture_session = session.session_id;
    photo.batch_id = session.session_id + ':' + photo.photo_type;
    session.type = photo.photo_type;
    // 拍完再选类型时，界面也跟着切换：非设备类型隐藏设备信息区。
    $('deviceSession').hidden = session.type !== 'equipment';
    setKindChoiceActive(session.type);
  }
  function highlightLastRole() {
    const last = localStorage.getItem('field-last-photo-role') || '';
    document.querySelectorAll('#photoRoleDialog [data-role]').forEach(button => button.classList.toggle('role-default', button.dataset.role === last));
  }
  function askPhotoRole() {
    highlightLastRole();
    return new Promise(resolve => { photoRoleResolve = resolve; $('photoRoleDialog').showModal(); });
  }
  function finishPhotoRole(role) {
    if (role) localStorage.setItem('field-last-photo-role', role);
    $('photoRoleDialog').close();
    const resolve = photoRoleResolve;
    photoRoleResolve = null;
    resolve?.(role);
  }
  async function blobImage(blob) {
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.src = url;
    await image.decode();
    return {image, url};
  }
  async function captureRawAndClassify(source, context) {
    const raw = await processedPhoto(source, {...context, watermark_source:'original'});
    // Stamp a preview immediately so a freshly shot photo already shows its watermark.
    // The raw original is kept and redrawn on group confirmation, once the batch fields
    // and watermark time are final.
    let stamped;
    try { stamped = await processedPhoto(source, {...context, watermark_source:'system'}); }
    catch (_) { stamped = raw; }
    // 沿用拍照前在顶部选定的类型，拍完不再弹出“这张照片是什么”二次选择；
    // 设备照片默认归入“其他设备照片”，需要细分铭牌 / 集装箱 / 位置号时点缩略图再改。
    const role = batch?.type === 'arrival' ? 'arrival'
      : batch?.type === 'departure' ? 'departure'
      : batch?.type === 'safety' ? 'safety'
      : 'other';
    const photo = {...context, blob:stamped, original_blob:raw, awaiting_watermark:stamped === raw, watermark_source:'system', photo_role:role, recognition_status:''};
    applyPhotoRole(photo, role);
    await storePhoto(photo);
    if (cameraStripSession && photo.source === 'camera' && photo.batch_id === cameraStripSession.batchId) cameraStripSession.ids.add(photo.client_id);
    await renderQueue();
    if (!RECOGNIZE_ROLES.has(photo.photo_role)) {
      photo.recognition_status = 'skipped';
      await storePhoto(photo);
      return;
    }
    if (!navigator.onLine) {
      photo.recognition_status = 'offline';
      await storePhoto(photo);
      notice('已记下照片类型，联网后会自动识别。');
      return;
    }
    await runRoleRecognition(photo);
  }
  async function runRoleRecognition(photo) {
    if (ocrBusy) return;
    const raw = photo.original_blob || photo.blob;
    const preview = URL.createObjectURL(raw);
    const kind = photo.photo_role === 'controller' ? 'fault' : photo.photo_role;
    showRecognizeProgress(kind, preview);
    recognizeAbort = new AbortController();
    ocrBusy = true;
    try {
      const drawn = await uprightDrawable(raw);
      const data = new FormData();
      data.append('photo', drawn.blob, 'photo.jpg');
      const mode = {container:'container', position:'position', controller:'controller_fault'}[photo.photo_role] || '';
      if (mode) data.append('mode', mode);
      const response = await requestAPI('/api/field/recognize-equipment', {method:'POST', headers:{'X-Field-Token':profile.csrf}, body:data, signal:recognizeAbort.signal});
      const result = await response.json().catch(() => ({}));
      if (recognizeCancelled) return;
      if (photo.photo_role === 'controller') showControllerVisionIssue(result);
      if (!response.ok) throw new Error(result.error || '识别失败');
      const pending = ensureGroupPending();
      pending.sources[photo.photo_role] = photo.client_id;
      if (photo.photo_role === 'nameplate') {
        pending.equipment_number = result.candidates?.[0] || pending.equipment_number;
        pending.equipment_model = result.equipment_model || pending.equipment_model;
        photo.recognized_number = pending.equipment_number;
        photo.recognized_model = pending.equipment_model;
        $('recognizedNumber').textContent = pending.equipment_number;
        $('recognizedModel').textContent = pending.equipment_model;
        applyRecognizedDevice(pending.equipment_number, pending.equipment_model);
        $('nameplateStatus').textContent = '识别完成，请确认。';
        $('nameplatePending').hidden = false;
        $('nameplatePendingConfirm').hidden = false;
        hideRecognizeProgress();
        if (!$('recognitionDialog').open) $('recognitionDialog').showModal();
      } else if (photo.photo_role === 'container') {
        pending.container_number = result.container_number || '';
        photo.recognized_container = pending.container_number;
        photo.container_number = pending.container_number;
        if (!pending.container_number) photo.position_number = photo.recognized_position || '';
        hideRecognizeProgress();
        notice(result.check_digit_ok ? '集装箱号已读出，保存前请再确认。' : '集装箱号校验位不一致，保存前请核对。', !result.check_digit_ok);
      } else if (photo.photo_role === 'position') {
        pending.position_number = result.position_number || '';
        photo.recognized_position = pending.position_number;
        hideRecognizeProgress();
        notice(pending.position_number ? '位置号已读出，保存前请再确认。' : '没有读到位置号，可以保存时手工填写。', !pending.position_number);
      } else if (photo.photo_role === 'controller') {
        appendFaultRecognition(photo, result.fault_text);
        hideRecognizeProgress();
        if (!pending.fault) notice('没有读到屏幕文字，可以保存时手工填写。', true);
        else if (result.source === 'ocr') notice('这次是本地 OCR 读的，屏幕文字可能不准。在“系统设置 → 大模型”里给“现场手操器故障识别”选一个支持视觉的模型后会自动改用它。', true);
        else notice('故障文字已读出，保存前请再确认。');
      }
      photo.recognition_status = 'done';
      await storePhoto(photo);
      URL.revokeObjectURL(drawn.url);
    } catch (error) {
      photo.recognition_status = navigator.onLine ? 'failed' : 'offline';
      await storePhoto(photo);
      recognitionFailure(error);
    } finally { URL.revokeObjectURL(preview); ocrBusy = false; }
  }
  function unionGroupFields(photos) {
    const fields = {equipment_number:'', equipment_model:'', container_number:'', position_number:'', fault:'', sources:{}};
    for (const photo of photos || []) {
      const role = photo.photo_role || '';
      const number = photo.recognized_number || (role === 'nameplate' ? photo.equipment_number : '') || '';
      const model = photo.recognized_model || (role === 'nameplate' ? (photo.photo_model || photo.equipment_model) : '') || '';
      const container = photo.recognized_container || (role === 'container' ? photo.container_number : '') || '';
      const position = photo.recognized_position || (role === 'position' ? photo.position_number : '') || '';
      const fault = photo.recognized_fault || (role === 'controller' ? photo.fault_note : '') || '';
      if (number) { fields.equipment_number = number; fields.sources.nameplate = photo.client_id || photo.id; }
      if (model) fields.equipment_model = model;
      if (container) { fields.container_number = container; fields.sources.container = photo.client_id || photo.id; }
      if (position) { fields.position_number = position; fields.sources.position = photo.client_id || photo.id; }
      if (fault) { fields.fault = fault; fields.sources.controller = photo.client_id || photo.id; }
      // A container photo must not own the position. If 62-S1 was stored there, offer it as the position.
      if (role === 'container' && !container && photo.position_number && !/^[A-Z]{4}\d{7}$/.test(String(photo.position_number).toUpperCase()) && !fields.position_number) {
        fields.position_number = photo.position_number;
      }
      if (!role) {
        if (!fields.equipment_number && photo.equipment_number) fields.equipment_number = photo.equipment_number;
        if (!fields.equipment_model && (photo.photo_model || photo.equipment_model)) fields.equipment_model = photo.photo_model || photo.equipment_model;
        if (!fields.container_number && photo.container_number) fields.container_number = photo.container_number;
        if (!fields.position_number && photo.position_number) fields.position_number = photo.position_number;
        if (!fields.fault && photo.fault_note) fields.fault = photo.fault_note;
      }
    }
    return fields;
  }
  function groupFieldsFrom(photos, inheritTyped = true) {
    const fields = unionGroupFields(photos);
    // Values already entered on the capture screen must not come back blank.
    const pending = (batch && batch.pending) || {};
    const typed = inheritTyped ? {
      equipment_number: ($('noEquipmentNumber')?.checked ? '' : ($('equipmentNumber')?.value || '')).trim(),
      equipment_model: ($('equipmentModel')?.value || '').trim(),
      container_number: ($('containerNumber')?.value || '').trim(),
      position_number: ($('positionNumber')?.value || '').trim(),
      fault: ($('repairFault')?.value || '').trim(),
    } : {};
    for (const key of Object.keys(typed)) {
      if (fields[key]) continue;
      fields[key] = pending[key] || typed[key];
    }
    if (batch) batch.pending = fields;
    return fields;
  }
  function showGroupConfirm(photos, {inheritTyped = true} = {}) {
    const fields = groupFieldsFrom(photos, inheritTyped);
    const specs = [
      ['equipment_number', '设备编号', 'nameplate'],
      ['equipment_model', '设备型号', 'nameplate'],
      ['container_number', '集装箱号', 'container'],
      ['position_number', '位置号', 'position'],
      ['fault', '故障信息', 'controller']
    ];
    const root = $('groupConfirmFields');
    root.replaceChildren();
    for (const [key, label, role] of specs) {
      const source = photos.find(photo => photo.client_id === fields.sources[role]) || photos.find(photo => photo.photo_role === role);
      const row = document.createElement('label');
      row.className = 'group-field';
      const title = document.createElement('span');
      title.textContent = label;
      const input = document.createElement(key === 'fault' ? 'textarea' : 'input');
      input.value = fields[key] || '';
      input.addEventListener('input', () => {
        fields[key] = input.value;
        const bad = (key === 'equipment_number' && input.value && !/^[A-Za-z0-9]{13}$/.test(input.value))
          || (key === 'container_number' && input.value && !/^[A-Z]{4}\d{7}$/.test(input.value.toUpperCase().replace(/[^A-Z0-9]/g, '')));
        input.classList.toggle('invalid-field', bad);
      });
      input.dispatchEvent(new Event('input'));
      row.append(title, input);
      if (source) {
        const thumb = document.createElement('img');
        thumb.alt = label;
        thumb.src = URL.createObjectURL(source.original_blob || source.blob);
        row.append(thumb);
        const again = document.createElement('button');
        again.type = 'button';
        again.textContent = '用这张再识别';
        again.addEventListener('click', () => runRoleRecognition({...source, photo_role: role}));
        const launch = document.createElement('span');
        launch.className = 'album-launch';
        const fresh = document.createElement('button');
        fresh.type = 'button';
        fresh.textContent = '换一张';
        const file = document.createElement('input');
        file.type = 'file';
        file.className = 'ios-file-input';
        file.accept = 'image/*,.heic,.heif,.avif';
        // No capture attribute: iPhone then offers 拍照 / 照片图库 instead of camera only.
        file.addEventListener('change', async () => {
          const [picked] = chosenFiles(file);
          if (!picked) return;
          source.original_blob = picked;
          source.photo_role = role;
          await runRoleRecognition(source);
          input.value = fields[key] || '';
          input.dispatchEvent(new Event('input'));
        });
        launch.append(fresh, file);
        row.append(again, launch);
      }
      root.append(row);
    }
    const earliest = photos.reduce((min, photo) => {
      const instant = new Date(photo.watermark_at || photo.captured_at);
      return !Number.isNaN(instant.getTime()) && instant < min ? instant : min;
    }, new Date(8640000000000000));
    const timeRow = document.createElement('label');
    timeRow.className = 'group-time';
    const timeTitle = document.createElement('span');
    timeTitle.textContent = '水印时间';
    const timeInput = document.createElement('input');
    timeInput.id = 'groupWatermarkTime';
    timeInput.type = 'datetime-local';
    timeInput.step = '60';
    if (earliest.getTime() < 8640000000000000) {
      const pad = n => String(n).padStart(2, '0');
      timeInput.value = `${earliest.getFullYear()}-${pad(earliest.getMonth()+1)}-${pad(earliest.getDate())}T${pad(earliest.getHours())}:${pad(earliest.getMinutes())}`;
    }
    timeInput.dataset.baseline = timeInput.value;
    const timeHint = document.createElement('small');
    timeHint.className = 'muted';
    timeHint.textContent = '默认是这组最早的拍摄或 EXIF 时间。改动后整组按同样的分钟差平移，并需要验证密码。';
    timeRow.append(timeTitle, timeInput, timeHint);
    root.append(timeRow);
    return new Promise(resolve => { groupConfirmResolve = resolve; $('groupConfirmDialog').showModal(); });
  }
  async function acceptGroupTime() {
    const input = $('groupWatermarkTime');
    if (!input || !batch?.pending) return true;
    if (!input.value) { notice('请选择有效的水印时间。', true); return false; }
    const chosen = new Date(input.value);
    if (Number.isNaN(chosen.getTime())) { notice('请选择有效的水印时间。', true); return false; }
    if (input.value === input.dataset.baseline) { batch.pending.watermark_start = ''; return true; }
    const allowed = await requestTimePassword();
    if (!allowed) return false;
    batch.pending.watermark_start = chosen.toISOString();
    return true;
  }
  async function applyGroupWatermark(photos, fields) {
    fields = fields || ensureGroupPending();
    if (fields.watermark_start) {
      const chosen = new Date(fields.watermark_start);
      const times = photos.map(photo => new Date(photo.watermark_at || photo.captured_at).getTime()).filter(time => !Number.isNaN(time));
      if (!Number.isNaN(chosen.getTime()) && times.length) {
        const earliest = new Date(Math.min(...times));
        const start = new Date(earliest); start.setSeconds(0, 0);
        const target = new Date(chosen); target.setSeconds(0, 0);
        const delta = target.getTime() - start.getTime();
        for (const photo of photos) {
          const base = new Date(photo.watermark_at || photo.captured_at).getTime();
          if (!Number.isNaN(base)) photo.watermark_at = new Date(base + delta).toISOString();
        }
      }
    }
    for (const photo of photos) {
      const optOut = photo.source === 'file' && photo.watermark_opt_out === true;
      photo.equipment_number = fields.equipment_number;
      photo.equipment_model = fields.equipment_model;
      photo.container_number = fields.container_number;
      photo.position_number = fields.position_number;
      photo.fault_note = fields.fault;
      if (optOut) {
        photo.awaiting_watermark = false;
        photo.watermark_source = 'original';
        photo.watermark_opt_out = true;
        photo.blob = photo.original_blob || photo.blob;
        await storePhoto(photo);
        continue;
      }
      photo.watermark_opt_out = false;
      const raw = photo.original_blob || photo.blob;
      let loaded;
      try {
        loaded = await blobImage(raw);
        const context = {...photo, equipment_number:fields.equipment_number, equipment_model:fields.equipment_model,
          container_number:fields.container_number, position_number:fields.position_number, fault_note:fields.fault,
          photo_type_label:photoTypeLabel(photo.photo_type), watermark_source:'system'};
        photo.blob = await processedPhoto(loaded.image, context);
        photo.original_blob = raw;
        photo.awaiting_watermark = false;
        photo.watermark_source = 'system';
        await storePhoto(photo);
      } catch (error) {
        photo.awaiting_watermark = true;
        await storePhoto(photo);
      } finally { if (loaded) URL.revokeObjectURL(loaded.url); }
    }
  }
  $('recognizeDevice').addEventListener('click', () => openCamera(true));
  $('takePhoto').addEventListener('click', async () => {
    if (document.body.classList.contains('recognition-mode')) {
      if (!document.body.classList.contains('controller-camera')) await recognizeDevice();
      return;
    }
    if (taking || switchingCamera || !stream) return;
    if (!requireDeviceFields()) return;
    taking = true; $('takePhoto').disabled = true;
    try { const context = await makeContext('camera'); if (context) await captureRawAndClassify($('viewfinder'), context); }
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
    if (batch.type === 'equipment' && repairStage === 'after' && !activeRepair) { reject('请先选择维修记录。'); return; }
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
    const session = batch?.session_id;
    if (!session) { clearRecentCaptures(); return; }
    photos = photos.filter(photo => photo.capture_session === session || photo.batch_id === session || String(photo.batch_id || '').startsWith(session + ':'));
    const pending = new Map(photos.map(photo => [photo.client_id, photo]));
    for (const [id, view] of recentCaptureViews) {
      if (view.photo.user_id !== profile?.user.id || (!pending.has(id) && !view.uploaded)) {
        URL.revokeObjectURL(view.url); recentCaptureViews.delete(id);
      }
    }
    for (const photo of photos) {
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
      const status = textNode('span', ROLE_LABEL[view.photo.photo_role] || '未分类', 'role-badge');
      button.title = fieldText('照片预览');
      button.append(image, status);
      image.onerror = () => { image.hidden = true; };
      button.addEventListener('click', () => openDraft(view.photo));
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
    const position = photo.photo_type === 'equipment'
      ? (photo.position_number || '').replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 100) : '';
    const stamp = photo.filename_time;
    const stem = stamp ? [position, stamp].filter(Boolean).join('_') : photo.order_number+'-'+photo.client_id;
    const extension = /\.[A-Za-z0-9]{1,8}$/.exec(photo.original_filename || '')?.[0] || '.jpg';
    const name = stem + extension;
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
    $('draftDialog').classList.toggle('controller-photo-preview', Boolean(photo.stagedFault));
    $('retryFaultDraft').hidden = !photo.stagedFault || Boolean(activeRepair);
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
      const pending = (await queued(batchId)).filter(photo => !photo.pending_device); uploadTotal = pending.length;
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
          const skip = new Set(['blob','error','original_blob','original_blob_type','sources','recognized_number','recognized_model','recognized_container','recognized_position','recognized_fault','watermark_opt_out']);
          Object.entries(photo).forEach(([k,v]) => { if (!skip.has(k) && v != null && typeof v !== 'object') data.append(k,String(v)); });
          data.append('photo',photo.blob,photo.original_filename || photo.client_id+'.jpg');
          const optOut = photo.source === 'file' && photo.watermark_opt_out === true;
          data.append('client_watermarked', String(!optOut && photo.awaiting_watermark !== true));
          data.append('watermark_opt_out', optOut ? 'true' : 'false');
          if (photo.original_blob instanceof Blob) data.append('original', photo.original_blob, photo.client_id+'.original.jpg');
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
          renderCameraDeviceInfo();
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
  async function pushGroupFieldsToRepair(fields) {
    // The repair ledger row is created before the shots, so corrections made in the
    // group confirm sheet have to be written back or the ledger keeps showing blanks.
    if (!activeRepair || !fields) return;
    const order = chosenOrder();
    if (!order || activeRepair.order_id !== order.id || activeRepair.completed_date) return;
    try {
      await repairRequest(order, {id: activeRepair.id, action: 'update_device',
        equipment_number: fields.equipment_number || '', equipment_model: fields.equipment_model || '',
        position_number: fields.position_number || '', container_number: fields.container_number || '',
        fault: fields.fault || ''});
      Object.assign(activeRepair, {
        equipment_number: fields.equipment_number || '', equipment_model: fields.equipment_model || '',
        position_number: fields.position_number || '', container_number: fields.container_number || '',
        fault: fields.fault || activeRepair.fault});
    } catch (_) {}
  }
  async function completeBatch() {
    if (!batch) return;
    const session = batch.session_id || batch.id;
    const inSession = photo => photo.capture_session === session || photo.batch_id === session || String(photo.batch_id || '').startsWith(session + ':');
    let groupPhotos = (await queued()).filter(inSession);
    if (groupPhotos.some(photo => photo.pending_device)) {
      notice('请先填齐设备资料并保存维修前记录，铭牌照片才能入账。',true);
      return;
    }
    if (navigator.onLine) {
      for (const photo of groupPhotos) {
        if (photo.recognition_status === 'offline' && RECOGNIZE_ROLES.has(photo.photo_role)) {
          try { await runRoleRecognition(photo); } catch (_) {}
        }
      }
      groupPhotos = (await queued()).filter(inSession);
    }
    if (groupPhotos.length) {
      // 进场 / 离场 / 自检 组没有设备字段可填，直接用水印时间归档，不再弹确认窗口。
      const deviceGroup = groupPhotos.some(photo => (photo.photo_type || 'equipment') === 'equipment');
      if (deviceGroup) {
        const accepted = await showGroupConfirm(groupPhotos);
        if (!accepted) return;
      } else {
        ensureGroupPending();
      }
      await applyGroupWatermark(groupPhotos);
      await pushGroupFieldsToRepair(batch && batch.pending);
    }
    for (const batchId of [...new Set(groupPhotos.map(photo => photo.batch_id))]) await syncQueue(batchId);
    if ((await queued()).filter(inSession).length) { notice('部分照片尚未上传，请检查网络后重试。',true); return; }
    stopCamera(); batch=null; deviceSession=null; timeAuthorized=false; useSystemTime=true; $('timeSettings').hidden=false; $('deviceSession').hidden=false; $('watermarkModeText').textContent='使用当前系统时间'; $('watermarkStart').value=''; $('watermarkPassword').value=''; setFieldText('passwordStatus',''); if ($('watermarkTimeDialog').open) $('watermarkTimeDialog').close(); if ($('watermarkPasswordDialog').open) $('watermarkPasswordDialog').close();
    clearDeviceInputs();  // 本组结束：清空设备输入框，下一组不得残留本组设备信息
    setKindChoiceActive(null); setFieldText('kindStatus','拍照后在照片上选择类型。'); $('photoNote').value=''; resizePhotoNote(); setUploadWatermark(false);
    notice('本组照片已全部上传，可以继续拍照。'); await renderQueue();
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
    const previousRepairId = activeRepair?.id;
    const typedWork = $('repairWork').value.trim();
    const typedResult = $('repairResult').value.trim();
    activeRepair = repair ? {...repair, repair_phase:phase} : null;
    repairStartId = null;
    deviceSession = null;
    $('repairResultFields').hidden = !repair;
    $('repairSummaryPosition').textContent = repair?.position_number || '—';
    $('repairSummaryContainer').textContent = repair?.container_number || '—';
    $('repairSummaryNumber').textContent = repair?.equipment_number || '—';
    $('repairSummaryModel').textContent = repair?.equipment_model || '—';
    $('repairOriginalFault').textContent = repair?.fault || '无已记录故障';
    $('startRepair').disabled = Boolean(repair);
    $('scanRepairFault').disabled = Boolean(repair);
    $('clearRepairFault').disabled = Boolean(repair);
    for (const id of ['equipmentNumber','equipmentModel','positionNumber','containerNumber','repairFault']) {
      $(id).readOnly = Boolean(repair);
    }
    $('noEquipmentNumber').disabled = Boolean(repair);
    $('recognizeDevice').disabled = Boolean(repair);

    $('repairStage').textContent = phase === 'before' ? '当前拍摄：维修前' : '当前拍摄：维修后';
    $('completeRepair').disabled = !repair;
    // 重新拍摄同一台设备的照片时不能清空已输入的工作内容；换记录时显示这条记录已保存的内容。
    const sameRepair = Boolean(repair && previousRepairId && repair.id === previousRepairId);
    $('repairWork').value = sameRepair && typedWork ? typedWork : (repair?.work_content || '');
    $('repairResult').value = sameRepair && typedResult ? typedResult : (repair?.result || '');
    if (repair && phase === 'before') $('repairFault').value = repair.fault || '';
    if (repair) {
      $('equipmentNumber').closest('fieldset').classList.remove('locked');
      $('equipmentNumber').value = repair.equipment_number;
      $('equipmentModel').value = repair.equipment_model;
      $('positionNumber').value = repair.position_number;
      $('containerNumber').value = repair.container_number;
      $('noEquipmentNumber').checked = !repair.equipment_number;
      $('equipmentNumber').disabled = false;
      // 接手别人未完成的维修时，施工员（维修人）默认用当前登录的新维修人；
      // 用户如果已经主动选了其他施工员，则保留用户的选择。
      if (repair.is_mine === false && (!$('technicianSelect').value || $('technicianSelect').value === String(repair.user_id))) {
        $('technicianSelect').value = String(profile.user.id);
      }
    }
    renderCameraDeviceInfo();
  }
  function clearFaultPhotos() {
    for (const photo of faultPhotos) URL.revokeObjectURL(photo.preview);
    faultPhotos = [];
    showFaultAlbumStatus('');
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
  function requireAfterWork() {
    if (!activeRepair) {
      notice('请先按位置号选择维修记录。', true);
      $('pendingRepairs').focus();
      return null;
    }
    const input = $('repairWork');
    const work = input.value.trim();
    if (work) return work;
    notice('请先填写工作内容，再拍摄或选择维修后照片。', true);
    input.focus();
    return null;
  }
  function prepareAfterPhoto() {
    if (!activeRepair || taking || processingFiles || captureContext || syncing || ocrBusy) return false;
    if (!requireAfterWork()) return false;
    activeRepair.repair_phase = 'after';
    if (deviceSession) deviceSession.repair_phase = 'after';
    $('repairStage').textContent = '当前拍摄：维修后';
    return true;
  }
  $('repairAfterPhoto').addEventListener('click', () => {
    if (prepareAfterPhoto()) { setCaptureMode('camera'); openCamera(); }
  });
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
      const label = [row.position_number,row.equipment_number,row.container_number].filter(Boolean).join(' · ') || row.id.slice(0,8);
      const owner = row.is_mine === false && row.owner_name ? ` · ${fieldText('维修人员')} ${row.owner_name}` : '';
      $('pendingRepairs').add(new Option(label + owner,row.id));
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
    if (!completeDeviceFields(repair)) { notice('请先填齐设备编号、型号、位置号和集装箱号。',true); return; }
    if (!repair.fault) { notice('请先记录故障现象。',true); return; }
    const button = $('startRepair'); button.disabled = true;
    try {
      await repairRequest(order, repair);
      if (chosenOrder()?.id !== order.id) return;
      stopCamera();
      for (const photo of (await queued()).filter(item => item.pending_device && item.order_id === order.id && item.batch_id === batch?.id)) {
        await storePhoto({...photo, pending_device:false, equipment_session:repair.id, repair_phase:'before', awaiting_watermark:true,
          equipment_number:repair.equipment_number, equipment_model:repair.equipment_model,
          position_number:repair.position_number, container_number:repair.container_number});
      }
      for (const photo of faultPhotos) {
        const context = {...photo.context, equipment_session:repair.id,
          equipment_number:repair.equipment_number, equipment_model:repair.equipment_model, photo_role:'controller',
          position_number:repair.position_number, container_number:repair.container_number, repair_phase:'before'};
        if (context.watermark_opt_out) await keepOriginalFile(photo.file, context);
        else await keepCapture(photo.canvas, {...context, watermark_source:'system'});
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
      const pending = (await queued()).filter(photo => photo.equipment_session === repair.id).length;
      notice(pending ? `本台维修已完成，${pending} 张照片待上传。` : '本台维修已完成。');
      await refreshPendingRepairs();
    } catch(error) { notice(error.message,true); }
    finally { button.disabled = !activeRepair; }
  });
  $('saveWorkLog').addEventListener('click', async () => {
    const order = chosenOrder(), work = $('repairWork').value.trim();
    if (!order || !profile) return;
    if (!work) { notice('请填写本次工作内容。', true); return; }
    if (!activeRepair) { notice('请先保存维修前状态，工作内容会记在这条维修记录上。', true); return; }
    const button = $('saveWorkLog'); button.disabled = true;
    try {
      await repairRequest(order, {id: activeRepair.id, action: 'save_work', work_content: work});
      notice('工作内容已保存。');
    } catch (error) { notice(error.message, true); }
    finally { button.disabled = false; }
  });
  $('scanRepairFault').addEventListener('click', () => { if (readyForController()) openCamera(true, true); });
  $('readRepairFault').addEventListener('click', () => recognizeDevice(false, true));

  $('cancelRecognize').addEventListener('click', () => { recognizeCancelled = true; recognizeAbort?.abort(); hideRecognizeProgress(); });
  $('overlayRetry').addEventListener('click', () => { const file = recognizeKind === 'fault' ? faultBlob : nameplateBlob; if (file) (recognizeKind === 'fault' ? recognizeFaultFile : recognizeNameplateFile)(file); });
  $('overlayManual').addEventListener('click', () => { hideRecognizeProgress(); if (recognizeKind === 'fault') $('repairFault').focus(); else { $('equipmentNumber').disabled = false; $('equipmentNumber').focus(); } });
  $('confirmRecognizedNumber').addEventListener('click', () => {
    const value = $('recognizedNumber').textContent.trim();
    if (value) {
      $('equipmentNumber').disabled = false; $('noEquipmentNumber').checked = false;
      $('equipmentNumber').value = value;
    }
    const model = $('recognizedModel').textContent.trim();
    if (model) $('equipmentModel').value = model;
    deviceSession = null; $('recognitionDialog').close();
    $('nameplatePending').hidden = true; $('nameplatePendingConfirm').hidden = true;
    notice(value && model ? '已填入设备编号和型号，请核对。' : '已填入识别结果，请补充另一项。');
  });
  $('nameplatePendingConfirm').addEventListener('click', () => {
    if ($('recognitionDialog').open) return;
    if (!$('recognizedNumber').textContent.trim() && nameplateBlob) { recognizeNameplateFile(nameplateBlob); return; }
    $('recognitionDialog').showModal();
  });
  $('retryRecognitionFile').addEventListener('change', () => { const [file] = chosenFiles($('retryRecognitionFile')); $('recognitionDialog').close(); if (file) recognizeNameplateFile(file); });
  $('retrySamePhoto').addEventListener('click', () => {
    $('recognitionDialog').close();
    if (nameplateBlob) recognizeNameplateFile(nameplateBlob);
  });
  $('retryAlbumFile').addEventListener('change', () => { const [file] = chosenFiles($('retryAlbumFile')); $('recognitionDialog').close(); if (file) recognizeNameplateFile(file); });
  async function reclassifyPhoto(photo) {
    const role = await askPhotoRole();
    if (!role) return;
    applyPhotoRole(photo, role);
    if (!RECOGNIZE_ROLES.has(photo.photo_role) || !navigator.onLine) {
      photo.recognition_status = RECOGNIZE_ROLES.has(photo.photo_role) ? 'offline' : 'skipped';
      await storePhoto(photo);
      if (photo.recognition_status === 'offline') notice('已记下照片类型，联网后会自动识别。');
      await renderQueue();
      return;
    }
    await runRoleRecognition(photo);
    await renderQueue();
  }
  let albumDestination = '';
  function chooseAlbumWatermark(destination) {
    if (taking || ocrBusy || syncing || !identityReady || !profile?.can_capture) return;
    if ((destination === 'repair' ||
        (destination === 'general' && batch?.type === 'equipment' && repairStage === 'after')) &&
        !requireAfterWork()) return;
    albumDestination = destination;
    for (const id of ['albumFileWithWatermark','albumFileWithoutWatermark']) {
      $(id).multiple = destination === 'general' || destination === 'repair';
    }
    $('albumWatermarkChoice').showModal();
  }
  $('shutterButton').addEventListener('click', () => openCamera(false));
  $('cameraAlbumButton').addEventListener('click', () => chooseAlbumWatermark('general'));
  $('repairAlbumButton').addEventListener('click', () => chooseAlbumWatermark('repair'));
  $('cameraBarAlbumButton').addEventListener('click', () => {
    if (document.body.classList.contains('controller-camera')) chooseAlbumWatermark('fault');
    else if (document.body.classList.contains('recognition-mode')) chooseAlbumWatermark('nameplate');
    else chooseAlbumWatermark('general');
  });
  for (const [id, addWatermark] of [['albumFileWithWatermark',true],['albumFileWithoutWatermark',false]]) {
    $(id).addEventListener('change', () => {
      const files = chosenFiles($(id)), destination = albumDestination;
      if (!files.length || !$('albumWatermarkChoice').open) return;
      $('albumWatermarkChoice').close();
      if (destination === 'fault') recognizeFaultFile(files[0], addWatermark);
      else if (destination === 'nameplate') recognizeNameplateFile(files[0], addWatermark);
      else if (destination === 'general' || (destination === 'repair' && prepareAfterPhoto())) saveAlbumLikeCamera(files, addWatermark);
    });
  }
  for (const [id, role] of [['roleArrival','arrival'],['roleDeparture','departure'],['roleSafety','safety'],['roleNameplate','nameplate'],['roleContainer','container'],['rolePosition','position'],['roleController','controller'],['roleOther','other']]) {
    $(id).addEventListener('click', () => finishPhotoRole(role));
  }
  $('photoRoleDialog').addEventListener('cancel', event => { event.preventDefault(); finishPhotoRole('other'); });
  $('cancelGroupConfirm').addEventListener('click', () => { $('groupConfirmDialog').close(); groupConfirmResolve?.(false); groupConfirmResolve = null; });
  $('confirmGroupSave').addEventListener('click', async () => {
    if ($('confirmGroupSave').disabled) return;
    $('confirmGroupSave').disabled = true;
    try {
      const accepted = await acceptGroupTime();
      if (!accepted) return;
      $('groupConfirmDialog').close();
      groupConfirmResolve?.(true);
      groupConfirmResolve = null;
    } finally { $('confirmGroupSave').disabled = false; }
  });
  $('groupConfirmDialog').addEventListener('cancel', event => { event.preventDefault(); $('cancelGroupConfirm').click(); });
  $('manualRecognition').addEventListener('click', () => { $('recognitionDialog').close(); $('nameplatePending').hidden = true; $('nameplatePendingConfirm').hidden = true; $('equipmentNumber').focus(); });
  $('editGroupWatermark').addEventListener('click', async () => {
    const photo = ledgerPhotos[ledgerPhotoIndex];
    if (!photo?.batch_id) { notice('这张照片没有分组，不能整组重做水印。', true); return; }
    const sessionKey = String(photo.batch_id).split(':')[0];
    const group = ledgerPhotos.filter(item => item.batch_id && (item.batch_id === photo.batch_id || String(item.batch_id).split(':')[0] === sessionKey));
    const savedBatch = batch;
    batch = {id:photo.batch_id, session_id:sessionKey, type:photo.photo_type || 'equipment', pending:unionGroupFields(group.length ? group : [photo])};
    const accepted = await showGroupConfirm(group.length ? group : [photo], {inheritTyped: false});
    const fields = {...batch.pending};
    batch = savedBatch;
    if (!accepted) return;
    const response = await requestAPI('/api/field/photos/rewatermark', {method:'POST', headers:{'Content-Type':'application/json','X-Field-Token':profile.csrf}, body:JSON.stringify({batch_id:photo.batch_id, equipment_number:fields.equipment_number, equipment_model:fields.equipment_model, container_number:fields.container_number, position_number:fields.position_number, fault:fields.fault, watermark_start:fields.watermark_start || ''})});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { notice(result.error || '水印没有更新。', true); return; }
    notice('已按确认的信息重做这组水印。');
    await loadLedger();
  });
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
  function resizePhotoNote() {
    const note = $('photoNote');
    if (!note.getClientRects().length) return;
    note.style.height = 'auto';
    const border = note.offsetHeight - note.clientHeight;
    note.style.height = note.scrollHeight + border + 'px';
  }
  $('photoNote').addEventListener('input', resizePhotoNote);
  $('captureExtras').addEventListener('toggle', resizePhotoNote);
  window.addEventListener('resize', resizePhotoNote);
  $('repairFault').addEventListener('compositionstart', () => { $('repairFault').dataset.composing = '1'; });
  $('repairFault').addEventListener('compositionend', () => { delete $('repairFault').dataset.composing; limitFaultField(); });
  $('repairFault').addEventListener('input', limitFaultField);
  $('repairFault').addEventListener('blur', limitFaultField);
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
  $('flipSafetyCamera').addEventListener('click',flipSafetyCamera);
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
  $('retryUpload').addEventListener('click', () => { syncQueue(); }); $('reloadOrders').addEventListener('click',bootstrap);
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
  $('retryFaultDraft').addEventListener('click', () => {
    const file = draftSelection?.stagedFault?.file;
    if (!file || activeRepair || ocrBusy) return;
    $('draftDialog').close();
    recognizeFaultFile(file);
  });
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
