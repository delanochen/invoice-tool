/* Shared compact file picker; preserves the original input and upload handlers. */
(() => {
  const t = text => window.uiTranslate?.(text) || text;
  function setup(input) {
    if (input.dataset.filePickerReady || input.closest('.photo-local-picker,.native-photo-picker') || input.id === 'clockCamera') return;
    input.dataset.filePickerReady = 'true';
    input.classList.add('unified-file-input');
    const control = document.createElement('span');
    control.className = 'unified-file-control';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'erp-btn unified-file-button';
    button.textContent = t('上传附件');
    const status = document.createElement('span');
    status.className = 'unified-file-status';
    status.setAttribute('aria-live', 'polite');
    const sync = () => {
      button.disabled = input.disabled;
      const files = Array.from(input.files || []);
      status.textContent = files.length === 1 ? files[0].name : files.length ? files.length + ' ' + t('个文件已选择') : '';
      status.hidden = !files.length || !!input.dataset.pendingAttachmentReady;
    };
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      input.click();
    });
    input.insertAdjacentElement('afterend', control);
    control.append(button, status);
    input.addEventListener('change', () => queueMicrotask(sync));
    input.form?.addEventListener('reset', () => setTimeout(sync, 0));
    new MutationObserver(sync).observe(input, {attributes:true, attributeFilter:['disabled']});
    sync();
  }
  function scan(node) {
    if (!(node instanceof Element)) return;
    if (node.matches('input[type="file"]')) setup(node);
    node.querySelectorAll('input[type="file"]').forEach(setup);
  }
  scan(document.body);
  new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(scan)))
    .observe(document.body, {childList:true, subtree:true});
})();
