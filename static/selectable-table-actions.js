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
      label.textContent = `${translate(prefix)}${row.dataset.rowLabel || ""}`;
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
    window.location.href = value;
  });
})();
