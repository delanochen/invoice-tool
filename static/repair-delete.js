(() => {
  const dialog = document.getElementById('deleteRepairDialog');
  if (!dialog) return;
  const confirm = document.getElementById('confirmDeleteRepair');
  const cancel = document.getElementById('cancelDeleteRepair');
  const error = document.getElementById('deleteRepairError');
  let selection = '';
  let busy = false;
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-repair-delete]');
    if (!button || busy) return;
    selection = button.dataset.repairDelete;
    document.getElementById('deleteRepairDevice').textContent = button.dataset.deviceLabel;
    document.getElementById('deleteRepairCount').textContent = button.dataset.photoCount;
    error.hidden = true;
    dialog.showModal();
  });
  cancel.addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  confirm.addEventListener('click', async () => {
    if (busy || !selection) return;
    busy = true;
    confirm.disabled = cancel.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(dialog.dataset.url, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Field-Token': dialog.dataset.csrf},
        body: JSON.stringify({token: selection}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.error || '删除失败，请刷新后重试。');
      if (result.cleanup_pending) window.alert('记录和照片已从台账移除，服务器临时文件清理失败，请联系管理员。');
      window.parent.postMessage({type:'workspace:saved'}, location.origin);
      location.reload();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      busy = false;
      confirm.disabled = cancel.disabled = false;
    }
  });
})();
