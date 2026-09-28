(() => {
  const translate = (value) => (window.uiTranslate ? window.uiTranslate(value) : value);

  const setDisabled = (element, disabled) => {
    if (!element) return;
    element.disabled = disabled;
  };

  const updateToolbar = (scope, row) => {
    const label = scope.querySelector("[data-selected-label]");
    if (label) {
      const prefix = label.dataset.selectedPrefix || "已选择：";
      const summary = label.dataset.selectedSummary || "";
      const selected = `${translate(prefix)}${row.dataset.rowLabel || ""}`;
      label.textContent = summary ? `${summary} · ${selected}` : selected;
    }

    for (const button of scope.querySelectorAll("[data-row-action]")) {
      const key = button.dataset.rowAction;
      const value = row.dataset[key] || "";
      button.dataset.selectedValue = value;
      setDisabled(button, !value);
      const labelKey = `${key}Label`;
      if (row.dataset[labelKey]) button.textContent = translate(row.dataset[labelKey]);
    }

    for (const form of scope.querySelectorAll("[data-row-action-form]")) {
      const key = form.dataset.rowActionForm;
      const value = row.dataset[key] || "";
      form.action = value;
      form.dataset.selectedValue = value;
      for (const button of form.querySelectorAll("button")) setDisabled(button, !value);
    }

    for (const input of scope.querySelectorAll("[data-selected-input]")) {
      const key = input.dataset.selectedInput || "rowId";
      input.value = row.dataset[key] || "";
    }

    for (const button of scope.querySelectorAll("[data-enable-on-select]")) {
      setDisabled(button, false);
    }
  };

  const selectRow = (table, row) => {
    const scope = table.closest("[data-selectable-scope]") || document;
    table.querySelectorAll("tbody tr.is-selected").forEach((item) => item.classList.remove("is-selected"));
    row.classList.add("is-selected");
    updateToolbar(scope, row);
  };

  document.querySelectorAll("[data-selectable-table]").forEach((table) => {
    table.querySelectorAll("tbody tr[data-row-id]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("a, button, input, select, textarea, label")) return;
        selectRow(table, row);
      });
      row.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        selectRow(table, row);
      });
    });
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-row-action]");
    if (!button || button.disabled) return;
    const value = button.dataset.selectedValue || "";
    if (!value) return;
    const mode = button.dataset.actionMode || "navigate";
    if (mode === "dialog") {
      document.getElementById(value)?.showModal();
      return;
    }
    // 明细类跳转（查看日报 / 查看报销 / 预览发票）要在工作区里新开一个标签页，
    // 而不是把当前标签的页面整个换掉 —— 否则用户在列表里点几次「查看」，
    // 列表页本身就被顶掉了（丢失滚动位置与筛选条件）。
    // 外壳会把 workspaceOpen 注入进来；不在工作区里跑时退化为浏览器新窗口。
    // 例外：下载类（pdfUrl 等直接给文件的）保持浏览器原生行为，别塞进 iframe 标签。
    const isDownload = /pdf|download|export/i.test(button.dataset.rowAction || "");
    if (!isDownload && typeof window.workspaceOpen === "function") {
      window.workspaceOpen(value, button.textContent.trim());
      return;
    }
    window.location.href = value;
  });

  // 非「行选择」类的跳转按钮（如工单详情页「编辑工单结算」）也走同一套：
  // 带上 data-workspace-url 就表示「在工作区里新开标签页」，而不是替换当前标签。
  // 下载类不能走这里（否则文件下载会变成 iframe 页面），仍用原生 onclick。
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-workspace-url]");
    if (!button || button.disabled) return;
    const url = button.dataset.workspaceUrl || "";
    if (!url) return;
    event.preventDefault();
    if (typeof window.workspaceOpen === "function") {
      window.workspaceOpen(url, button.dataset.workspaceTitle || button.textContent.trim());
      return;
    }
    window.location.href = url;
  });
})();
