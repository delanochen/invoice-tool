document.querySelectorAll("[data-dialog-open]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = document.getElementById(button.dataset.dialogOpen);
    if (dialog) dialog.showModal();
  });
});

// system-grid.js 的 scan() 会接管 dialog 里带 thead 的表格（如用户弹窗里的
// 「外部员工工单授权」清单），而 scan 在 dialog 关闭（display:none）时就执行了：
// Tabulator 量到 0 高度，弹窗打开后只剩「N 条记录 / 表内搜索 / 列设置」工具栏，
// 数据行全部不可见。这里监听 <dialog open> 属性变化——无论从哪条路径打开
// （data-dialog-open 按钮、行选中 data-action-mode="dialog"、其它脚本直接
// showModal），打开后都对弹窗内的网格重算一次尺寸。
const redrawDialogGrids = (dialog) => {
  requestAnimationFrame(() => {
    window.systemGrids?.instances.forEach((instance) => {
      if (instance.ready && dialog.contains(instance.source)) instance.grid.redraw(true);
    });
  });
};
const dialogGridObserver = new MutationObserver((records) => {
  records.forEach((record) => {
    if (record.attributeName === "open" && record.target.open) redrawDialogGrids(record.target);
  });
});
document.querySelectorAll("dialog.modal-dialog").forEach((dialog) => {
  if (dialog.open) redrawDialogGrids(dialog);
  dialogGridObserver.observe(dialog, { attributes: true, attributeFilter: ["open"] });
});

document.querySelectorAll("[data-dialog-close]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = button.closest("dialog");
    if (dialog) dialog.close();
  });
});

document.querySelectorAll(".modal-dialog").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    // v0.1.247: only close on genuine backdrop clicks. With native <dialog>,
    // clicks anywhere on the dialog's own padding/gaps ALSO report the dialog
    // as target, which kept closing the edit-user form when users clicked
    // blank areas inside it. A backdrop click lands OUTSIDE the dialog box,
    // so compare coordinates against the content rect.
    const rect = dialog.getBoundingClientRect();
    const insideContent =
      event.clientX >= rect.left && event.clientX <= rect.right &&
      event.clientY >= rect.top && event.clientY <= rect.bottom;
    if (!insideContent) dialog.close();
  });
});

document.querySelectorAll(".modal-dialog form").forEach((form) => {
  form.addEventListener("submit", () => {
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
    });
  });
});
