const reportExportConfig = window.reportExportConfig || {};

function exportableReportTable() {
  return Array.from(document.querySelectorAll("main table")).find((table) => (
    !table.closest("dialog") && !table.hasAttribute("data-no-report-export") && table.querySelector("thead th")
  ));
}

function reportTablePayload(table) {
  const headerCells = Array.from(table.querySelectorAll("thead th"));
  const includedIndexes = headerCells
    .map((cell, index) => ({index, label: cell.textContent.trim()}))
    .filter((column) => column.label !== "操作" && column.label !== "选择");
  const rows = Array.from(table.querySelectorAll("tbody tr"))
    .filter((row) => !row.querySelector("td.empty"))
    .map((row) => {
      const cells = Array.from(row.cells);
      return includedIndexes.map(({index}) => (cells[index]?.innerText || "").trim());
    });
  return {
    title: reportExportConfig.title || document.querySelector("main h1")?.textContent?.trim() || "报表",
    headers: includedIndexes.map((column) => column.label),
    rows,
  };
}

async function downloadVisibleReport(button) {
  const table = exportableReportTable();
  if (!table) {
    window.alert("当前页面没有可导出的报表表格。");
    return;
  }
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "正在导出...";
  try {
    const response = await fetch(reportExportConfig.url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(reportTablePayload(table)),
    });
    if (!response.ok) throw new Error("导出请求失败");
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encodedName ? decodeURIComponent(encodedName) : "report.xlsx";
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    window.alert("报表导出失败，请稍后重试。");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

const reportHeader = document.querySelector("main .page-header");
if (reportHeader && exportableReportTable()) {
  let actions = reportHeader.querySelector(":scope > .actions");
  if (!actions) {
    actions = document.createElement("div");
    actions.className = "actions no-print";
    reportHeader.appendChild(actions);
  }
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "导出 Excel";
  button.addEventListener("click", () => downloadVisibleReport(button));
  actions.prepend(button);
}
