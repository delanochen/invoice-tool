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
const nasParentButton = document.getElementById("nasParentButton");
const nasSelectionCount = document.getElementById("nasSelectionCount");
let activeNasCategory = "";
let currentNasPath = "";
let currentNasParent = null;
const nasSelections = new Map();
const pendingNasSelection = new Map();

function updateNasSelectionCount() {
  nasSelectionCount.textContent = `已选择 ${pendingNasSelection.size} 张`;
}

function renderNasBrowser(data) {
  nasBrowser.replaceChildren();
  currentNasPath = data.current || "";
  currentNasParent = data.parent;
  nasCurrentPath.textContent = currentNasPath || "照片根目录";
  nasParentButton.disabled = currentNasParent === null;
  if (!data.available) {
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = "共享照片目录尚未挂载或不可访问。";
    nasBrowser.appendChild(message);
    return;
  }
  data.folders.forEach((folder) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nas-folder";
    button.textContent = `文件夹：${folder.name}`;
    button.addEventListener("click", () => loadNasFolder(folder.path));
    nasBrowser.appendChild(button);
  });
  data.images.forEach((image) => {
    const label = document.createElement("label");
    label.className = "nas-photo-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
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
  if (!data.folders.length && !data.images.length) {
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = "这个文件夹中没有可用照片。";
    nasBrowser.appendChild(message);
  }
}

async function loadNasFolder(path = "") {
  nasBrowser.innerHTML = '<p class="empty">正在读取照片...</p>';
  const url = new URL(nasDialog.dataset.browseUrl, window.location.origin);
  url.searchParams.set("path", path);
  const response = await fetch(url);
  if (!response.ok) {
    nasBrowser.innerHTML = '<p class="empty">无法读取这个照片目录。</p>';
    return;
  }
  renderNasBrowser(await response.json());
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
    loadNasFolder("");
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
nasParentButton?.addEventListener("click", () => {
  if (currentNasParent !== null) loadNasFolder(currentNasParent);
});
document.getElementById("confirmNasSelection")?.addEventListener("click", () => {
  const selection = new Map(pendingNasSelection);
  nasSelections.set(activeNasCategory, selection);
  renderSelectedNasPhotos(activeNasCategory);
  nasDialog.close();
});
