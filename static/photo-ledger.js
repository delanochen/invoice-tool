// 照片台账：行选中、批量删除、预览弹窗删除
(() => {
  const table = document.querySelector('.photo-query-table');
  if (!table) return;

  const batchBar = document.getElementById('photoBatchDownload');
  const deleteUrl = batchBar?.dataset.deleteUrl || '/api/field/photos/delete';
  const csrfToken = batchBar?.dataset.csrf || '';
  const deleteButton = document.getElementById('deleteSelectedPhotos');
  const countSpan = document.getElementById('selectedPhotoCount');
  const selectAll = document.getElementById('selectAllPhotos');
  const previewDeleteButton = document.getElementById('previewDeletePhoto');
  const previewDialog = document.getElementById('imageAttachmentPreviewDialog');

  let currentPreviewToken = null;
  let currentPreviewRow = null;

  // 获取所有可删除的行
  function getDeletableRows() {
    return Array.from(table.querySelectorAll('tr.photo-deletable'));
  }

  // 获取选中的行
  function getSelectedRows() {
    return getDeletableRows().filter(row => row.querySelector('.photo-select-checkbox')?.checked);
  }

  // 更新选中计数和按钮状态
  function updateSelectionState() {
    const selected = getSelectedRows();
    const count = selected.length;
    if (countSpan) countSpan.textContent = count;
    if (deleteButton) deleteButton.disabled = count === 0;
    if (selectAll) {
      const deletable = getDeletableRows();
      selectAll.checked = deletable.length > 0 && selected.length === deletable.length;
      selectAll.indeterminate = selected.length > 0 && selected.length < deletable.length;
    }
  }

  // 切换行选中状态
  function toggleRow(row, checked) {
    const checkbox = row.querySelector('.photo-select-checkbox');
    if (!checkbox) return;
    checkbox.checked = checked !== undefined ? checked : !checkbox.checked;
    row.classList.toggle('photo-row-selected', checkbox.checked);
    updateSelectionState();
  }

  // 行点击选中（排除链接和输入元素）
  table.addEventListener('click', event => {
    const row = event.target.closest('tr.photo-deletable');
    if (!row) return;
    if (event.target.closest('a, button, input, select, textarea')) return;
    event.preventDefault();
    toggleRow(row);
  });

  // 复选框变化
  table.addEventListener('change', event => {
    if (!event.target.classList.contains('photo-select-checkbox')) return;
    const row = event.target.closest('tr.photo-deletable');
    if (row) {
      row.classList.toggle('photo-row-selected', event.target.checked);
      updateSelectionState();
    }
  });

  // 全选/取消全选
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      getDeletableRows().forEach(row => toggleRow(row, selectAll.checked));
    });
  }

  // 调用删除 API
  async function deletePhoto(token, photoId) {
    const response = await fetch(deleteUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Field-Token': csrfToken },
      body: JSON.stringify({ token }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `删除失败 (${response.status})`);
    }
    return data;
  }

  // 从 DOM 移除行
  function removeRow(photoId) {
    const row = table.querySelector(`tr[data-photo-id="${photoId}"]`);
    if (row) row.remove();
  }

  // 批量删除
  if (deleteButton) {
    deleteButton.addEventListener('click', async () => {
      const selected = getSelectedRows();
      if (selected.length === 0) return;

      const confirmed = window.confirm(`确认删除选中的 ${selected.length} 张照片？\n此操作无法撤销，照片文件将从服务器删除。`);
      if (!confirmed) return;

      deleteButton.disabled = true;
      const originalText = deleteButton.innerHTML;
      let successCount = 0;
      let failCount = 0;

      for (let i = 0; i < selected.length; i++) {
        const row = selected[i];
        const token = row.dataset.deleteToken;
        const photoId = row.dataset.photoId;
        deleteButton.innerHTML = `删除中 ${i + 1}/${selected.length}...`;
        try {
          await deletePhoto(token, photoId);
          removeRow(photoId);
          successCount++;
        } catch (error) {
          failCount++;
          console.error('删除照片失败:', photoId, error);
        }
      }

      deleteButton.innerHTML = originalText;
      updateSelectionState();

      if (failCount > 0) {
        alert(`删除完成：成功 ${successCount} 张，失败 ${failCount} 张。\n请刷新页面后重试失败的照片。`);
      } else {
        alert(`已成功删除 ${successCount} 张照片。`);
      }
      location.reload();
    });
  }

  // 预览弹窗删除：监听照片链接点击，保存删除 token
  document.addEventListener('pointerup', event => {
    const link = event.target.closest?.('[data-image-preview]');
    if (!link) return;
    currentPreviewToken = link.dataset.previewDeleteToken || null;
    currentPreviewRow = link.closest('tr.photo-deletable') || null;
  }, true);

  // 弹窗打开时控制删除按钮显示
  if (previewDialog && previewDeleteButton) {
    const observer = new MutationObserver(() => {
      if (previewDialog.open) {
        previewDeleteButton.style.display = currentPreviewToken ? '' : 'none';
      }
    });
    observer.observe(previewDialog, { attributes: true, attributeFilter: ['open'] });

    previewDeleteButton.addEventListener('click', async () => {
      if (!currentPreviewToken) return;
      const photoId = currentPreviewRow?.dataset.photoId || '当前照片';
      const confirmed = window.confirm(`确认删除这张照片？\n此操作无法撤销，照片文件将从服务器删除。`);
      if (!confirmed) return;

      previewDeleteButton.disabled = true;
      previewDeleteButton.textContent = '删除中...';
      try {
        await deletePhoto(currentPreviewToken, photoId);
        previewDialog.close();
        if (currentPreviewRow) currentPreviewRow.remove();
        alert('照片已删除。');
        location.reload();
      } catch (error) {
        alert(`删除失败：${error.message}`);
        previewDeleteButton.disabled = false;
        previewDeleteButton.textContent = '删除照片';
      }
    });
  }

  // 初始化
  updateSelectionState();
})();
