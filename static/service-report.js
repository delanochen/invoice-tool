function clonePartRow(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const lastRow = table.querySelector("tbody tr:last-child");
  if (!lastRow) return;
  const nextRow = lastRow.cloneNode(true);
  nextRow.querySelectorAll("input").forEach((input) => {
    input.value = "";
  });
  nextRow.querySelectorAll("select").forEach((select) => {
    select.selectedIndex = 0;
  });
  table.querySelector("tbody").appendChild(nextRow);
}

const nasDialog = document.getElementById("nasPhotoDialog");
const nasBrowser = document.getElementById("nasPhotoBrowser");
const nasCurrentPath = document.getElementById("nasCurrentPath");
const nasSelectionCount = document.getElementById("nasSelectionCount");
const nasProcessingStatus = document.getElementById("nasProcessingStatus");
let activeNasCategory = "";
let currentNasPath = "";
let currentNasImages = [];
let nasRefreshTimer = null;
const nasSelections = new Map();
const pendingNasSelection = new Map();
const localPhotoSelections = new Map();
const localPhotoUrls = new Map();
const serviceReportForm = document.getElementById("serviceReportForm");
let serviceReportSubmitting = false;

function reportText(value) {
  if (document.documentElement.lang === "zh-CN" || window.uiLanguage === "zh-CN") return value;
  return window.uiTranslate?.(value) || value;
}

function updateNasSelectionCount() {
  nasSelectionCount.textContent = reportText(`已选择 ${pendingNasSelection.size} 张`);
}

function renderNasProcessingStatus(status = {}) {
  const waiting = Number(status.waiting || 0);
  const processing = Number(status.processing || 0);
  const completed = Number(status.completed || 0);
  const failed = Number(status.failed || 0);
  const active = waiting + processing;
  const parts = [];
  if (active) {
    if (waiting) parts.push(`${waiting} ${reportText("张等待处理")}`);
    if (processing) parts.push(`${processing} ${reportText("张正在处理")}`);
    if (completed) parts.push(`${completed} ${reportText("张已处理成功")}`);
    if (failed) parts.push(`${failed} ${reportText("张处理失败")}`);
  } else if (completed || failed) {
    parts.push(`${completed} ${reportText("张已处理成功")}`);
    parts.push(`${failed} ${reportText("张处理失败")}`);
  }
  const separator = window.uiLanguage === "zh-CN" ? "，" : ", ";
  const sentenceSeparator = window.uiLanguage === "zh-CN" ? "。" : ". ";
  nasProcessingStatus.hidden = parts.length === 0;
  nasProcessingStatus.textContent = parts.length
    ? `${parts.join(separator)}${active ? `${sentenceSeparator}${reportText("窗口会自动刷新。")}` : ""}`
    : "";
}

function renderNasBrowser(data) {
  nasBrowser.replaceChildren();
  currentNasPath = data.current || "";
  currentNasImages = data.images || [];
  nasCurrentPath.textContent = currentNasPath || nasDialog.dataset.orderNumber;
  renderNasProcessingStatus(data.status);
  if (!data.available) {
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = reportText("共享照片目录尚未挂载或不可访问。");
    nasBrowser.appendChild(message);
    return;
  }
  if (data.folder_exists === false) {
    const message = document.createElement("p");
    message.className = "empty nas-folder-warning";
    message.textContent = reportText("请先创建与工单同名的文件夹，并上传图片");
    nasBrowser.appendChild(message);
    return;
  }
  currentNasImages.forEach((image) => {
    const label = document.createElement("label");
    label.className = "nas-photo-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.path = image.path;
    checkbox.checked = pendingNasSelection.has(image.path);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        pendingNasSelection.set(image.path, image);
      } else {
        pendingNasSelection.delete(image.path);
      }
      updateNasSelectionCount();
    });
    const thumbnail = document.createElement("img");
    thumbnail.src = image.thumbnail;
    thumbnail.alt = image.name;
    thumbnail.loading = "lazy";
    const caption = document.createElement("span");
    caption.textContent = image.name;
    label.append(checkbox, thumbnail, caption);
    nasBrowser.appendChild(label);
  });
  if (!currentNasImages.length) {
    const message = document.createElement("p");
    message.className = "empty";
    const status = data.status || {};
    message.textContent = Number(status.waiting || 0) + Number(status.processing || 0) > 0
      ? reportText("新照片正在等待处理，完成后会自动显示。")
      : reportText("这个工单还没有处理完成的照片。");
    nasBrowser.appendChild(message);
  }
}

async function loadNasFolder(path = "", showLoading = true) {
  currentNasImages = [];
  if (showLoading) nasBrowser.innerHTML = `<p class="empty">${reportText("正在读取照片...")}</p>`;
  const url = new URL(nasDialog.dataset.browseUrl, window.location.origin);
  url.searchParams.set("path", path);
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    nasBrowser.innerHTML = `<p class="empty">${reportText("无法读取这个照片目录。")}</p>`;
    return;
  }
  if (!response.ok) {
    nasBrowser.innerHTML = `<p class="empty">${reportText("无法读取这个照片目录。")}</p>`;
    return;
  }
  renderNasBrowser(await response.json());
}

function updateVisibleNasSelection(mode) {
  currentNasImages.forEach((image) => {
    const selected = pendingNasSelection.has(image.path);
    if (mode === "select" || (mode === "invert" && !selected)) {
      pendingNasSelection.set(image.path, image);
    } else if (mode === "clear" || (mode === "invert" && selected)) {
      pendingNasSelection.delete(image.path);
    }
  });
  nasBrowser.querySelectorAll(".nas-photo-option input[type='checkbox']").forEach((checkbox) => {
    checkbox.checked = pendingNasSelection.has(checkbox.dataset.path);
  });
  updateNasSelectionCount();
}

function renderSelectedNasPhotos(category) {
  const container = document.querySelector(`[data-nas-list="${category}"]`);
  container.replaceChildren();
  document.querySelectorAll(`input[data-shared-category="${category}"]`).forEach((input) => input.remove());
  const selected = nasSelections.get(category);
  if (!(selected instanceof Map)) return;
  selected.forEach((image, path) => {
    const card = document.createElement("figure");
    card.className = "selected-photo-card";
    const thumbnail = document.createElement("img");
    thumbnail.src = image.thumbnail;
    thumbnail.alt = image.name;
    const caption = document.createElement("figcaption");
    caption.textContent = image.name;
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "danger small";
    removeButton.textContent = reportText("移除");
    removeButton.addEventListener("click", () => {
      selected.delete(path);
      renderSelectedNasPhotos(category);
    });
    card.append(thumbnail, caption, removeButton);
    container.appendChild(card);
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = `shared_photo_${category}`;
    hidden.value = path;
    hidden.dataset.sharedCategory = category;
    document.getElementById("serviceReportForm").appendChild(hidden);
  });
}

function localFileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function syncLocalPhotoInput(category) {
  const input = document.querySelector(`[data-local-photo="${category}"]`);
  const transfer = new DataTransfer();
  (localPhotoSelections.get(category) || []).forEach((file) => transfer.items.add(file));
  input.files = transfer.files;
}

function renderLocalPhotoPreviews(category) {
  const container = document.querySelector(`[data-local-list="${category}"]`);
  const previousUrls = localPhotoUrls.get(category) || [];
  previousUrls.forEach((url) => URL.revokeObjectURL(url));
  const nextUrls = [];
  container.replaceChildren();
  (localPhotoSelections.get(category) || []).forEach((file, index) => {
    const card = document.createElement("figure");
    card.className = "selected-photo-card";
    const thumbnail = document.createElement("img");
    const objectUrl = URL.createObjectURL(file);
    nextUrls.push(objectUrl);
    thumbnail.src = objectUrl;
    thumbnail.alt = file.name;
    thumbnail.addEventListener("error", () => {
      thumbnail.removeAttribute("src");
      thumbnail.classList.add("preview-unavailable");
      thumbnail.alt = reportText("HEIC 图片将在保存后显示");
    }, { once: true });
    const caption = document.createElement("figcaption");
    caption.textContent = file.name;
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "danger small";
    removeButton.textContent = reportText("移除");
    removeButton.addEventListener("click", () => {
      const files = localPhotoSelections.get(category) || [];
      files.splice(index, 1);
      syncLocalPhotoInput(category);
      renderLocalPhotoPreviews(category);
    });
    card.append(thumbnail, caption, removeButton);
    container.appendChild(card);
  });
  localPhotoUrls.set(category, nextUrls);
}

document.querySelectorAll("[data-local-photo]").forEach((input) => {
  input.addEventListener("change", () => {
    const category = input.dataset.localPhoto;
    const existing = localPhotoSelections.get(category) || [];
    const known = new Set(existing.map(localFileKey));
    Array.from(input.files).forEach((file) => {
      const key = localFileKey(file);
      if (!known.has(key)) {
        existing.push(file);
        known.add(key);
      }
    });
    localPhotoSelections.set(category, existing);
    syncLocalPhotoInput(category);
    renderLocalPhotoPreviews(category);
  });
});

serviceReportForm?.addEventListener("submit", (event) => {
  const submitter = event.submitter;
  if (submitter?.classList.contains("delete-photo")) return;
  const workerInputs = Array.from(document.querySelectorAll("[name='worker_user_id']"));
  const selectedWorker = workerInputs.find((input) => input.checked);
  const workerError = document.getElementById("serviceWorkersError");
  if (!selectedWorker) {
    event.preventDefault();
    workerError.hidden = false;
    workerInputs[0]?.focus();
    return;
  }
  workerError.hidden = true;
  if (serviceReportSubmitting) {
    event.preventDefault();
    return;
  }
  serviceReportSubmitting = true;
  const saveButton = document.getElementById("saveServiceReport");
  if (saveButton) {
    saveButton.disabled = true;
    saveButton.textContent = reportText("保存中...");
    saveButton.setAttribute("aria-busy", "true");
  }
});

document.addEventListener("click", (event) => {
  const nasButton = event.target.closest("[data-open-nas]");
  if (nasButton) {
    activeNasCategory = nasButton.dataset.openNas;
    const savedSelection = nasSelections.get(activeNasCategory);
    pendingNasSelection.clear();
    if (savedSelection instanceof Map) {
      savedSelection.forEach((image, path) => pendingNasSelection.set(path, image));
    }
    updateNasSelectionCount();
    nasDialog.showModal();
    loadNasFolder(nasDialog.dataset.orderNumber);
    window.clearInterval(nasRefreshTimer);
    nasRefreshTimer = window.setInterval(() => {
      if (nasDialog.open) loadNasFolder(nasDialog.dataset.orderNumber, false);
    }, 5000);
    return;
  }
  const addButton = event.target.closest("[data-add-part]");
  if (addButton) {
    clonePartRow(addButton.dataset.addPart);
    return;
  }
  const removeButton = event.target.closest(".remove-part");
  if (!removeButton) {
    return;
  }
  const row = removeButton.closest("tr");
  const body = removeButton.closest("tbody");
  if (!row || !body || body.rows.length <= 1) return;
  row.remove();
});

document.getElementById("closeNasDialog")?.addEventListener("click", () => nasDialog.close());
nasDialog?.addEventListener("close", () => {
  window.clearInterval(nasRefreshTimer);
  nasRefreshTimer = null;
});
document.getElementById("selectAllNasPhotos")?.addEventListener("click", () => updateVisibleNasSelection("select"));
document.getElementById("clearAllNasPhotos")?.addEventListener("click", () => updateVisibleNasSelection("clear"));
document.getElementById("invertNasPhotos")?.addEventListener("click", () => updateVisibleNasSelection("invert"));
document.getElementById("confirmNasSelection")?.addEventListener("click", () => {
  const selection = new Map(pendingNasSelection);
  nasSelections.set(activeNasCategory, selection);
  renderSelectedNasPhotos(activeNasCategory);
  nasDialog.close();
});
