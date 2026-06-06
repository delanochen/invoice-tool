const expenseItems = document.querySelector("#expenseItems");
const addExpenseItem = document.querySelector("#addExpenseItem");
const expenseTotal = document.querySelector("#expenseTotal");

function escapeExpenseHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function expenseProjectOptions() {
  return [
    '<option value="">选择报销项目</option>',
    ...(window.expenseProjects || [])
      .filter((project) => project.active)
      .map((project) => `<option value="${project.id}">${escapeExpenseHtml(project.name)}</option>`),
  ].join("");
}

function updateExpenseTotal() {
  const total = Array.from(document.querySelectorAll("[name='item_amount']")).reduce(
    (sum, input) => sum + (Number.parseFloat(input.value) || 0),
    0
  );
  if (expenseTotal) expenseTotal.textContent = total.toFixed(2);
}

function bindExpenseRows() {
  document.querySelectorAll(".remove-expense-item").forEach((button) => {
    button.onclick = () => {
      const rows = document.querySelectorAll(".expense-item-row");
      if (rows.length > 1) {
        button.closest(".expense-item-row").remove();
        updateExpenseTotal();
      }
    };
  });
  document.querySelectorAll("[name='item_amount']").forEach((input) => {
    input.oninput = updateExpenseTotal;
  });
}

addExpenseItem?.addEventListener("click", () => {
  const row = document.createElement("div");
  row.className = "expense-item-row";
  row.innerHTML = `
    <select name="project_id" class="expense-project-select" aria-label="报销项目" required>
      ${expenseProjectOptions()}
    </select>
    <input type="number" step="0.01" min="0.01" name="item_amount" placeholder="金额（USD）" aria-label="金额" required>
    <input name="item_description" placeholder="明细说明" aria-label="明细说明">
    <button type="button" class="ghost remove-expense-item">删除</button>
  `;
  expenseItems.appendChild(row);
  bindExpenseRows();
  row.querySelector("select").focus();
});

bindExpenseRows();
updateExpenseTotal();
