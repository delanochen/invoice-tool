/* Delete selected records locally while preserving the surrounding list. */
(() => {
  const t = value => window.uiTranslate ? window.uiTranslate(value) : value;
  document.querySelectorAll('[data-local-list]').forEach(scope => {
    const table = scope.querySelector('[data-selectable-table]');
    if (!table) return;
    const actions = () => scope.querySelectorAll('[data-row-action], [data-row-action-form] button');
    const sync = () => {
      const row = table.querySelector('tr[data-row-id].is-selected');
      const panel = table.closest('[data-erp-panel]');
      actions().forEach(button => {
        const form = button.closest('[data-row-action-form]');
        const key = form?.dataset.rowActionForm || button.dataset.rowAction;
        button.disabled = !!scope.dataset.deletePending || !panel.classList.contains('active') || !row?.dataset[key];
      });
    };
    // In particular, project-list actions must not act on a hidden selection
    // while the expense mapping tab is open.
    scope.addEventListener('click', () => queueMicrotask(sync));
    scope.addEventListener('keydown', () => queueMicrotask(sync));
    scope.querySelectorAll('form[data-local-delete]').forEach(form => {
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const row = table.querySelector('tr[data-row-id].is-selected');
        if (!row || scope.dataset.deletePending || !row.dataset.deleteUrl) return;
        if (!window.confirm(t(form.dataset.confirm))) return;
        const url = row.dataset.deleteUrl;
        scope.dataset.deletePending = 'true';
        sync();
        try {
          const response = await fetch(url, {
            method: 'POST', body: new FormData(form), credentials: 'same-origin',
            headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
          });
          const result = response.headers.get('content-type')?.includes('application/json')
            ? await response.json() : null;
          if (!response.ok || !result?.ok) throw new Error(result?.message || t('删除失败，请重试。'));
          const instance = window.systemGrids?.instances.get(table);
          if (instance?.ready) {
            // Delete only this grid row; keep sorting, filters and scroll position.
            const holder = instance.host.querySelector('.tabulator-tableholder');
            const scrollTop = holder?.scrollTop;
            const scrollLeft = holder?.scrollLeft;
            await instance.grid.deleteRow(row._gridId);
            row.remove();
            instance.rows.delete(row._gridId);
            instance.observer.takeRecords();
            if (holder) {
              holder.scrollTop = scrollTop;
              holder.scrollLeft = scrollLeft;
            }
          } else {
            row.remove();
          }
          if (!table.querySelector('tbody tr[data-row-id]')) {
            const empty = table.tBodies[0].insertRow();
            const cell = empty.insertCell();
            cell.colSpan = table.tHead.rows[0].cells.length;
            cell.className = 'empty';
            cell.textContent = t('没有符合条件的记录。');
            instance?.observer.takeRecords();
          }
          scope.querySelectorAll('[data-list-count]').forEach(node => {
            node.textContent = table.querySelectorAll('tbody tr[data-row-id]').length;
          });
          const dialog = document.getElementById(row.dataset.editTarget);
          dialog?.remove();
          scope.querySelectorAll('[data-row-action-form]').forEach(actionForm => {
            actionForm.removeAttribute('action');
            actionForm.dataset.selectedValue = '';
          });
        } catch (error) {
          window.alert(t(error.message || '删除失败，请重试。'));
        } finally {
          delete scope.dataset.deletePending;
          // The selected row may have changed while the request was running.
          const selected = table.querySelector('tr[data-row-id].is-selected');
          if (selected) selected.click();
          sync();
        }
      });
    });
    sync();
  });
})();
