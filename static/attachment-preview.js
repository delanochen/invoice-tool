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
const imageAttachmentViewport = imageAttachmentPreviewImage?.closest('.ledger-photo-stage, .image-preview-wrap');
imageAttachmentViewport?.classList.add('attachment-image-viewport');
imageAttachmentPreviewImage?.classList.add('attachment-preview-image');
let imagePreviewReturn = null;

function applyImageAttachmentPreviewZoom() {
  if (!imageAttachmentPreviewImage) return;
  imageAttachmentPreviewImage.classList.toggle("is-fit", imageAttachmentPreviewMode === "fit");
  imageAttachmentPreviewImage.classList.toggle("is-original", imageAttachmentPreviewMode === "original");
  imageAttachmentPreviewImage.classList.toggle("is-zoomed", imageAttachmentPreviewMode === "zoom");
  // Real dimensions participate in scrolling; transforms can put pixels outside the reachable area.
  const image=imageAttachmentPreviewImage, viewport=imageAttachmentViewport;
  if(viewport && image.naturalWidth && viewport.clientWidth && viewport.clientHeight) {
    const factor=imageAttachmentPreviewMode==='fit'
      ? Math.min(1,viewport.clientWidth/image.naturalWidth,viewport.clientHeight/image.naturalHeight)
      : imageAttachmentPreviewMode==='original' ? 1 : imageAttachmentPreviewZoom;
    image.style.width=`${image.naturalWidth*factor}px`;
    image.style.height=`${image.naturalHeight*factor}px`;
    image.style.transform='none';
  }
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

function openImageAttachmentPreview(link, event) {
  if (!link || !imageAttachmentPreviewDialog || !imageAttachmentPreviewImage) return;
  event.preventDefault();
  // Handle the visible thumbnail before grid adapters forward its click to a hidden source row.
  event.stopPropagation();
  if (imageAttachmentPreviewDialog.open && imageAttachmentPreviewImage.src === link.href) return;
  const containers=[];
  for(let node=link.parentElement;node;node=node.parentElement) {
    if(node.scrollHeight>node.clientHeight || node.scrollWidth>node.clientWidth) containers.push([node,node.scrollLeft,node.scrollTop]);
  }
  imagePreviewReturn={link,containers,x:window.scrollX,y:window.scrollY};
  imageAttachmentPreviewTitle.textContent = link.dataset.previewName || link.textContent.trim() || "附件预览";
  imageAttachmentPreviewImage.src = link.href;
  setImageAttachmentPreviewFit();
  if (!imageAttachmentPreviewDialog.open) imageAttachmentPreviewDialog.showModal();
  applyImageAttachmentPreviewZoom();
  window.scrollTo(imagePreviewReturn.x,imagePreviewReturn.y);
}

// A grid can replace a cell between pointerdown and click. Handle the release
// against the visible link as well; retain click for keyboard activation.
let imagePreviewPointer = null;
document.addEventListener('pointerdown', event => {
  const link = event.target.closest?.('[data-image-preview]');
  imagePreviewPointer = event.isPrimary && event.button === 0 && link
    ? {id:event.pointerId, href:link.href, x:event.clientX, y:event.clientY} : null;
}, true);
document.addEventListener('pointercancel', () => { imagePreviewPointer = null; }, true);
document.addEventListener('pointerup', event => {
  const start = imagePreviewPointer; imagePreviewPointer = null;
  const link = event.target.closest?.('[data-image-preview]');
  if (start && start.id === event.pointerId && link?.href === start.href
      && Math.hypot(event.clientX-start.x, event.clientY-start.y) < 8) {
    openImageAttachmentPreview(link, event);
  }
}, true);
document.addEventListener('click', event => {
  openImageAttachmentPreview(event.target.closest?.('[data-image-preview]'), event);
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
  if(imagePreviewReturn) {
    const saved=imagePreviewReturn;imagePreviewReturn=null;
    saved.link.isConnected && saved.link.focus({preventScroll:true});
    saved.containers.forEach(([node,x,y])=>node.scrollTo(x,y));
    window.scrollTo(saved.x,saved.y);
  }
});
imageAttachmentPreviewImage?.addEventListener('load',applyImageAttachmentPreviewZoom);
if(imageAttachmentViewport) new ResizeObserver(()=>{
  if(imageAttachmentPreviewDialog.open) applyImageAttachmentPreviewZoom();
}).observe(imageAttachmentViewport);
