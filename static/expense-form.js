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

function bindExpenseRows() {
  document.querySelectorAll(".remove-expense-item").forEach((button) => {
    button.onclick = () => {
      const rows = document.querySelectorAll(".expense-item-row");
      if (rows.length > 1) {
        button.closest(".expense-item-row").remove();
        updateExpenseTotal();
      }
    };
  });
  document.querySelectorAll("[name='item_amount']").forEach((input) => {
    input.oninput = updateExpenseTotal;
  });
  document.querySelectorAll(".expense-item-row").forEach((row) => {
    const projectSelect = row.querySelector(".expense-project-select");
    projectSelect.onchange = () => syncFuelVehicleField(row);
    syncFuelVehicleField(row);
  });
}

addExpenseItem?.addEventListener("click", () => {
  const row = document.createElement("tr");
  row.className = "expense-item-row";
  row.innerHTML = `
    <td>
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
    <td><input type="number" step="0.01" min="0.01" name="item_amount" placeholder="金额（USD）" aria-label="金额" required></td>
    <td><input name="item_description" placeholder="明细说明" aria-label="明细说明"></td>
    <td><button type="button" class="ghost remove-expense-item">删除</button></td>
  `;
  expenseItems.appendChild(row);
  bindExpenseRows();
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
