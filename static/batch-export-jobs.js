(() => {
  const rows = document.getElementById("batch-export-rows");
  if (!rows) return;

  const form = document.getElementById("batch-export-cleanup");
  const selectAll = document.getElementById("batch-export-select-all");
  const submit = document.getElementById("batch-export-cleanup-submit");
  const choices = () => [...rows.querySelectorAll('input[name="job_ids"]')];

  function updateSelection() {
    const options = choices();
    const selected = options.filter((option) => option.checked).length;
    selectAll.disabled = options.length === 0;
    selectAll.checked = selected > 0 && selected === options.length;
    selectAll.indeterminate = selected > 0 && selected < options.length;
    submit.disabled = selected === 0;
  }

  selectAll.addEventListener("change", () => {
    choices().forEach((option) => { option.checked = selectAll.checked; });
    updateSelection();
  });
  rows.addEventListener("change", updateSelection);
  form.addEventListener("submit", (event) => {
    const count = choices().filter((option) => option.checked).length;
    if (!count || !window.confirm('确定清理选中的 ' + count + ' 条导出任务及对应的 ZIP？清理后需重新导出才能下载。')) {
      event.preventDefault();
    }
  });
  updateSelection();

  const pending = '[data-job-status="queued"], [data-job-status="processing"]';

  async function refresh() {
    if (!rows.querySelector(pending)) return;
    if (!document.hidden) {
      try {
        const response = await fetch(window.location.href, { cache: "no-store" });
        if (response.redirected) return;
        if (response.ok) {
          const next = new DOMParser()
            .parseFromString(await response.text(), "text/html")
            .getElementById("batch-export-rows");
          if (next && next.innerHTML !== rows.innerHTML) {
            const selected = new Set(choices().filter((option) => option.checked).map((option) => option.value));
            rows.replaceChildren(...next.children);
            choices().forEach((option) => { option.checked = selected.has(option.value); });
            updateSelection();
          }
        }
      } catch {
        // A transient connection error will be retried on the next poll.
      }
    }
    if (rows.querySelector(pending)) window.setTimeout(refresh, 2500);
  }

  if (rows.querySelector(pending)) window.setTimeout(refresh, 1000);
})();
