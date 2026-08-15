const expenseItems = document.querySelector("#expenseItems");
const addExpenseItem = document.querySelector("#addExpenseItem");
const expenseTotal = document.querySelector("#expenseTotal");
const expenseForm = document.querySelector("#expenseForm");
let expenseSubmitting = false;

function escapeExpenseHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function expenseProjectOptions() {
  return [
    '<option value="">选择报销项目</option>',
    ...(window.expenseProjects || [])
      .filter((project) => project.active)
      .map((project) => `<option value="${project.id}">${escapeExpenseHtml(project.name)}</option>`),
  ].join("");
}

function updateExpenseTotal() {
  const total = Array.from(document.querySelectorAll("[name='item_amount']")).reduce(
    (sum, input) => sum + (Number.parseFloat(input.value) || 0),
    0
  );
  if (expenseTotal) expenseTotal.textContent = total.toFixed(2);
}

function expenseProject(projectId) {
  return (window.expenseProjects || []).find((project) => String(project.id) === String(projectId));
}

function newExpenseLineKey() {
  if (window.crypto?.randomUUID) return `line-${window.crypto.randomUUID()}`;
  return `line-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function syncFuelVehicleField(row) {
  const projectSelect = row.querySelector(".expense-project-select");
  const field = row.querySelector(".fuel-vehicle-field");
  const vehicleSelect = field?.querySelector('[name="fuel_vehicle_type"]');
  if (!projectSelect || !field || !vehicleSelect) return;
  const isFuel = Boolean(expenseProject(projectSelect.value)?.isFuel);
  field.hidden = !isFuel;
  vehicleSelect.required = isFuel;
  if (!isFuel) vehicleSelect.value = "";
}

function syncLodgingLimitWarning(row) {
  const projectSelect = row.querySelector(".expense-project-select");
  const amountInput = row.querySelector('[name="item_amount"]');
  const warning = row.querySelector(".lodging-limit-warning");
  if (!projectSelect || !amountInput || !warning) return;
  const limit = Number.parseFloat(window.expenseLodgingLimit) || 0;
  const amount = Number.parseFloat(amountInput.value) || 0;
  const showWarning = Boolean(expenseProject(projectSelect.value)?.isLodging) && limit > 0 && amount > limit;
  warning.hidden = !showWarning;
  warning.textContent = showWarning
    ? `提醒：住宿费超过单人参考标准 $${limit.toFixed(2)}；如为多人或连续多日合并订房，可继续提交并在明细说明中注明。`
    : "";
}

function bindExpenseRows() {
  document.querySelectorAll(".remove-expense-item").forEach((button) => {
    button.onclick = () => {
      const rows = document.querySelectorAll(".expense-item-row");
      if (rows.length > 1) {
        if (
          button.closest(".expense-item-row")?.querySelector(".saved-item-attachment")
          && !window.confirm("删除这条明细也会删除其已保存附件，确定继续吗？")
        ) return;
        button.closest(".expense-item-row").remove();
        updateExpenseTotal();
      }
    };
  });
  document.querySelectorAll("[name='item_amount']").forEach((input) => {
    input.oninput = () => {
      updateExpenseTotal();
      syncLodgingLimitWarning(input.closest(".expense-item-row"));
    };
  });
  document.querySelectorAll(".expense-item-row").forEach((row) => {
    const projectSelect = row.querySelector(".expense-project-select");
    projectSelect.onchange = () => {
      syncFuelVehicleField(row);
      syncLodgingLimitWarning(row);
    };
    syncFuelVehicleField(row);
    syncLodgingLimitWarning(row);
  });
}

addExpenseItem?.addEventListener("click", () => {
  const lineKey = newExpenseLineKey();
  const row = document.createElement("tr");
  row.className = "expense-item-row";
  row.innerHTML = `
    <td>
      <input type="hidden" name="item_line_key" value="${lineKey}">
      <select name="project_id" class="expense-project-select" aria-label="报销项目" required>
        ${expenseProjectOptions()}
      </select>
      <label class="fuel-vehicle-field" hidden>
        <span>加油车辆 <em class="required-mark">*</em></span>
        <select name="fuel_vehicle_type" aria-label="加油车辆类型">
          <option value="">请选择车辆类型</option>
          <option value="personal">个人／自有车辆（仅员工报销）</option>
          <option value="rental">租赁车辆（可计入工单结算）</option>
        </select>
      </label>
    </td>
    <td>
      <input type="number" step="0.01" min="0.01" name="item_amount" placeholder="金额（USD）" aria-label="金额" required>
      <small class="lodging-limit-warning" hidden></small>
    </td>
    <td><input name="item_description" placeholder="明细说明" aria-label="明细说明"></td>
    <td class="expense-line-attachments-cell">
      <label class="compact-file-picker">
        <span>添加附件</span>
        <input type="file" name="item_attachments_${lineKey}" accept=".doc,.docx,.xls,.xlsx,.pdf,image/*" multiple>
      </label>
    </td>
    <td><button type="button" class="ghost remove-expense-item">删除</button></td>
  `;
  expenseItems.appendChild(row);
  bindExpenseRows();
  window.setupPendingAttachmentInput?.(row.querySelector('input[type="file"]'));
  row.querySelector("select").focus();
});

bindExpenseRows();
updateExpenseTotal();

expenseForm?.addEventListener("submit", (event) => {
  if (expenseSubmitting) {
    event.preventDefault();
    return;
  }
  expenseSubmitting = true;
  const submitter = event.submitter;
  if (submitter?.name === "action") {
    const action = document.createElement("input");
    action.type = "hidden";
    action.name = "action";
    action.value = submitter.value;
    expenseForm.appendChild(action);
  }
  document.querySelectorAll("[data-expense-submit]").forEach((button) => {
    button.disabled = true;
    button.textContent = button === submitter ? "处理中..." : button.textContent;
    button.setAttribute("aria-busy", "true");
  });
});
