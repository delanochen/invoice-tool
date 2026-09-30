/**
 * 员工往来账「合并发放」：勾选多张付款单 → 合成一张支票发放。
 *
 * 两个必须遵守的前置事实（改这个文件前先读）：
 * 1. 表格会被 system-grid.js 镜像成 Tabulator，镜像出来的 checkbox 也在**同一个
 *    document** 里（.system-grid 是原表的兄弟节点）。`querySelectorAll` 会把原件和
 *    副本一起数进来 → 一张付款单被算两遍。所以选中态一律以**原表**为准，副本
 *    用 `closest('.system-grid')` 排除掉（与 messages.js 同一手法）。
 * 2. 镜像副本在 mirror() 里被摘掉了 name 属性，原生表单提交收不到它们；
 *    所以提交前由本文件把原表里勾中的 id 注入成 hidden input。
 *    镜像 → 原件的状态回写是 system-grid.js 的 mirror() 负责的（点副本会同步原件
 *    并派发冒泡的 change），这里只需监听 change 即可。
 */
(() => {
  const translate = (value) => (window.uiTranslate ? window.uiTranslate(value) : value);

  function sourceBoxes() {
    return [...document.querySelectorAll("[data-payment-select]")].filter(
      (box) => !box.closest(".system-grid")
    );
  }

  function selectedBoxes() {
    return sourceBoxes().filter((box) => box.checked);
  }

  function refresh() {
    const boxes = sourceBoxes();
    const chosen = boxes.filter((box) => box.checked);
    const selectAll = document.querySelector("[data-payment-select-all]");
    if (selectAll) {
      selectAll.checked = boxes.length > 0 && chosen.length === boxes.length;
      selectAll.indeterminate = chosen.length > 0 && chosen.length < boxes.length;
    }
    const counter = document.querySelector("[data-payment-selected-count]");
    if (counter) {
      counter.textContent = chosen.length ? translate(`已选 ${chosen.length} 张`) : "";
    }
    const total = document.querySelector("[data-payment-selected-total]");
    if (total) {
      const sum = chosen.reduce((accumulator, box) =>
        accumulator + (Number.parseFloat(box.dataset.netAmount || "0") || 0), 0);
      total.textContent = chosen.length ? sum.toFixed(2) : "";
    }
    const button = document.querySelector("[data-payment-batch-open]");
    if (button) button.disabled = chosen.length === 0;
  }

  document.querySelector("[data-payment-select-all]")?.addEventListener("change", (event) => {
    const checked = event.target.checked;
    sourceBoxes().forEach((box) => {
      box.checked = checked;
      // 镜像 DOM 是独立副本，原件改完要主动通知它一次。
      box.dispatchEvent(new Event("change", { bubbles: true }));
    });
    refresh();
  });

  document.addEventListener("change", (event) => {
    if (event.target.closest("[data-payment-select]")) refresh();
  });

  const form = document.getElementById("paymentBatchForm");
  form?.addEventListener("submit", (event) => {
    form.querySelectorAll('input[name="payment_id"]').forEach((input) => input.remove());
    const ids = selectedBoxes().map((box) => box.value);
    if (!ids.length) {
      event.preventDefault();
      window.alert(translate("请先勾选要合并发放的付款单。"));
      return;
    }
    for (const id of ids) {
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "payment_id";
      hidden.value = id;
      form.appendChild(hidden);
    }
  });

  refresh();
})();
