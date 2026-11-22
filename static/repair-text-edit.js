(() => {
  const dialog = document.getElementById('editRepairDialog');
  const dataNode = document.getElementById('repairEditors');
  if (!dialog || !dataNode) return;
  const editors = JSON.parse(dataNode.textContent);
  const fieldsRoot = document.getElementById('editRepairFields');
  const title = document.getElementById('editRepairTitle');
  const error = document.getElementById('editRepairError');
  const confirm = document.getElementById('confirmEditRepair');
  const cancel = document.getElementById('cancelEditRepair');
  let current = null;
  let busy = false;

  function textarea(kind, index, label, value, maxLength) {
    const wrap = document.createElement('label');
    wrap.className = 'erp-field';
    const caption = document.createElement('span');
    caption.textContent = label;
    const input = document.createElement('textarea');
    input.dataset.kind = kind;
    input.dataset.index = String(index);
    input.maxLength = maxLength;
    input.value = value || '';
    wrap.append(caption, input);
    return wrap;
  }

  function openEditor(key) {
    const editor = editors[key];
    if (!editor || busy) return;
    current = structuredClone(editor);
    title.textContent = editor.title || '';
    fieldsRoot.replaceChildren();
    current.repairs.forEach((repair, index) => {
      const section = document.createElement('section');
      const heading = document.createElement('h3');
      heading.textContent = repair.label;
      section.append(
        heading,
        textarea('fault', index, '故障现象', repair.fault, 4000),
        textarea('work_content', index, '工作内容', repair.work_content, 2000),
        textarea('result', index, '处理结果', repair.result, 1000),
      );
      fieldsRoot.append(section);
    });
    current.notes.forEach((note, index) => {
      const section = document.createElement('section');
      const heading = document.createElement('h3');
      heading.textContent = note.label;
      section.append(heading, textarea('note', index, '备注', note.note, 1000));
      fieldsRoot.append(section);
    });
    current.fault_notes.forEach((note, index) => {
      const section = document.createElement('section');
      const heading = document.createElement('h3');
      heading.textContent = note.label;
      section.append(heading, textarea('fault_note', index, '照片故障说明（不改水印）', note.fault_note, 4000));
      fieldsRoot.append(section);
    });
    error.hidden = true;
    dialog.showModal();
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-repair-edit]');
    if (!button) return;
    openEditor(button.dataset.repairEdit);
  });
  cancel.addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  confirm.addEventListener('click', async () => {
    if (busy || !current) return;
    for (const input of fieldsRoot.querySelectorAll('textarea')) {
      const index = Number(input.dataset.index);
      if (input.dataset.kind === 'note') current.notes[index].note = input.value;
      else if (input.dataset.kind === 'fault_note') current.fault_notes[index].fault_note = input.value;
      else current.repairs[index][input.dataset.kind] = input.value;
    }
    busy = true;
    confirm.disabled = cancel.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(dialog.dataset.url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Field-Token': dialog.dataset.csrf},
        body: JSON.stringify({
          repairs: current.repairs.map(repair => ({
            id: repair.id, fault: repair.fault, work_content: repair.work_content, result: repair.result,
          })),
          notes: current.notes.map(note => ({photo_ids: note.photo_ids, note: note.note})),
          fault_notes: current.fault_notes.map(note => ({photo_ids: note.photo_ids, fault_note: note.fault_note})),
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.error || '保存失败，请刷新后重试。');
      window.parent.postMessage({type: 'workspace:saved'}, location.origin);
      location.reload();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      busy = false;
      confirm.disabled = cancel.disabled = false;
    }
  });
})();
