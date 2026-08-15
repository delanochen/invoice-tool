(() => {
  const excludedInputIds = new Set(["attachmentsInput", "detailAttachmentsInput"]);

  function fileKey(file) {
    return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
  }

  function fileSize(size) {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  }

  function fileKind(file) {
    const name = file.name.toLowerCase();
    if (file.type.startsWith("image/")) return "image";
    if (file.type === "application/pdf" || name.endsWith(".pdf")) return "pdf";
    if (/\.(doc|docx)$/.test(name)) return "word";
    if (/\.(xls|xlsx|csv)$/.test(name)) return "excel";
    return "file";
  }

  function translate(text) {
    return window.uiTranslate?.(text) || text;
  }

  function setup(input) {
    if (
      input.dataset.pendingAttachmentReady
      || input.dataset.localPhoto
      || input.name === "import_file"
      || excludedInputIds.has(input.id)
    ) return;
    input.dataset.pendingAttachmentReady = "true";
    let files = [];
    const urls = new Map();
    const panel = document.createElement("section");
    panel.className = "pending-attachment-panel";
    panel.hidden = true;
    panel.innerHTML = `
      <div class="pending-attachment-heading">
        <strong>${translate("待上传附件")}</strong>
        <span>${translate("保存后进入已保存附件")}</span>
      </div>
      <div class="pending-attachment-grid"></div>
    `;
    const anchor = input.closest("label, .file-picker-row") || input;
    anchor.insertAdjacentElement("afterend", panel);
    const grid = panel.querySelector(".pending-attachment-grid");

    function syncInput() {
      if (typeof DataTransfer === "undefined") return;
      const transfer = new DataTransfer();
      files.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
    }

    function localUrl(file) {
      const key = fileKey(file);
      if (!urls.has(key)) urls.set(key, URL.createObjectURL(file));
      return urls.get(key);
    }

    function removeFile(index) {
      const [removed] = files.splice(index, 1);
      const key = removed && fileKey(removed);
      if (key && urls.has(key)) {
        URL.revokeObjectURL(urls.get(key));
        urls.delete(key);
      }
      syncInput();
      render();
    }

    function render() {
      panel.hidden = files.length === 0;
      grid.replaceChildren();
      files.forEach((file, index) => {
        const kind = fileKind(file);
        const card = document.createElement("article");
        card.className = "pending-attachment-card";
        const preview = document.createElement(kind === "image" || kind === "pdf" ? "a" : "div");
        preview.className = `pending-attachment-preview is-${kind}`;
        if (kind === "image") {
          preview.href = localUrl(file);
          preview.dataset.imagePreview = "";
          preview.dataset.previewName = file.name;
          const image = document.createElement("img");
          image.src = preview.href;
          image.alt = file.name;
          preview.appendChild(image);
        } else if (kind === "pdf") {
          preview.href = localUrl(file);
          preview.target = "_blank";
          preview.rel = "noopener";
          preview.textContent = "PDF";
          preview.title = translate("预览");
        } else {
          preview.textContent = kind === "word" ? "DOC" : kind === "excel" ? "XLS" : "FILE";
        }
        const details = document.createElement("div");
        details.className = "pending-attachment-details";
        const name = document.createElement("strong");
        name.textContent = file.name;
        const meta = document.createElement("span");
        meta.textContent = `${fileSize(file.size)} · ${translate("待上传")}`;
        details.append(name, meta);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "danger small";
        remove.textContent = translate("移除");
        remove.addEventListener("click", () => removeFile(index));
        card.append(preview, details, remove);
        grid.appendChild(card);
      });
    }

    input.addEventListener("change", () => {
      const known = new Set(files.map(fileKey));
      Array.from(input.files || []).forEach((file) => {
        if (!known.has(fileKey(file))) {
          files.push(file);
          known.add(fileKey(file));
        }
      });
      syncInput();
      render();
    });
    input.form?.addEventListener("reset", () => {
      urls.forEach((url) => URL.revokeObjectURL(url));
      urls.clear();
      files = [];
      render();
    });
  }

  document.querySelectorAll('input[type="file"]').forEach(setup);
})();
