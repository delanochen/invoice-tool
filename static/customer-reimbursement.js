const table = document.getElementById("customerReimbursementTable");
const addRowButton = document.getElementById("addCustomerReimbursementRow");
const reimbursementForm = document.getElementById("customerReimbursementForm");
const reimbursementRates = window.customerReimbursementRates || {};
const reimbursementMro = reimbursementNumber(window.customerReimbursementMro);
const reimbursementRentalFuel = reimbursementNumber(window.customerReimbursementRentalFuel);
const reimbursementLodgingLimit = reimbursementNumber(window.customerReimbursementLodgingLimit);
const reimbursementLiveTotalsEnabled = reimbursementForm?.dataset.liveTotals === "true";
const travelAmountFields = ["lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi"];
const allExpenseAmountFields = [...travelAmountFields, "other"];
let reimbursementSubmitting = false;
let reimbursementResetTimer = null;

function reimbursementNumber(value) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function reimbursementMoney(value) {
  return `$${reimbursementNumber(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function reimbursementQuantity(value) {
  return reimbursementNumber(value).toLocaleString("en-US", {
    maximumFractionDigits: 2,
  });
}

function reimbursementInputValue(row, name) {
  const input = row.querySelector(`[name="${name}"]`);
  return reimbursementNumber(input?.value) + reimbursementNumber(input?.dataset.autoAmount);
}

function reimbursementAutoValue(row, name) {
  return reimbursementNumber(row.querySelector(`[name="${name}"]`)?.dataset.autoAmount);
}

function calculateReimbursementRow(row) {
  const labor =
    reimbursementInputValue(row, "standard_hours") * reimbursementNumber(reimbursementRates.standard) +
    reimbursementInputValue(row, "transport_hours") * reimbursementNumber(reimbursementRates.transport) +
    reimbursementInputValue(row, "overtime_hours") * reimbursementNumber(reimbursementRates.overtime) +
    reimbursementInputValue(row, "holiday_hours") * reimbursementNumber(reimbursementRates.holiday);
  const mileage = reimbursementInputValue(row, "miles") * reimbursementNumber(reimbursementRates.mileage);
  const travel = travelAmountFields.reduce((sum, name) => sum + reimbursementInputValue(row, name), 0);
  const other = reimbursementInputValue(row, "other");
  return { labor, travel, mileage, other, total: labor + travel + mileage + other };
}

function updateReimbursementLodgingWarning(row) {
  const warning = row.querySelector(".lodging-limit-warning");
  if (!warning) return;
  const amount = reimbursementInputValue(row, "lodging");
  const overLimit = reimbursementLodgingLimit > 0 && amount > reimbursementLodgingLimit;
  warning.hidden = !overLimit;
  warning.textContent = overLimit
    ? `提醒：住宿费 ${reimbursementMoney(amount)} 超过参考上限 ${reimbursementMoney(reimbursementLodgingLimit)}，请核对后再保存。`
    : "";
}

function updateCustomerReimbursementTotals() {
  if (!reimbursementLiveTotalsEnabled) return;
  const totals = {
    labor: 0,
    lodging: 0,
    travel: reimbursementRentalFuel,
    mileage: 0,
    rentalFuel: reimbursementRentalFuel,
    mro: reimbursementMro,
    employeeExpense: 0,
    other: 0,
    total: reimbursementRentalFuel,
  };
  const columnTotals = {
    standard_hours: 0, transport_hours: 0, overtime_hours: 0, holiday_hours: 0,
    labor_total: 0, lodging: 0, airfare: 0, baggage: 0, rental_car: 0,
    fuel: 0, parking: 0, taxi: 0, miles: 0, mileage_total: 0, other: 0, total: 0,
  };
  table?.querySelectorAll("tbody tr").forEach((row) => {
    updateReimbursementLodgingWarning(row);
    const rowTotals = calculateReimbursementRow(row);
    ["standard_hours", "transport_hours", "overtime_hours", "holiday_hours", "miles"].forEach((name) => {
      columnTotals[name] += reimbursementInputValue(row, name);
    });
    allExpenseAmountFields.forEach((name) => {
      columnTotals[name] += reimbursementInputValue(row, name);
    });
    columnTotals.labor_total += rowTotals.labor;
    columnTotals.mileage_total += rowTotals.mileage;
    columnTotals.total += rowTotals.total;
    totals.lodging += reimbursementInputValue(row, "lodging");
    totals.employeeExpense += allExpenseAmountFields.reduce(
      (sum, name) => sum + reimbursementAutoValue(row, name),
      0,
    );
    Object.keys(rowTotals).forEach((key) => {
      totals[key] += rowTotals[key];
      const cell = row.querySelector(`[data-row-total="${key}"]`);
      if (cell) cell.textContent = reimbursementMoney(rowTotals[key]);
    });
  });
  Object.entries(totals).forEach(([key, value]) => {
    const metric = document.querySelector(`[data-reimbursement-total="${key}"]`);
    if (metric) metric.textContent = reimbursementMoney(value);
  });
  Object.entries(columnTotals).forEach(([key, value]) => {
    const target = document.querySelector(`[data-column-total="${key}"]`);
    if (!target) return;
    target.textContent = ["standard_hours", "transport_hours", "overtime_hours", "holiday_hours", "miles"].includes(key)
      ? reimbursementQuantity(value)
      : reimbursementMoney(value);
  });
}

function resetReimbursementSubmitState() {
  reimbursementSubmitting = false;
  window.clearTimeout(reimbursementResetTimer);
  reimbursementResetTimer = null;
  reimbursementForm?.querySelectorAll('input[data-submit-action="true"]').forEach((input) => input.remove());
  document.querySelectorAll(`button[form="${reimbursementForm?.id}"]`).forEach((button) => {
    button.disabled = false;
    if (button.dataset.originalText) {
      button.textContent = button.dataset.originalText;
      delete button.dataset.originalText;
    }
  });
}

function cloneCustomerReimbursementRow() {
  const body = table?.querySelector("tbody");
  const lastRow = body?.querySelector("tr:last-child");
  if (!body || !lastRow) return;
  const nextRow = lastRow.cloneNode(true);
  nextRow.querySelectorAll("input").forEach((input) => {
    input.value = input.type === "date" ? input.value : "";
    if (input.dataset.autoAmount !== undefined) input.dataset.autoAmount = "0";
  });
  nextRow.querySelectorAll(".auto-expense-amount").forEach((label) => label.remove());
  body.appendChild(nextRow);
  updateCustomerReimbursementTotals();
}

addRowButton?.addEventListener("click", cloneCustomerReimbursementRow);

table?.addEventListener("click", (event) => {
  const button = event.target.closest(".remove-customer-reimbursement-row");
  if (!button) return;
  const row = button.closest("tr");
  const body = button.closest("tbody");
  if (!row || !body || body.rows.length <= 1) return;
  row.remove();
  updateCustomerReimbursementTotals();
});

table?.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "number") return;
  updateCustomerReimbursementTotals();
});


reimbursementForm?.addEventListener("keydown", (event) => {
  const target = event.target;
  if (
    event.key === "Enter" &&
    target instanceof HTMLInputElement &&
    !["button", "checkbox", "file", "hidden", "radio", "reset", "submit"].includes(target.type)
  ) {
    event.preventDefault();
  }
});

reimbursementForm?.addEventListener("submit", (event) => {
  if (reimbursementSubmitting) {
    event.preventDefault();
    return;
  }
  reimbursementSubmitting = true;
  const submitter = event.submitter;
  if (submitter?.name === "action") {
    const action = document.createElement("input");
    action.type = "hidden";
    action.name = "action";
    action.value = submitter.value;
    action.dataset.submitAction = "true";
    reimbursementForm.appendChild(action);
  }
  document.querySelectorAll(`button[form="${reimbursementForm.id}"]`).forEach((button) => {
    button.dataset.originalText = button.textContent;
    button.disabled = true;
    if (button === submitter) button.textContent = "处理中...";
  });
  if (submitter?.value === "generate_pdf") {
    reimbursementResetTimer = window.setTimeout(resetReimbursementSubmitState, 1500);
  }
});

window.addEventListener("focus", () => {
  if (reimbursementSubmitting && reimbursementResetTimer) {
    resetReimbursementSubmitState();
  }
});
window.addEventListener("pageshow", resetReimbursementSubmitState);
updateCustomerReimbursementTotals();
