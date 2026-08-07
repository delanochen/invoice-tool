(() => {
  const dialog = document.querySelector("#editKnowledgeDialog");
  if (!dialog) return;
  const form = dialog.querySelector("[data-knowledge-edit-form]");
  const title = dialog.querySelector("[data-knowledge-title]");
  const category = dialog.querySelector("[data-knowledge-category]");
  const description = dialog.querySelector("[data-knowledge-description]");

  document.querySelectorAll("[data-edit-knowledge]").forEach((button) => {
    button.addEventListener("click", () => {
      form.action = button.dataset.action;
      title.value = button.dataset.title || "";
      category.value = button.dataset.category || "";
      description.value = button.dataset.description || "";
      dialog.showModal();
      title.focus();
    });
  });

  dialog.querySelector("[data-close-knowledge-edit]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
