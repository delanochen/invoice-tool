(() => {
  const rows = document.getElementById("batch-export-rows");
  if (!rows) return;

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
            rows.replaceChildren(...next.children);
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
