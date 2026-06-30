const imageAttachmentPreviewDialog = document.querySelector("#imageAttachmentPreviewDialog");
const imageAttachmentPreviewTitle = document.querySelector("#imageAttachmentPreviewTitle");
const imageAttachmentPreviewImage = document.querySelector("#imageAttachmentPreviewImage");
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
  imageAttachmentPreviewTitle.textContent = link.dataset.previewName || link.textContent.trim() || "附件预览";
  imageAttachmentPreviewImage.src = link.href;
  setImageAttachmentPreviewFit();
  imageAttachmentPreviewDialog.showModal();
});

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
