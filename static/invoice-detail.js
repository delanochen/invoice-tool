const attachmentPreviewDialog = document.querySelector("#attachmentPreviewDialog");
const attachmentPreviewFrame = document.querySelector("#attachmentPreviewFrame");
const attachmentPreviewTitle = document.querySelector("#attachmentPreviewTitle");
const attachmentPreviewImage = document.querySelector("#attachmentPreviewImage");
const imagePreviewWrap = document.querySelector("#imagePreviewWrap");
const zoomInBtn = document.querySelector("#zoomInBtn");
const zoomOutBtn = document.querySelector("#zoomOutBtn");
const zoomFitBtn = document.querySelector("#zoomFitBtn");
const zoomOriginalBtn = document.querySelector("#zoomOriginalBtn");
const detailAttachmentsInput = document.querySelector("#detailAttachmentsInput");
const detailAttachmentList = document.querySelector("#detailAttachmentList");
let previewZoom = 1;
let previewMode = "fit";
let detailSelectedFiles = [];
const existingAttachmentNames = new Set(
  Array.from(document.querySelectorAll(".attachment-preview-link")).map((link) =>
    (link.dataset.previewName || link.textContent || "").trim().toLocaleLowerCase()
  )
);

function applyZoom() {
  attachmentPreviewImage.classList.toggle("is-fit", previewMode === "fit");
  attachmentPreviewImage.classList.toggle("is-original", previewMode === "original");
  attachmentPreviewImage.classList.toggle("is-zoomed", previewMode === "zoom");
  attachmentPreviewImage.style.transform = previewMode === "zoom" ? `scale(${previewZoom})` : "none";
}

function setFitPreview() {
  previewMode = "fit";
  previewZoom = 1;
  applyZoom();
}

function setOriginalPreview() {
  previewMode = "original";
  previewZoom = 1;
  applyZoom();
}

function setZoomPreview(nextZoom) {
  previewMode = "zoom";
  previewZoom = Math.max(0.25, Math.min(nextZoom, 4));
  applyZoom();
}

function closeAttachmentPreview() {
  attachmentPreviewFrame.src = "about:blank";
  attachmentPreviewImage.removeAttribute("src");
  attachmentPreviewDialog.close();
}

function showImagePreview(url) {
  attachmentPreviewFrame.hidden = true;
  imagePreviewWrap.hidden = false;
  zoomInBtn.hidden = false;
  zoomOutBtn.hidden = false;
  zoomFitBtn.hidden = false;
  zoomOriginalBtn.hidden = false;
  attachmentPreviewImage.src = url;
  setFitPreview();
}

function showDocumentPreview(url) {
  attachmentPreviewImage.removeAttribute("src");
  attachmentPreviewFrame.hidden = false;
  imagePreviewWrap.hidden = true;
  zoomInBtn.hidden = true;
  zoomOutBtn.hidden = true;
  zoomFitBtn.hidden = true;
  zoomOriginalBtn.hidden = true;
  attachmentPreviewFrame.src = url;
}

function syncDetailInput() {
  const transfer = new DataTransfer();
  detailSelectedFiles.forEach((file) => transfer.items.add(file));
  detailAttachmentsInput.files = transfer.files;
}

function renderDetailFileList() {
  detailAttachmentList.innerHTML = "";
  detailSelectedFiles.forEach((file, index) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    const remove = document.createElement("button");
    name.textContent = file.name;
    remove.type = "button";
    remove.className = "danger small";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      detailSelectedFiles.splice(index, 1);
      renderDetailFileList();
      syncDetailInput();
    });
    item.append(name, remove);
    detailAttachmentList.appendChild(item);
  });
}

function fileKey(file) {
  return file.name.trim().toLocaleLowerCase();
}

document.querySelectorAll(".attachment-preview-link").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    attachmentPreviewTitle.textContent = link.dataset.previewName || "附件预览";
    if ((link.dataset.contentType || "").startsWith("image/")) {
      showImagePreview(link.href);
    } else {
      showDocumentPreview(link.href);
    }
    attachmentPreviewDialog.showModal();
  });
});

zoomInBtn?.addEventListener("click", () => setZoomPreview((previewMode === "fit" ? 1 : previewZoom) + 0.25));
zoomOutBtn?.addEventListener("click", () => setZoomPreview((previewMode === "fit" ? 1 : previewZoom) - 0.25));
zoomFitBtn?.addEventListener("click", setFitPreview);
zoomOriginalBtn?.addEventListener("click", setOriginalPreview);

attachmentPreviewImage?.addEventListener("click", () => {
  if (previewMode === "fit") {
    setZoomPreview(2);
  } else {
    setFitPreview();
  }
});

if (detailAttachmentsInput && detailAttachmentList) {
  detailAttachmentsInput.addEventListener("change", () => {
    const existing = new Set([...existingAttachmentNames, ...detailSelectedFiles.map(fileKey)]);
    Array.from(detailAttachmentsInput.files).forEach((file) => {
      const key = fileKey(file);
      if (!existing.has(key)) {
        detailSelectedFiles.push(file);
        existing.add(key);
      }
    });
    renderDetailFileList();
    syncDetailInput();
  });
}
