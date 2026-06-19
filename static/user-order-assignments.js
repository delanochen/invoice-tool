document.querySelectorAll("[data-order-status-filter]").forEach((filter) => {
  const table = document.getElementById(filter.dataset.orderStatusFilter);
  if (!table) return;

  const applyFilter = () => {
    const selectedStatus = filter.value;
    let visibleCount = 0;
    table.querySelectorAll("tbody tr[data-order-status]").forEach((row) => {
      const visible = selectedStatus === "all" || row.dataset.orderStatus === selectedStatus;
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    const emptyRow = table.querySelector("[data-filter-empty]");
    if (emptyRow) emptyRow.hidden = visibleCount !== 0;
  };

  filter.addEventListener("change", applyFilter);
  applyFilter();
});
