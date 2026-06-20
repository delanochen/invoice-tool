document.addEventListener("click", (event) => {
  const addButton = event.target.closest("[data-add-translation]");
  if (addButton) {
    const tbody = addButton.closest("[data-translation-list]").querySelector("[data-translation-rows]");
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><input name="language_code" placeholder="例如 de" required></td>
      <td><input name="translation_name" required></td>
      <td><input name="translation_region_name" required></td>
      <td><button class="danger small" type="button" data-remove-translation>删除</button></td>
    `;
    tbody.appendChild(row);
    row.querySelector("input").focus();
    return;
  }
  const removeButton = event.target.closest("[data-remove-translation]");
  if (removeButton) {
    const tbody = removeButton.closest("tbody");
    if (tbody.querySelectorAll("tr").length > 1) {
      removeButton.closest("tr").remove();
    }
  }
});
