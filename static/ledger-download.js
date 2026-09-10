(() => {
  const root = document.getElementById('photoBatchDownload');
  if (!root) return;
  const dialog = document.createElement('dialog');
  dialog.style.cssText = 'max-width:440px;width:calc(100% - 48px);border:1px solid #ccd6df;border-radius:12px;padding:20px;';
  const title = document.createElement('h2'); title.textContent = '批量分享照片';
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  const prepare = document.createElement('button'); prepare.type = 'button'; prepare.textContent = '准备照片';
  const share = document.createElement('button'); share.type = 'button'; share.textContent = '分享本批照片'; share.hidden = true;
  const zip = document.createElement('a'); zip.className = 'button'; zip.textContent = '下载全部照片 ZIP';
  const close = document.createElement('button'); close.type = 'button'; close.textContent = '关闭';
  const actions = document.createElement('div'); actions.style.cssText = 'display:flex;flex-wrap:wrap;gap:10px;';
  actions.append(prepare, share, zip, close); dialog.append(title, status, actions); document.body.append(dialog);
  let photos = [], offset = 0, files = [], controller = null, generation = 0;
  const clear = () => { generation++; controller?.abort(); files = []; photos = []; };
  const fail = message => { status.textContent = message; prepare.disabled = false; };
  root.querySelector('[data-save-photos]').addEventListener('click', async () => {
    clear(); offset = 0; const version = generation;
    controller = new AbortController(); const signal = controller.signal;
    share.hidden = true; prepare.hidden = true; prepare.disabled = true;
    zip.href = '/api/field/photos.zip?' + root.dataset.photoQuery;
    status.textContent = '正在查询照片…'; dialog.showModal();
    try {
      const response = await fetch('/api/field/photos?' + root.dataset.photoQuery, {cache:'no-store', signal});
      if (!response.ok) throw new Error('无法读取照片，请确认已登录。');
      const result = await response.json();
      if (version !== generation) return;
      if (result.truncated) throw new Error('超过 2000 张，请缩小筛选范围。');
      photos = result.rows;
      if (!photos.length) throw new Error('没有符合条件的照片。');
      if (!navigator.share || !navigator.canShare?.({files:[new File(['test'], 'test.jpg', {type:'image/jpeg'})]})) {
        status.textContent = `共 ${photos.length} 张。此浏览器不支持批量分享图片，请下载 ZIP 后解压保存。`; return;
      }
      status.textContent = `共 ${photos.length} 张。每批最多 20 张，准备后可通过系统菜单分享给他人，也可选择保存到相册。`;
      prepare.textContent = '准备照片'; prepare.disabled = false; prepare.hidden = false;
    } catch (error) { if (version === generation && error.name !== 'AbortError') fail(error.message); }
  });
  prepare.addEventListener('click', async () => {
    const version = generation; const signal = controller.signal;
    prepare.disabled = true; share.hidden = true; files = []; let size = 0;
    try {
      for (let index = offset; index < Math.min(offset + 20, photos.length); index++) {
        status.textContent = `正在准备第 ${index + 1}/${photos.length} 张…`;
        const photo = photos[index];
        const response = await fetch(photo.preview, {cache:'no-store', signal});
        if (!response.ok) throw new Error(`第 ${index + 1} 张读取失败，未跳过，请重试或下载 ZIP。`);
        if (!response.headers.get('Content-Type')?.startsWith('image/')) throw new Error('照片读取失败，请重新登录后再试。');
        const bytes = await response.arrayBuffer();
        if (version !== generation) return;
        if (files.length && size + bytes.byteLength > 40 * 1024 * 1024) break;
        if (bytes.byteLength > 40 * 1024 * 1024) throw new Error('这张照片过大，请使用 ZIP 下载。');
        files.push(new File([bytes], `${photo.order_number}-${photo.capture_date}-${photo.id}.jpg`, {type:'image/jpeg'}));
        size += bytes.byteLength;
      }
      if (!navigator.canShare({files})) throw new Error('系统不支持分享这批照片，请使用 ZIP 下载。');
      status.textContent = `已准备第 ${offset + 1}–${offset + files.length} 张，共 ${photos.length} 张。点击下方按钮，选择分享目标或保存图片。`;
      share.hidden = false;
    } catch (error) { if (version === generation && error.name !== 'AbortError') { files = []; fail(error.message); } }
  });
  share.addEventListener('click', async () => {
    if (!files.length) return;
    const version = generation;
    share.disabled = true;
    try {
      // This click supplies the fresh user gesture required by iOS after downloading.
      await navigator.share({files});
      if (version !== generation) return;
      offset += files.length; files = []; share.hidden = true; prepare.disabled = false;
      prepare.hidden = offset >= photos.length; prepare.textContent = '准备下一批';
      status.textContent = offset >= photos.length ? '所有批次已交给系统分享菜单。' : `本批已交给系统分享菜单，还有 ${photos.length - offset} 张待准备。`;
    } catch (error) {
      if (version === generation) status.textContent = error.name === 'AbortError' ? '已取消，可再次分享本批照片。' : '系统未能分享本批照片，请重试或下载 ZIP。';
    } finally { share.disabled = false; }
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', clear);
})();
