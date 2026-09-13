const imageAttachmentPreviewDialog = document.querySelector("#imageAttachmentPreviewDialog, #ledgerPhotoDialog");
const imageAttachmentPreviewTitle = document.querySelector("#imageAttachmentPreviewTitle, #ledgerPhotoTitle");
const imageAttachmentPreviewImage = document.querySelector("#imageAttachmentPreviewImage, #ledgerPhotoImage");
const imageAttachmentPreviewFit = document.querySelector("[data-image-preview-fit]");
const imageAttachmentPreviewOut = document.querySelector("[data-image-preview-out]");
const imageAttachmentPreviewOriginal = document.querySelector("[data-image-preview-original]");
const imageAttachmentPreviewIn = document.querySelector("[data-image-preview-in]");
const imageAttachmentPreviewClose = document.querySelector("[data-image-preview-close]");
const imageAttachmentPreviewBackdrop = document.querySelector("#imageAttachmentPreviewBackdrop");
let imageAttachmentPreviewMode = "fit";
let imageAttachmentPreviewZoom = 1;
const imageAttachmentViewport = imageAttachmentPreviewImage?.closest('.ledger-photo-stage, .image-preview-wrap');
imageAttachmentViewport?.classList.add('attachment-image-viewport');
imageAttachmentPreviewImage?.classList.add('attachment-preview-image');
let imagePreviewReturn = null;

// The expense preview is a plain div (display controlled manually) so that
// showModal() can never scroll the page; the field-work preview stays a
// native <dialog>. Branch on the element type so both work.
function isImageAttachmentPreviewOpen() {
  const el = imageAttachmentPreviewDialog;
  if (!el) return false;
  return el.tagName === "DIALOG" ? el.open : el.style.display === "block";
}
function openImageAttachmentPreviewDialog() {
  const el = imageAttachmentPreviewDialog;
  if (!el) return;
  if (el.tagName === "DIALOG") {
    el.showModal();
  } else {
    el.style.display = "block";
    el.classList.add("is-open");
    if (imageAttachmentPreviewBackdrop) imageAttachmentPreviewBackdrop.hidden = false;
  }
}
function closeImageAttachmentPreviewDialog() {
  const el = imageAttachmentPreviewDialog;
  if (!el) return;
  if (el.tagName === "DIALOG") {
    el.close();
  } else {
    el.style.display = "none";
    el.classList.remove("is-open");
    if (imageAttachmentPreviewBackdrop) imageAttachmentPreviewBackdrop.hidden = true;
  }
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
  closeImageAttachmentPreviewDialog();
  restoreImageAttachmentPreview();
}

function openImageAttachmentPreview(link, event) {
  if (!link || !imageAttachmentPreviewDialog || !imageAttachmentPreviewImage) return;
  event.preventDefault();
  // Handle the visible thumbnail before grid adapters forward its click to a hidden source row.
  event.stopPropagation();
  if (isImageAttachmentPreviewOpen() && imageAttachmentPreviewImage.src === link.href) return;
  const containers=[];
  for(let node=link.parentElement;node;node=node.parentElement) {
    if(node.scrollHeight>node.clientHeight || node.scrollWidth>node.clientWidth) containers.push([node,node.scrollLeft,node.scrollTop]);
  }
  imagePreviewReturn={link,containers,x:window.scrollX,y:window.scrollY};
  imageAttachmentPreviewTitle.textContent = link.dataset.previewName || link.textContent.trim() || "附件预览";
  imageAttachmentPreviewImage.src = link.href;
  setImageAttachmentPreviewFit();
  if (!isImageAttachmentPreviewOpen()) openImageAttachmentPreviewDialog();
  applyImageAttachmentPreviewZoom();
  // Chrome may scroll the dialog into view *after* the synchronous call above
  // (a later rendering frame), which would yank the page back to the top when the
  // dialog sits above the current scroll position. Re-apply the saved scroll
  // position now and again over the next frames/timers to win that race.
  const restoreScroll = () => {
    const saved = imagePreviewReturn;
    if (!saved) return;
    if (window.scrollX !== saved.x || window.scrollY !== saved.y) window.scrollTo(saved.x, saved.y);
    for (const [node, sx, sy] of saved.containers) {
      if (node.scrollTop !== sy) node.scrollTop = sy;
      if (node.scrollLeft !== sx) node.scrollLeft = sx;
    }
  };
  restoreScroll();
  requestAnimationFrame(restoreScroll);
  setTimeout(restoreScroll, 50);
  setTimeout(restoreScroll, 250);
}

// A grid can replace a cell between pointerdown and click. Handle the release
// against the visible link as well; retain click for keyboard activation.
let imagePreviewPointer = null;
document.addEventListener('pointerdown', event => {
  const link = event.target.closest?.('[data-image-preview]');
  imagePreviewPointer = event.isPrimary && event.button === 0 && link
    ? {id:event.pointerId, href:link.href, name:link.dataset.previewName || link.textContent.trim() || '', x:event.clientX, y:event.clientY} : null;
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
  openImageAttachmentPreview(hit, event);
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
  if(imagePreviewReturn) {
    const saved=imagePreviewReturn;imagePreviewReturn=null;
    saved.link.isConnected && saved.link.focus({preventScroll:true});
    saved.containers.forEach(([node,x,y])=>node.scrollTo(x,y));
    window.scrollTo(saved.x,saved.y);
  }
}
imageAttachmentPreviewDialog?.addEventListener("close", () => {
  // Native close events are queued. An old event must not clear a newly opened
  // image or move the page while the next attachment is being activated.
  if (!isImageAttachmentPreviewOpen()) restoreImageAttachmentPreview();
});
// The plain div has no native Escape handling; the field-work <dialog> keeps its own.
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && imageAttachmentPreviewDialog?.tagName !== 'DIALOG' && isImageAttachmentPreviewOpen()) {
    closeImageAttachmentPreview();
  }
});
imageAttachmentPreviewImage?.addEventListener('load',applyImageAttachmentPreviewZoom);
if(imageAttachmentViewport) new ResizeObserver(()=>{
  if(isImageAttachmentPreviewOpen()) applyImageAttachmentPreviewZoom();
}).observe(imageAttachmentViewport);
