const items = document.querySelector("#items");
const addLine = document.querySelector("#addLine");
const attachmentsInput = document.querySelector("#attachmentsInput");
const attachmentList = document.querySelector("#attachmentList");
const selectedAttachmentDialog = document.querySelector("#selectedAttachmentDialog");
const selectedAttachmentFrame = document.querySelector("#selectedAttachmentFrame");
const selectedAttachmentTitle = document.querySelector("#selectedAttachmentTitle");
const selectedAttachmentImage = document.querySelector("#selectedAttachmentImage");
const selectedImagePreviewWrap = document.querySelector("#selectedImagePreviewWrap");
const selectedZoomFitBtn = document.querySelector("#selectedZoomFitBtn");
const selectedZoomOutBtn = document.querySelector("#selectedZoomOutBtn");
const selectedZoomOriginalBtn = document.querySelector("#selectedZoomOriginalBtn");
const selectedZoomInBtn = document.querySelector("#selectedZoomInBtn");
const closeSelectedAttachment = document.querySelector("#closeSelectedAttachment");
const invoiceForm = document.querySelector("#invoiceForm");
let invoiceSubmitting = false;
let selectedAttachmentUrls = [];
let selectedFiles = [];
let selectedPreviewMode = "fit";
let selectedPreviewZoom = 1;
const existingAttachmentNames = new Set(
  Array.from(document.querySelectorAll(".attachment-preview-link")).map((link) =>
    (link.dataset.previewName || link.textContent || "").trim().toLocaleLowerCase()
  )
);

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function projectOptionsHtml() {
  return [
    '<option value="">选择项目</option>',
    ...(window.invoiceProjects || []).map((project) => (
      `<option value="${project.id}" data-tax="${project.taxRate}" data-amount="${project.defaultAmount}">${escapeHtml(project.name)}</option>`
    )),
  ].join("");
}

function applyProjectDefaults(select) {
  const option = select.selectedOptions[0];
  const row = select.closest(".item-row");
  row.querySelector("[name='amount']").value = option?.dataset.amount || "0";
  row.querySelector("[name='item_tax_rate']").value = option?.dataset.tax || "0";
}

function bindProjectSelects() {
  document.querySelectorAll(".project-select").forEach((select) => {
    select.onchange = () => applyProjectDefaults(select);
  });
}

function bindRemoveButtons() {
  document.querySelectorAll(".remove-line").forEach((button) => {
    button.onclick = () => {
      if (document.querySelectorAll(".item-row").length > 1) {
        button.closest(".item-row").remove();
      }
    };
  });
}

function fileKey(file) {
  return file.name.trim().toLocaleLowerCase();
}

function syncAttachmentInput() {
  const transfer = new DataTransfer();
  selectedFiles.forEach((file) => transfer.items.add(file));
  attachmentsInput.files = transfer.files;
}

function revokeSelectedUrls() {
  selectedAttachmentUrls.forEach((url) => URL.revokeObjectURL(url));
  selectedAttachmentUrls = [];
}

function applySelectedZoom() {
  selectedAttachmentImage.classList.toggle("is-fit", selectedPreviewMode === "fit");
  selectedAttachmentImage.classList.toggle("is-original", selectedPreviewMode === "original");
  selectedAttachmentImage.classList.toggle("is-zoomed", selectedPreviewMode === "zoom");
  selectedAttachmentImage.style.transform = selectedPreviewMode === "zoom" ? `scale(${selectedPreviewZoom})` : "none";
}

function setSelectedFitPreview() {
  selectedPreviewMode = "fit";
  selectedPreviewZoom = 1;
  applySelectedZoom();
}

function setSelectedOriginalPreview() {
  selectedPreviewMode = "original";
  selectedPreviewZoom = 1;
  applySelectedZoom();
}

function setSelectedZoomPreview(nextZoom) {
  selectedPreviewMode = "zoom";
  selectedPreviewZoom = Math.max(0.25, Math.min(nextZoom, 4));
  applySelectedZoom();
}

function showSelectedPreview(file, url) {
  selectedAttachmentTitle.textContent = file.name;
  if (file.type.startsWith("image/")) {
    selectedAttachmentFrame.src = "about:blank";
    selectedAttachmentFrame.hidden = true;
    selectedImagePreviewWrap.hidden = false;
    selectedZoomFitBtn.hidden = false;
    selectedZoomOutBtn.hidden = false;
    selectedZoomOriginalBtn.hidden = false;
    selectedZoomInBtn.hidden = false;
    selectedAttachmentImage.src = url;
    setSelectedFitPreview();
  } else {
    selectedAttachmentImage.removeAttribute("src");
    selectedImagePreviewWrap.hidden = true;
    selectedAttachmentFrame.hidden = false;
    selectedZoomFitBtn.hidden = true;
    selectedZoomOutBtn.hidden = true;
    selectedZoomOriginalBtn.hidden = true;
    selectedZoomInBtn.hidden = true;
    selectedAttachmentFrame.src = url;
  }
  selectedAttachmentDialog.showModal();
}

function renderAttachmentList() {
  revokeSelectedUrls();
  attachmentList.innerHTML = "";
  selectedFiles.forEach((file, index) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    const remove = document.createElement("button");
    const url = URL.createObjectURL(file);
    selectedAttachmentUrls.push(url);
    link.href = url;
    link.textContent = file.name;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showSelectedPreview(file, url);
    });
    remove.type = "button";
    remove.className = "danger small";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      selectedFiles.splice(index, 1);
      renderAttachmentList();
      syncAttachmentInput();
    });
    item.append(link, remove);
    attachmentList.appendChild(item);
  });
}

addLine?.addEventListener("click", () => {
  const row = document.createElement("div");
  row.className = "item-row";
  row.innerHTML = `
    <select name="project_id" class="project-select" required>${projectOptionsHtml()}</select>
    <input name="amount" type="number" step="0.01" min="0.01" value="0" aria-label="金额">
    <input name="item_tax_rate" type="number" step="0.01" min="0" value="0" aria-label="税率" readonly>
    <button type="button" class="ghost remove-line">删除</button>
  `;
  items.appendChild(row);
  bindRemoveButtons();
  bindProjectSelects();
});

bindRemoveButtons();
bindProjectSelects();

if (attachmentsInput && attachmentList) {
  attachmentsInput.addEventListener("change", () => {
    const existing = new Set([...existingAttachmentNames, ...selectedFiles.map(fileKey)]);
    Array.from(attachmentsInput.files).forEach((file) => {
      const key = fileKey(file);
      if (!existing.has(key)) {
        selectedFiles.push(file);
        existing.add(key);
      }
    });
    renderAttachmentList();
    syncAttachmentInput();
  });
}

selectedZoomInBtn?.addEventListener("click", () => setSelectedZoomPreview((selectedPreviewMode === "fit" ? 1 : selectedPreviewZoom) + 0.25));
selectedZoomOutBtn?.addEventListener("click", () => setSelectedZoomPreview((selectedPreviewMode === "fit" ? 1 : selectedPreviewZoom) - 0.25));
selectedZoomFitBtn?.addEventListener("click", setSelectedFitPreview);
selectedZoomOriginalBtn?.addEventListener("click", setSelectedOriginalPreview);

selectedAttachmentImage?.addEventListener("click", () => {
  if (selectedPreviewMode === "fit") {
    setSelectedZoomPreview(2);
  } else {
    setSelectedFitPreview();
  }
});

closeSelectedAttachment?.addEventListener("click", () => {
  selectedAttachmentFrame.src = "about:blank";
  selectedAttachmentImage.removeAttribute("src");
  selectedAttachmentDialog.close();
});

invoiceForm?.addEventListener("submit", (event) => {
  if (invoiceSubmitting) {
    event.preventDefault();
    return;
  }
  invoiceSubmitting = true;
  const submitter = event.submitter;
  if (submitter?.name === "action") {
    const action = document.createElement("input");
    action.type = "hidden";
    action.name = "action";
    action.value = submitter.value;
    invoiceForm.appendChild(action);
  }
  document.querySelectorAll("[data-invoice-submit]").forEach((button) => {
    button.disabled = true;
    button.textContent = button === submitter ? "处理中..." : button.textContent;
    button.setAttribute("aria-busy", "true");
  });
});
