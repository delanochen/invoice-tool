const table = document.getElementById("customerReimbursementTable");
const addRowButton = document.getElementById("addCustomerReimbursementRow");
const attachmentInput = document.getElementById("customerReimbursementAttachments");
const selectedFiles = document.getElementById("selectedCustomerReimbursementFiles");
const reimbursementForm = document.getElementById("customerReimbursementForm");
const reimbursementRates = window.customerReimbursementRates || {};
const reimbursementLiveTotalsEnabled = reimbursementForm?.dataset.liveTotals === "true";
const travelAmountFields = ["lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"];
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

function reimbursementInputValue(row, name) {
  return reimbursementNumber(row.querySelector(`[name="${name}"]`)?.value);
}

function calculateReimbursementRow(row) {
  const labor =
    reimbursementInputValue(row, "standard_hours") * reimbursementNumber(reimbursementRates.standard) +
    reimbursementInputValue(row, "transport_hours") * reimbursementNumber(reimbursementRates.transport) +
    reimbursementInputValue(row, "overtime_hours") * reimbursementNumber(reimbursementRates.overtime) +
    reimbursementInputValue(row, "holiday_hours") * reimbursementNumber(reimbursementRates.holiday);
  const mileage = reimbursementInputValue(row, "miles") * reimbursementNumber(reimbursementRates.mileage);
  const travel = travelAmountFields.reduce((sum, name) => sum + reimbursementInputValue(row, name), 0);
  return { labor, travel, mileage, total: labor + travel + mileage };
}

function updateCustomerReimbursementTotals() {
  if (!reimbursementLiveTotalsEnabled) return;
  const totals = { labor: 0, travel: 0, mileage: 0, total: 0 };
  table?.querySelectorAll("tbody tr").forEach((row) => {
    const rowTotals = calculateReimbursementRow(row);
    Object.keys(totals).forEach((key) => {
      totals[key] += rowTotals[key];
      const cell = row.querySelector(`[data-row-total="${key}"]`);
      if (cell) cell.textContent = reimbursementMoney(rowTotals[key]);
    });
  });
  Object.entries(totals).forEach(([key, value]) => {
    const metric = document.querySelector(`[data-reimbursement-total="${key}"]`);
    if (metric) metric.textContent = reimbursementMoney(value);
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
  });
  body.appendChild(nextRow);
  updateCustomerReimbursementTotals();
}

function renderSelectedFiles() {
  selectedFiles?.replaceChildren();
  Array.from(attachmentInput?.files || []).forEach((file) => {
    const card = document.createElement("figure");
    card.className = "selected-photo-card";
    if (file.type.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = file.name;
      img.addEventListener("load", () => URL.revokeObjectURL(img.src), { once: true });
      card.appendChild(img);
    }
    const caption = document.createElement("figcaption");
    caption.textContent = file.name;
    card.appendChild(caption);
    selectedFiles.appendChild(card);
  });
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

attachmentInput?.addEventListener("change", renderSelectedFiles);

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
