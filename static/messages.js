const messageRefreshKey = "invoice-tool-message-read";

function messageText(value) {
  return window.uiTranslate?.(value) || value;
}

const messageLanguage = document.documentElement.lang;
const messageTranslations = {
  en: {
    externalPending: "New external employee account pending approval",
    employeePending: "New employee account pending approval",
    accountEnabled: "Account enabled",
    newExpense: "New expense pending review",
    expenseSubmitted: "Expense submitted for review",
    expenseApproved: "Expense approved",
    expenseReturned: "Expense returned",
    expenseReimbursed: "Expense reimbursed",
    newSettlement: "New work order settlement pending review",
    settlementApproved: "Work order settlement approved",
    settlementReturned: "Work order settlement returned",
    invoiceSubmitted: "Invoice submitted for review",
    invoiceReviewed: "Invoice review completed",
    invoiceConfirmed: "Invoice confirmed completed",
    invoiceAdjusted: "Invoice status adjusted",
    registeredExternal: (name) => `${name} registered an external employee account. Please review and enable it.`,
    registeredEmployee: (name) => `${name} registered an employee account. Please review and enable it.`,
    enabledBy: (name) => `${name} approved and enabled your account.`,
    submittedExpense: (name, expense, order, amount) => `${name} submitted expense ${expense}, work order ${order}, amount ${amount}.`,
    approvedExpense: (name, expense, order, amount) => `${name} approved expense ${expense}, work order ${order}, amount ${amount}.`,
    returnedExpense: (expense, reason) => `Expense ${expense} was returned. Reason: ${reason}`,
    reimbursedExpense: (expense, amount) => `Expense ${expense} has been reimbursed, amount ${amount}.`,
    submittedSettlement: (name, order, amount) => `${name} submitted the settlement for work order ${order}, amount ${amount}.`,
    approvedSettlement: (name, order, amount) => `${name} approved the settlement for work order ${order}, amount ${amount}.`,
    returnedSettlement: (order, reason) => `The settlement for work order ${order} was returned. Reason: ${reason}`,
    submittedInvoice: (name, invoice, client, amount) => `${name} submitted invoice ${invoice}, client: ${client}, amount: ${amount}. Please review.`,
    resubmittedInvoice: (name, invoice, client, amount) => `${name} resubmitted invoice ${invoice}, client: ${client}, amount: ${amount}. Please review.`,
    returnedInvoice: (invoice, client, amount, reason) => `Invoice ${invoice} was returned by the manager. Client: ${client}, amount: ${amount}. Reason: ${reason}`,
    reviewedInvoice: (name, invoice) => `${name} completed review for invoice ${invoice}.`,
    adjustedInvoice: (invoice) => `An administrator changed invoice ${invoice} back to draft.`,
  },
  nl: {
    externalPending: "Nieuwe externe-medewerkeraccount wacht op goedkeuring",
    employeePending: "Nieuwe medewerkeraccount wacht op goedkeuring",
    accountEnabled: "Account ingeschakeld",
    newExpense: "Nieuwe onkosten wachten op beoordeling",
    expenseSubmitted: "Onkosten ingediend voor beoordeling",
    expenseApproved: "Onkosten goedgekeurd",
    expenseReturned: "Onkosten teruggestuurd",
    expenseReimbursed: "Onkosten vergoed",
    newSettlement: "Nieuwe werkorderafrekening wacht op beoordeling",
    settlementApproved: "Werkorderafrekening goedgekeurd",
    settlementReturned: "Werkorderafrekening teruggestuurd",
    invoiceSubmitted: "Factuur ingediend voor beoordeling",
    invoiceReviewed: "Factuurbeoordeling voltooid",
    invoiceConfirmed: "Factuur bevestigd als voltooid",
    invoiceAdjusted: "Factuurstatus aangepast",
    registeredExternal: (name) => `${name} heeft een extern medewerkeraccount geregistreerd. Controleer en schakel het in.`,
    registeredEmployee: (name) => `${name} heeft een medewerkeraccount geregistreerd. Controleer en schakel het in.`,
    enabledBy: (name) => `${name} heeft je account goedgekeurd en ingeschakeld.`,
    submittedExpense: (name, expense, order, amount) => `${name} heeft onkosten ${expense} ingediend, werkorder ${order}, bedrag ${amount}.`,
    approvedExpense: (name, expense, order, amount) => `${name} heeft onkosten ${expense} goedgekeurd, werkorder ${order}, bedrag ${amount}.`,
    returnedExpense: (expense, reason) => `Onkosten ${expense} zijn teruggestuurd. Reden: ${reason}`,
    reimbursedExpense: (expense, amount) => `Onkosten ${expense} zijn vergoed, bedrag ${amount}.`,
    submittedSettlement: (name, order, amount) => `${name} heeft de afrekening voor werkorder ${order} ingediend, bedrag ${amount}.`,
    approvedSettlement: (name, order, amount) => `${name} heeft de afrekening voor werkorder ${order} goedgekeurd, bedrag ${amount}.`,
    returnedSettlement: (order, reason) => `De afrekening voor werkorder ${order} is teruggestuurd. Reden: ${reason}`,
    submittedInvoice: (name, invoice, client, amount) => `${name} heeft factuur ${invoice} ingediend, klant: ${client}, bedrag: ${amount}. Controleer deze.`,
    resubmittedInvoice: (name, invoice, client, amount) => `${name} heeft factuur ${invoice} opnieuw ingediend, klant: ${client}, bedrag: ${amount}. Controleer deze.`,
    returnedInvoice: (invoice, client, amount, reason) => `Factuur ${invoice} is door de manager teruggestuurd. Klant: ${client}, bedrag: ${amount}. Reden: ${reason}`,
    reviewedInvoice: (name, invoice) => `${name} heeft de beoordeling van factuur ${invoice} voltooid.`,
    adjustedInvoice: (invoice) => `Een beheerder heeft factuur ${invoice} teruggezet naar concept.`,
  },
  de: {
    externalPending: "Neues Konto für externen Mitarbeiter wartet auf Genehmigung",
    employeePending: "Neues Mitarbeiterkonto wartet auf Genehmigung",
    accountEnabled: "Konto aktiviert",
    newExpense: "Neue Spese wartet auf Prüfung",
    expenseSubmitted: "Spese zur Prüfung eingereicht",
    expenseApproved: "Spese genehmigt",
    expenseReturned: "Spese zurückgesendet",
    expenseReimbursed: "Spese erstattet",
    newSettlement: "Neue Arbeitsauftragsabrechnung wartet auf Prüfung",
    settlementApproved: "Arbeitsauftragsabrechnung genehmigt",
    settlementReturned: "Arbeitsauftragsabrechnung zurückgesendet",
    invoiceSubmitted: "Rechnung zur Prüfung eingereicht",
    invoiceReviewed: "Rechnungsprüfung abgeschlossen",
    invoiceConfirmed: "Rechnung als abgeschlossen bestätigt",
    invoiceAdjusted: "Rechnungsstatus angepasst",
    registeredExternal: (name) => `${name} hat ein Konto für externe Mitarbeiter registriert. Bitte prüfen und aktivieren.`,
    registeredEmployee: (name) => `${name} hat ein Mitarbeiterkonto registriert. Bitte prüfen und aktivieren.`,
    enabledBy: (name) => `${name} hat dein Konto genehmigt und aktiviert.`,
    submittedExpense: (name, expense, order, amount) => `${name} hat Spese ${expense} eingereicht, Arbeitsauftrag ${order}, Betrag ${amount}.`,
    approvedExpense: (name, expense, order, amount) => `${name} hat Spese ${expense} genehmigt, Arbeitsauftrag ${order}, Betrag ${amount}.`,
    returnedExpense: (expense, reason) => `Spese ${expense} wurde zurückgesendet. Grund: ${reason}`,
    reimbursedExpense: (expense, amount) => `Spese ${expense} wurde erstattet, Betrag ${amount}.`,
    submittedSettlement: (name, order, amount) => `${name} hat die Abrechnung für Arbeitsauftrag ${order} eingereicht, Betrag ${amount}.`,
    approvedSettlement: (name, order, amount) => `${name} hat die Abrechnung für Arbeitsauftrag ${order} genehmigt, Betrag ${amount}.`,
    returnedSettlement: (order, reason) => `Die Abrechnung für Arbeitsauftrag ${order} wurde zurückgesendet. Grund: ${reason}`,
    submittedInvoice: (name, invoice, client, amount) => `${name} hat Rechnung ${invoice} eingereicht, Kunde: ${client}, Betrag: ${amount}. Bitte prüfen.`,
    resubmittedInvoice: (name, invoice, client, amount) => `${name} hat Rechnung ${invoice} erneut eingereicht, Kunde: ${client}, Betrag: ${amount}. Bitte prüfen.`,
    returnedInvoice: (invoice, client, amount, reason) => `Rechnung ${invoice} wurde vom Manager zurückgesendet. Kunde: ${client}, Betrag: ${amount}. Grund: ${reason}`,
    reviewedInvoice: (name, invoice) => `${name} hat die Prüfung der Rechnung ${invoice} abgeschlossen.`,
    adjustedInvoice: (invoice) => `Ein Administrator hat Rechnung ${invoice} auf Entwurf zurückgesetzt.`,
  },
  es: {
    externalPending: "Nueva cuenta de empleado externo pendiente de aprobación",
    employeePending: "Nueva cuenta de empleado pendiente de aprobación",
    accountEnabled: "Cuenta habilitada",
    newExpense: "Nuevo gasto pendiente de revisión",
    expenseSubmitted: "Gasto enviado para revisión",
    expenseApproved: "Gasto aprobado",
    expenseReturned: "Gasto devuelto",
    expenseReimbursed: "Gasto reembolsado",
    newSettlement: "Nueva liquidación de orden pendiente de revisión",
    settlementApproved: "Liquidación de orden aprobada",
    settlementReturned: "Liquidación de orden devuelta",
    invoiceSubmitted: "Factura enviada para revisión",
    invoiceReviewed: "Revisión de factura completada",
    invoiceConfirmed: "Factura confirmada como completada",
    invoiceAdjusted: "Estado de factura ajustado",
    registeredExternal: (name) => `${name} registró una cuenta de empleado externo. Revísela y habilítela.`,
    registeredEmployee: (name) => `${name} registró una cuenta de empleado. Revísela y habilítela.`,
    enabledBy: (name) => `${name} aprobó y habilitó tu cuenta.`,
    submittedExpense: (name, expense, order, amount) => `${name} envió el gasto ${expense}, orden ${order}, importe ${amount}.`,
    approvedExpense: (name, expense, order, amount) => `${name} aprobó el gasto ${expense}, orden ${order}, importe ${amount}.`,
    returnedExpense: (expense, reason) => `El gasto ${expense} fue devuelto. Motivo: ${reason}`,
    reimbursedExpense: (expense, amount) => `El gasto ${expense} ha sido reembolsado, importe ${amount}.`,
    submittedSettlement: (name, order, amount) => `${name} envió la liquidación de la orden ${order}, importe ${amount}.`,
    approvedSettlement: (name, order, amount) => `${name} aprobó la liquidación de la orden ${order}, importe ${amount}.`,
    returnedSettlement: (order, reason) => `La liquidación de la orden ${order} fue devuelta. Motivo: ${reason}`,
    submittedInvoice: (name, invoice, client, amount) => `${name} envió la factura ${invoice}, cliente: ${client}, importe: ${amount}. Revísela.`,
    resubmittedInvoice: (name, invoice, client, amount) => `${name} volvió a enviar la factura ${invoice}, cliente: ${client}, importe: ${amount}. Revísela.`,
    returnedInvoice: (invoice, client, amount, reason) => `La factura ${invoice} fue devuelta por el gerente. Cliente: ${client}, importe: ${amount}. Motivo: ${reason}`,
    reviewedInvoice: (name, invoice) => `${name} completó la revisión de la factura ${invoice}.`,
    adjustedInvoice: (invoice) => `Un administrador cambió la factura ${invoice} de nuevo a borrador.`,
  },
};

for (const language of ["nl", "de", "es"]) {
  messageTranslations[language] = { ...messageTranslations.en, ...messageTranslations[language] };
}

function mt(key, ...values) {
  const translations = messageTranslations[messageLanguage] || messageTranslations.en;
  const entry = translations[key] || messageTranslations.en[key];
  return typeof entry === "function" ? entry(...values) : entry;
}

function translateMessageValue(value) {
  if (document.documentElement.lang === "zh-CN") return value;
  const text = String(value || "");
  const direct = messageText(text);
  if (direct !== text) return direct;

  const replacements = [
    [/^新外部员工账号待批准$/, mt("externalPending")],
    [/^新员工账号待批准$/, mt("employeePending")],
    [/^账号已启用$/, mt("accountEnabled")],
    [/^新报销待审核$/, mt("newExpense")],
    [/^报销已提交审核$/, mt("expenseSubmitted")],
    [/^报销已审核通过$/, mt("expenseApproved")],
    [/^报销已被退回$/, mt("expenseReturned")],
    [/^报销已发放$/, mt("expenseReimbursed")],
    [/^新工单结算待审核$/, mt("newSettlement")],
    [/^工单结算已审核通过$/, mt("settlementApproved")],
    [/^工单结算已被退回$/, mt("settlementReturned")],
    [/^发票已提交审核$/, mt("invoiceSubmitted")],
    [/^发票已审核完成$/, mt("invoiceReviewed")],
    [/^发票已确认完成$/, mt("invoiceConfirmed")],
    [/^发票状态已调整$/, mt("invoiceAdjusted")],
  ];
  for (const [pattern, replacement] of replacements) {
    if (pattern.test(text)) return replacement;
  }

  let translated = text;
  translated = translated.replace(/^(.+?) 已注册外部员工账号，请审核并启用。$/, (_, name) => mt("registeredExternal", name));
  translated = translated.replace(/^(.+?) 已注册员工账号，请审核并启用。$/, (_, name) => mt("registeredEmployee", name));
  translated = translated.replace(/^(.+?) 已批准并启用你的账号。$/, (_, name) => mt("enabledBy", name));
  translated = translated.replace(/^(.+?)提交了报销 (.+?)，工单 (.+?)，金额 (.+?)。$/, (_, name, expense, order, amount) => mt("submittedExpense", name, expense, order, amount));
  translated = translated.replace(/^(.+?)已审核通过报销 (.+?)，工单 (.+?)，金额 (.+?)。$/, (_, name, expense, order, amount) => mt("approvedExpense", name, expense, order, amount));
  translated = translated.replace(/^报销 (.+?) 已被退回。原因：(.+)$/, (_, expense, reason) => mt("returnedExpense", expense, reason));
  translated = translated.replace(/^报销 (.+?) 已完成发放，金额 (.+?)。$/, (_, expense, amount) => mt("reimbursedExpense", expense, amount));
  translated = translated.replace(/^(.+?)提交了工单 (.+?) 的工单结算，金额 (.+?)。$/, (_, name, order, amount) => mt("submittedSettlement", name, order, amount));
  translated = translated.replace(/^(.+?)已审核通过工单 (.+?) 的工单结算，金额 (.+?)。$/, (_, name, order, amount) => mt("approvedSettlement", name, order, amount));
  translated = translated.replace(/^工单 (.+?) 的工单结算已被退回。原因：(.+)$/, (_, order, reason) => mt("returnedSettlement", order, reason));
  translated = translated.replace(/^(.+?)提交了发票 (.+?) 客户:(.+?) 金额:(.+?) 请审核。$/, (_, name, invoice, client, amount) => mt("submittedInvoice", name, invoice, client, amount));
  translated = translated.replace(/^(.+?)重新提交了发票 (.+?) 客户:(.+?) 金额:(.+?) 请审核。$/, (_, name, invoice, client, amount) => mt("resubmittedInvoice", name, invoice, client, amount));
  translated = translated.replace(/^(.+?)提交了 发票 (.+?) 客户:(.+?) 金额:(.+?) 请审核。$/, (_, name, invoice, client, amount) => mt("submittedInvoice", name, invoice, client, amount));
  translated = translated.replace(/^发票 (.+?) 已被经理退回。客户:(.+?) 金额:(.+?) 原因：(.+)$/, (_, invoice, client, amount, reason) => mt("returnedInvoice", invoice, client, amount, reason));
  translated = translated.replace(/^(.+?)已审核完成发票(.+?)。$/, (_, name, invoice) => mt("reviewedInvoice", name, invoice));
  translated = translated.replace(/^管理员已将发票 (.+?) 调整为保存未提交状态。$/, (_, invoice) => mt("adjustedInvoice", invoice));
  return translated;
}

// v0.1.307：消息从「主菜单」下拉提升为菜单栏上的一级入口，未读数显示为红底徽标，
// 而不是在「消息」二字后面缀一个 "(2)"。data-message-nav 因此挂在**徽标** <span> 上：
// 它的兄弟节点是图标与「消息」文字，对父级 <a> 做 textContent = 会把图标一起抹掉。
//   count > 0 → 写数字（>99 收敛成 99+）并去掉 hidden
//   count = 0 → 清空文本并加 hidden（CSS 里 .nav-badge[hidden] 显式 display:none，
//               因为 .nav-badge 自己设了 display:inline-flex，会盖掉 UA 的 [hidden] 规则）
function paintMessageNav(count) {
  const badge = document.querySelector("[data-message-nav]");
  if (!badge) return;
  const safe = Math.max(0, count);
  badge.dataset.unreadCount = String(safe);
  badge.textContent = safe > 99 ? "99+" : safe ? String(safe) : "";
  badge.hidden = !safe;
  const label = messageText("消息");
  badge.setAttribute("aria-label", safe ? `${label}（${safe} 条未读）` : label);
}

function translateMessages() {
  document.querySelectorAll("[data-message-title], [data-message-body], [data-message-status]").forEach((element) => {
    element.textContent = translateMessageValue(element.textContent);
  });
  const navigation = document.querySelector("[data-message-nav]");
  if (navigation) {
    paintMessageNav(Math.max(0, Number.parseInt(navigation.dataset.unreadCount || "0", 10)));
  }
}

function updateUnreadNavigation() {
  const navigation = document.querySelector("[data-message-nav]");
  if (!navigation) return;
  const count = Math.max(0, Number.parseInt(navigation.dataset.unreadCount || "0", 10) - 1);
  paintMessageNav(count);
}

document.querySelectorAll("[data-message-link]").forEach((link) => {
  link.addEventListener("click", () => {
    const row = link.closest("[data-message-row]");
    if (!row || row.dataset.isRead === "1") return;

    row.dataset.isRead = "1";
    row.classList.remove("message-unread");
    const status = row.querySelector("[data-message-status]");
    if (status) status.textContent = translateMessageValue("已读");
    updateUnreadNavigation();
    sessionStorage.setItem(messageRefreshKey, "1");
  });
});

// ── 勾选与批量标记 ────────────────────────────────────────────────────────
// 页面里的表格会被 system-grid.js 镜像成 Tabulator，鼠标点到的是镜像出来的
// checkbox；镜像层会把状态写回原 checkbox 并派发 change（见 system-grid.js
// 的 mirror()）。所以「哪些行被选中」必须始终以**原表**里的 checkbox 为准。
//
// 关键：镜像出来的行也在**同一个 document** 里（.system-grid 是原表的兄弟
// 节点），所以 `document.querySelectorAll('[data-message-select]')` 会把原件
// 和副本一起数进来 —— 勾一行会被算成两行。必须把副本排除掉。
function messageSourceCheckboxes() {
  return [...document.querySelectorAll("[data-message-select]")].filter(
    (box) => !box.closest(".system-grid")
  );
}

function selectedMessageIds() {
  return messageSourceCheckboxes()
    .filter((box) => box.checked)
    .map((box) => box.value);
}

function messageSelection() {
  const boxes = messageSourceCheckboxes();
  const selected = boxes.filter((box) => box.checked);
  const markRead = document.querySelector('[data-message-mark-selected][data-mark-read="1"]');
  const markUnread = document.querySelector('[data-message-mark-selected][data-mark-read="0"]');
  for (const button of [markRead, markUnread]) {
    if (button) button.disabled = selected.length === 0;
  }
  const all = document.querySelector("[data-message-select-all]");
  if (all) {
    all.checked = boxes.length > 0 && selected.length === boxes.length;
    all.indeterminate = selected.length > 0 && selected.length < boxes.length;
  }
  const count = document.querySelector("[data-message-selected-count]");
  if (count) count.textContent = selected.length ? `已选 ${selected.length} 条` : "";
}

async function submitMessageMark(ids, isRead) {
  if (!ids.length) return;
  const form = new FormData();
  form.set("is_read", isRead ? "1" : "0");
  for (const id of ids) form.append("message_id", id);
  const response = await fetch(document.getElementById("messageMarkForm").action, {
    method: "POST",
    body: form,
    headers: { "X-Requested-With": "XMLHttpRequest" },
  });
  if (!response.ok) return;
  // 标记后行内状态与未读徽标都要跟着变；排序规则是未读在前，
  // 局部改 class 不会重排，所以这一步直接整页刷新最稳。
  window.location.reload();
}

document.querySelector("[data-message-select-all]")?.addEventListener("change", (event) => {
  const checked = event.target.checked;
  messageSourceCheckboxes().forEach((box) => {
    box.checked = checked;
    // 镜像 DOM 是独立副本，原 checkbox 改了要同步通知它一次。
    box.dispatchEvent(new Event("change", { bubbles: true }));
  });
  messageSelection();
});

document.addEventListener("change", (event) => {
  if (event.target.closest("[data-message-select]")) messageSelection();
});

document.querySelectorAll("[data-message-mark-selected]").forEach((button) => {
  button.addEventListener("click", () => {
    submitMessageMark(selectedMessageIds(), button.dataset.markRead === "1");
  });
});

document.querySelector("[data-message-mark-all]")?.addEventListener("click", () => {
  const form = document.getElementById("messageMarkAllForm");
  if (form) form.submit();
});

messageSelection();

translateMessages();

window.addEventListener("pageshow", (event) => {
  if (sessionStorage.getItem(messageRefreshKey) !== "1") return;
  sessionStorage.removeItem(messageRefreshKey);
  if (event.persisted) window.location.reload();
});
