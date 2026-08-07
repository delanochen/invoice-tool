(() => {
  const dialog = document.querySelector("#editKnowledgeDialog");
  if (!dialog) return;
  const form = dialog.querySelector("[data-knowledge-edit-form]");
  const title = dialog.querySelector("[data-knowledge-title]");
  const category = dialog.querySelector("[data-knowledge-category]");
  const description = dialog.querySelector("[data-knowledge-description]");
  const expiresOn = dialog.querySelector("[data-knowledge-expires]");
  const isPinned = dialog.querySelector("[data-knowledge-pinned]");

  document.querySelectorAll("[data-edit-knowledge]").forEach((button) => {
    button.addEventListener("click", () => {
      form.action = button.dataset.action;
      title.value = button.dataset.title || "";
      category.value = button.dataset.category || "";
      description.value = button.dataset.description || "";
      expiresOn.value = button.dataset.expiresOn || "";
      isPinned.checked = button.dataset.isPinned === "1";
      dialog.showModal();
      title.focus();
    });
  });

  dialog.querySelector("[data-close-knowledge-edit]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });

  const versionDialog = document.querySelector("#newKnowledgeVersionDialog");
  const versionForm = versionDialog.querySelector("[data-knowledge-version-form]");
  const versionTitle = versionDialog.querySelector("[data-knowledge-version-title]");
  document.querySelectorAll("[data-new-knowledge-version]").forEach((button) => {
    button.addEventListener("click", () => {
      versionForm.action = button.dataset.action;
      versionTitle.textContent = button.dataset.title || "";
      versionForm.reset();
      versionDialog.showModal();
    });
  });
  versionDialog.querySelector("[data-close-knowledge-version]").addEventListener("click", () => versionDialog.close());
  versionDialog.addEventListener("click", (event) => {
    if (event.target === versionDialog) versionDialog.close();
  });
})();
