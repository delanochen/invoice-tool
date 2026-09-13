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
let previewPageStyles = null;

function lockImagePreviewPage(position) {
  if (previewPageStyles) return;
  const body=document.body, root=document.documentElement;
  previewPageStyles=[body,root].map(node=>[node,node.getAttribute('style')]);
  const width=root.clientWidth;
  Object.assign(root.style,{overflow:'hidden',scrollBehavior:'auto'});
  Object.assign(body.style,{position:'fixed',top:`${-position.y}px`,left:`${-position.x}px`,width:`${width}px`,overflow:'visible'});
}

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
  imageAttachmentPreviewDialog?.close();
  restoreImageAttachmentPreview();
}

function captureImagePreviewPosition(link) {
  const containers=[];
  for(let node=link.parentElement;node;node=node.parentElement) {
    if(node.scrollHeight>node.clientHeight || node.scrollWidth>node.clientWidth) containers.push([node,node.scrollLeft,node.scrollTop]);
  }
  return {link,containers,x:window.scrollX,y:window.scrollY};
}

function openImageAttachmentPreview(link, event, position) {
  if (!link || !imageAttachmentPreviewDialog || !imageAttachmentPreviewImage) return;
  event.preventDefault();
  // Handle the visible thumbnail before grid adapters forward its click to a hidden source row.
  event.stopPropagation();
  if (imageAttachmentPreviewDialog.open && imageAttachmentPreviewImage.src === link.href) return;
  if (!imagePreviewReturn) imagePreviewReturn=position || captureImagePreviewPosition(link);
  lockImagePreviewPage(imagePreviewReturn);
  imageAttachmentPreviewTitle.textContent = link.dataset.previewName || link.textContent.trim() || "附件预览";
  imageAttachmentPreviewImage.src = link.href;
  setImageAttachmentPreviewFit();
  if (!imageAttachmentPreviewDialog.open) imageAttachmentPreviewDialog.showModal();
  applyImageAttachmentPreviewZoom();
}

// A grid can replace a cell between pointerdown and click. Handle the release
// against the visible link as well; retain click for keyboard activation.
let imagePreviewPointer = null;
document.addEventListener('pointerdown', event => {
  const link = event.target.closest?.('[data-image-preview]');
  imagePreviewPointer = event.isPrimary && event.button === 0 && link
    ? {id:event.pointerId, href:link.href, name:link.dataset.previewName || link.textContent.trim() || '', x:event.clientX, y:event.clientY, position:captureImagePreviewPosition(link)} : null;
}, true);
// Mouse focus happens before pointerup/click. Keep it from scrolling a grid
// thumbnail into view before the preview has captured the user's position.
document.addEventListener('mousedown', event => {
  if (event.button === 0 && event.target.closest?.('[data-image-preview]')) {
    event.preventDefault();
    event.stopPropagation();
  }
}, true);
document.addEventListener('pointercancel', () => { imagePreviewPointer = null; }, true);
document.addEventListener('pointerup', event => {
  const start = imagePreviewPointer; imagePreviewPointer = null;
  if (!start || start.id !== event.pointerId || Math.hypot(event.clientX-start.x, event.clientY-start.y) >= 8) return;
  // A grid can replace the row DOM while scrolling; if the released target no
  // longer matches, open with the link captured at pointerdown instead.
  const link = event.target.closest?.('[data-image-preview]');
  const hit = link && link.href === start.href
    ? link
    : {href: start.href, dataset: {previewName: start.name}};
  openImageAttachmentPreview(hit, event, start.position);
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

function restoreImageAttachmentPreview() {
  imageAttachmentPreviewImage?.removeAttribute("src");
  if(previewPageStyles) {
    previewPageStyles.forEach(([node,style])=>style===null ? node.removeAttribute('style') : node.setAttribute('style',style));
    previewPageStyles=null;
  }
  if(imagePreviewReturn) {
    const saved=imagePreviewReturn;imagePreviewReturn=null;
    saved.link.isConnected && saved.link.focus({preventScroll:true});
    saved.containers.forEach(([node,x,y])=>node.scrollTo(x,y));
    window.scrollTo({left:saved.x,top:saved.y,behavior:'instant'});
  }
}
imageAttachmentPreviewDialog?.addEventListener("close", () => {
  // Native close events are queued. An old event must not clear a newly opened
  // image or move the page while the next attachment is being activated.
  if (!imageAttachmentPreviewDialog.open) restoreImageAttachmentPreview();
});
imageAttachmentPreviewImage?.addEventListener('load',() => {
  applyImageAttachmentPreviewZoom();
});
if(imageAttachmentViewport) new ResizeObserver(()=>{
  if(imageAttachmentPreviewDialog.open) applyImageAttachmentPreviewZoom();
}).observe(imageAttachmentViewport);
