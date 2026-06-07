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
let activeNasCategory = "";
let currentNasPath = "";
let currentNasImages = [];
const nasSelections = new Map();
const pendingNasSelection = new Map();

function updateNasSelectionCount() {
  nasSelectionCount.textContent = `已选择 ${pendingNasSelection.size} 张`;
}

function renderNasBrowser(data) {
  nasBrowser.replaceChildren();
  currentNasPath = data.current || "";
  currentNasImages = data.images || [];
  nasCurrentPath.textContent = currentNasPath || nasDialog.dataset.orderNumber;
  if (!data.available) {
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = "共享照片目录尚未挂载或不可访问。";
    nasBrowser.appendChild(message);
    return;
  }
  if (data.folder_exists === false) {
    const message = document.createElement("p");
    message.className = "empty nas-folder-warning";
    message.textContent = "请先创建与工单同名的文件夹，并上传图片";
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
        pendingNasSelection.set(image.path, image.name);
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
    message.textContent = "这个文件夹中没有可用照片。";
    nasBrowser.appendChild(message);
  }
}

async function loadNasFolder(path = "") {
  currentNasImages = [];
  nasBrowser.innerHTML = '<p class="empty">正在读取照片...</p>';
  const url = new URL(nasDialog.dataset.browseUrl, window.location.origin);
  url.searchParams.set("path", path);
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    nasBrowser.innerHTML = '<p class="empty">无法读取这个照片目录。</p>';
    return;
  }
  if (!response.ok) {
    nasBrowser.innerHTML = '<p class="empty">无法读取这个照片目录。</p>';
    return;
  }
  renderNasBrowser(await response.json());
}

function updateVisibleNasSelection(mode) {
  currentNasImages.forEach((image) => {
    const selected = pendingNasSelection.has(image.path);
    if (mode === "select" || (mode === "invert" && !selected)) {
      pendingNasSelection.set(image.path, image.name);
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
  selected.forEach((name, path) => {
    const chip = document.createElement("span");
    chip.className = "nas-selection-chip";
    chip.textContent = name;
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.textContent = "移除";
    removeButton.addEventListener("click", () => {
      selected.delete(path);
      renderSelectedNasPhotos(category);
    });
    chip.appendChild(removeButton);
    container.appendChild(chip);
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = `shared_photo_${category}`;
    hidden.value = path;
    hidden.dataset.sharedCategory = category;
    document.getElementById("serviceReportForm").appendChild(hidden);
  });
}

document.addEventListener("click", (event) => {
  const nasButton = event.target.closest("[data-open-nas]");
  if (nasButton) {
    activeNasCategory = nasButton.dataset.openNas;
    const savedSelection = nasSelections.get(activeNasCategory);
    pendingNasSelection.clear();
    if (savedSelection instanceof Map) {
      savedSelection.forEach((name, path) => pendingNasSelection.set(path, name));
    }
    updateNasSelectionCount();
    nasDialog.showModal();
    loadNasFolder(nasDialog.dataset.orderNumber);
    return;
  }
  const addButton = event.target.closest("[data-add-part]");
  if (addButton) {
    clonePartRow(addButton.dataset.addPart);
    return;
  }
  const removeButton = event.target.closest(".remove-part");
  if (!removeButton) {
    const deleteButton = event.target.closest(".delete-photo");
    if (!deleteButton || !deleteButton.dataset.deleteUrl) return;
    if (!window.confirm("确定删除这张照片吗？")) return;
    const form = document.createElement("form");
    form.method = "post";
    form.action = deleteButton.dataset.deleteUrl;
    document.body.appendChild(form);
    form.submit();
    return;
  }
  const row = removeButton.closest("tr");
  const body = removeButton.closest("tbody");
  if (!row || !body || body.rows.length <= 1) return;
  row.remove();
});

document.getElementById("closeNasDialog")?.addEventListener("click", () => nasDialog.close());
document.getElementById("selectAllNasPhotos")?.addEventListener("click", () => updateVisibleNasSelection("select"));
document.getElementById("clearAllNasPhotos")?.addEventListener("click", () => updateVisibleNasSelection("clear"));
document.getElementById("invertNasPhotos")?.addEventListener("click", () => updateVisibleNasSelection("invert"));
document.getElementById("confirmNasSelection")?.addEventListener("click", () => {
  const selection = new Map(pendingNasSelection);
  nasSelections.set(activeNasCategory, selection);
  renderSelectedNasPhotos(activeNasCategory);
  nasDialog.close();
});
