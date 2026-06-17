const table = document.getElementById("customerReimbursementTable");
const addRowButton = document.getElementById("addCustomerReimbursementRow");
const attachmentInput = document.getElementById("customerReimbursementAttachments");
const selectedFiles = document.getElementById("selectedCustomerReimbursementFiles");

function cloneCustomerReimbursementRow() {
  const body = table?.querySelector("tbody");
  const lastRow = body?.querySelector("tr:last-child");
  if (!body || !lastRow) return;
  const nextRow = lastRow.cloneNode(true);
  nextRow.querySelectorAll("input").forEach((input) => {
    if (["standard_rate", "transport_rate", "overtime_rate", "holiday_rate", "mileage_rate"].includes(input.name)) {
      return;
    }
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
