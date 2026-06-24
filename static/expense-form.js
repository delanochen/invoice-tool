const expenseItems = document.querySelector("#expenseItems");
const addExpenseItem = document.querySelector("#addExpenseItem");
const expenseTotal = document.querySelector("#expenseTotal");
const expenseForm = document.querySelector("#expenseForm");
const expenseAttachmentsInput = document.querySelector("#expenseAttachmentsInput");
const selectedAttachmentPanel = document.querySelector("[data-expense-attachment-panel]");
const selectedAttachmentList = document.querySelector("[data-expense-attachment-list]");
let expenseSubmitting = false;
let accumulatedExpenseFiles = [];

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

function formatExpenseFileSize(size) {
  if (!Number.isFinite(size)) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function syncExpenseAttachmentInput() {
  if (!expenseAttachmentsInput || typeof DataTransfer === "undefined") return false;
  const dataTransfer = new DataTransfer();
  accumulatedExpenseFiles.forEach((file) => dataTransfer.items.add(file));
  expenseAttachmentsInput.files = dataTransfer.files;
  return true;
}

function renderExpenseAttachmentList() {
  if (!selectedAttachmentPanel || !selectedAttachmentList) return;
  selectedAttachmentPanel.hidden = accumulatedExpenseFiles.length === 0;
  selectedAttachmentList.innerHTML = "";
  accumulatedExpenseFiles.forEach((file, index) => {
    const item = document.createElement("li");
    item.className = "selected-attachment-item";

    const label = document.createElement("span");
    label.textContent = `${file.name} (${formatExpenseFileSize(file.size)})`;

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "ghost small";
    removeButton.textContent = window.uiTranslate?.("移除") || "移除";
    removeButton.addEventListener("click", () => {
      accumulatedExpenseFiles.splice(index, 1);
      syncExpenseAttachmentInput();
      renderExpenseAttachmentList();
      expenseAttachmentsInput.dataset.syncing = "true";
      expenseAttachmentsInput?.dispatchEvent(new Event("change", { bubbles: false }));
    });

    item.append(label, removeButton);
    selectedAttachmentList.appendChild(item);
  });
}

expenseAttachmentsInput?.addEventListener("change", () => {
  if (expenseAttachmentsInput.dataset.syncing === "true") {
    delete expenseAttachmentsInput.dataset.syncing;
    return;
  }
  const newlySelectedFiles = Array.from(expenseAttachmentsInput.files || []);
  if (!newlySelectedFiles.length) {
    return;
  }
  if (typeof DataTransfer === "undefined") {
    accumulatedExpenseFiles = newlySelectedFiles;
    renderExpenseAttachmentList();
    return;
  }
  accumulatedExpenseFiles.push(...newlySelectedFiles);
  syncExpenseAttachmentInput();
  renderExpenseAttachmentList();
});

addExpenseItem?.addEventListener("click", () => {
  const row = document.createElement("tr");
  row.className = "expense-item-row";
  row.innerHTML = `
    <td><select name="project_id" class="expense-project-select" aria-label="报销项目" required>
      ${expenseProjectOptions()}
    </select></td>
    <td><input type="number" step="0.01" min="0.01" name="item_amount" placeholder="金额（USD）" aria-label="金额" required></td>
    <td><input name="item_description" placeholder="明细说明" aria-label="明细说明"></td>
    <td><button type="button" class="ghost remove-expense-item">删除</button></td>
  `;
  expenseItems.appendChild(row);
  bindExpenseRows();
  row.querySelector("select").focus();
});

bindExpenseRows();
updateExpenseTotal();

expenseForm?.addEventListener("submit", (event) => {
  if (expenseSubmitting) {
    event.preventDefault();
    return;
  }
  expenseSubmitting = true;
  const submitter = event.submitter;
  if (submitter?.name === "action") {
    const action = document.createElement("input");
    action.type = "hidden";
    action.name = "action";
    action.value = submitter.value;
    expenseForm.appendChild(action);
  }
  document.querySelectorAll("[data-expense-submit]").forEach((button) => {
    button.disabled = true;
    button.textContent = button === submitter ? "处理中..." : button.textContent;
    button.setAttribute("aria-busy", "true");
  });
});
