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

document.addEventListener("click", (event) => {
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
