const imageAttachmentPreviewDialog = document.querySelector("#imageAttachmentPreviewDialog, #ledgerPhotoDialog");
const imageAttachmentPreviewTitle = document.querySelector("#imageAttachmentPreviewTitle, #ledgerPhotoTitle");
const imageAttachmentPreviewImage = document.querySelector("#imageAttachmentPreviewImage, #ledgerPhotoImage");
const imageAttachmentPreviewFit = document.querySelector("[data-image-preview-fit]");
const imageAttachmentPreviewOut = document.querySelector("[data-image-preview-out]");
const imageAttachmentPreviewOriginal = document.querySelector("[data-image-preview-original]");
const imageAttachmentPreviewIn = document.querySelector("[data-image-preview-in]");
const imageAttachmentPreviewClose = document.querySelector("[data-image-preview-close]");
let imageAttachmentPreviewMode = "fit";
let imageAttachmentPreviewZoom = 1;

function applyImageAttachmentPreviewZoom() {
  if (!imageAttachmentPreviewImage) return;
  imageAttachmentPreviewImage.classList.toggle("is-fit", imageAttachmentPreviewMode === "fit");
  imageAttachmentPreviewImage.classList.toggle("is-original", imageAttachmentPreviewMode === "original");
  imageAttachmentPreviewImage.classList.toggle("is-zoomed", imageAttachmentPreviewMode === "zoom");
  imageAttachmentPreviewImage.style.transform = imageAttachmentPreviewMode === "zoom" ? `scale(${imageAttachmentPreviewZoom})` : "none";
  if (imageAttachmentPreviewMode === "zoom") {
    imageAttachmentPreviewImage.closest(".ledger-photo-stage, .image-preview-wrap")?.scrollTo({left: 0, top: 0});
  }
}

function setImageAttachmentPreviewFit() {
  imageAttachmentPreviewMode = "fit";
  imageAttachmentPreviewZoom = 1;
  applyImageAttachmentPreviewZoom();
}

function setImageAttachmentPreviewOriginal() {
  imageAttachmentPreviewMode = "original";
  imageAttachmentPreviewZoom = 1;
  applyImageAttachmentPreviewZoom();
}

function setImageAttachmentPreviewZoom(nextZoom) {
  imageAttachmentPreviewMode = "zoom";
  imageAttachmentPreviewZoom = Math.max(0.25, Math.min(nextZoom, 4));
  applyImageAttachmentPreviewZoom();
}

function closeImageAttachmentPreview() {
  imageAttachmentPreviewImage?.removeAttribute("src");
  imageAttachmentPreviewDialog?.close();
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("[data-image-preview]");
  if (!link || !imageAttachmentPreviewDialog || !imageAttachmentPreviewImage) return;
  event.preventDefault();
  // Handle the visible thumbnail before grid adapters forward its click to a hidden source row.
  event.stopPropagation();
  imageAttachmentPreviewTitle.textContent = link.dataset.previewName || link.textContent.trim() || "附件预览";
  imageAttachmentPreviewImage.src = link.href;
  setImageAttachmentPreviewFit();
  if (!imageAttachmentPreviewDialog.open) imageAttachmentPreviewDialog.showModal();
}, true);

imageAttachmentPreviewFit?.addEventListener("click", setImageAttachmentPreviewFit);
imageAttachmentPreviewOriginal?.addEventListener("click", setImageAttachmentPreviewOriginal);
imageAttachmentPreviewIn?.addEventListener("click", () => setImageAttachmentPreviewZoom((imageAttachmentPreviewMode === "fit" ? 1 : imageAttachmentPreviewZoom) + 0.25));
imageAttachmentPreviewOut?.addEventListener("click", () => setImageAttachmentPreviewZoom((imageAttachmentPreviewMode === "fit" ? 1 : imageAttachmentPreviewZoom) - 0.25));
imageAttachmentPreviewClose?.addEventListener("click", closeImageAttachmentPreview);

imageAttachmentPreviewImage?.addEventListener("click", () => {
  if (imageAttachmentPreviewMode === "fit") {
    setImageAttachmentPreviewZoom(2);
  } else {
    setImageAttachmentPreviewFit();
  }
});

imageAttachmentPreviewDialog?.addEventListener("close", () => {
  imageAttachmentPreviewImage?.removeAttribute("src");
});
