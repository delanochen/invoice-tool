(() => {
  const pool = document.getElementById("settlementSourceSelectionForm");
  const form = document.getElementById("customerReimbursementForm");
  if (!pool || !form) return;
  const reveal = (input) => {
    const panel = input.closest("[data-settlement-tab-panel]");
    if (panel?.hidden) document.querySelector(`[data-settlement-tab="${panel.dataset.settlementTabPanel}"]`)?.click();
  };
  // Native number validation runs before submit, including controls in hidden tabs.
  pool.addEventListener("invalid", (event) => reveal(event.target), true);
  pool.querySelectorAll(".source-choice").forEach((choice) => {
    choice.addEventListener("change", () => {
      choice.closest("tr").querySelectorAll("input").forEach((input) => input.setCustomValidity(""));
    });
  });
  pool.querySelectorAll(".source-settled-amount").forEach((input) => {
    input.addEventListener("input", () => {
      const choice = input.closest("tr").querySelector(".source-choice");
      if (choice && !choice.disabled) choice.checked = true;
      input.setCustomValidity("");
    });
  });
  pool.querySelectorAll(".source-adjustment-reason").forEach((input) => {
    input.addEventListener("input", () => input.setCustomValidity(""));
  });
  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    const details = [];
    let invalid = null;
    pool.querySelectorAll("tbody tr").forEach((row) => {
      const choice = row.querySelector(".source-choice");
      const amount = row.querySelector(".source-settled-amount");
      const reason = row.querySelector(".source-adjustment-reason");
      if (amount) amount.setCustomValidity("");
      if (reason) reason.setCustomValidity("");
      if (!choice?.checked || choice.disabled || !amount) return;
      const value = Number(amount.value), full = Number(amount.max);
      if (!amount.value.trim() || !Number.isFinite(value) || value < 0 || value > full) {
        amount.setCustomValidity("客户结算金额必须在 0 与原报销金额之间。");
        invalid ||= amount;
      } else if (value < full) {
        if (!reason?.value.trim()) {
          reason?.setCustomValidity("调减客户结算金额后必须填写原因。");
          invalid ||= reason || amount;
        }
        details.push(`原报销 ${full.toFixed(2)}，客户结算 ${value.toFixed(2)}，调减 ${(full-value).toFixed(2)}。原因：${reason?.value.trim() || "未填写"}`);
      }
    });
    if (invalid) {
      event.preventDefault();
      // The invalid field may be inside a hidden workspace tab.
      reveal(invalid);
      invalid.focus();
      invalid.reportValidity();
      // Give the grid's validation proxy time to copy this message to its visible input.
      window.setTimeout(() => invalid.setCustomValidity(""), 0);
      return;
    }
    if (details.length && !window.uiConfirm(`以下明细部分计入客户结算：\n\n${details.join("\n")}\n\n确认保存？`)) event.preventDefault();
  });
})();
