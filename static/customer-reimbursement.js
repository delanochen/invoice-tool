const table = document.getElementById("customerReimbursementTable");
const addRowButton = document.getElementById("addCustomerReimbursementRow");
const attachmentInput = document.getElementById("customerReimbursementAttachments");
const selectedFiles = document.getElementById("selectedCustomerReimbursementFiles");
const reimbursementForm = document.getElementById("customerReimbursementForm");
let reimbursementSubmitting = false;
let reimbursementResetTimer = null;

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
