const messageRefreshKey = "invoice-tool-message-read";

function updateUnreadNavigation() {
  const navigation = document.querySelector("[data-message-nav]");
  if (!navigation) return;
  const count = Math.max(0, Number.parseInt(navigation.dataset.unreadCount || "0", 10) - 1);
  navigation.dataset.unreadCount = String(count);
  navigation.textContent = count ? `消息 (${count})` : "消息";
}

document.querySelectorAll("[data-message-link]").forEach((link) => {
  link.addEventListener("click", () => {
    const row = link.closest("[data-message-row]");
    if (!row || row.dataset.isRead === "1") return;

    row.dataset.isRead = "1";
    row.classList.remove("message-unread");
    const status = row.querySelector("[data-message-status]");
    if (status) status.textContent = "已读";
    updateUnreadNavigation();
    sessionStorage.setItem(messageRefreshKey, "1");
  });
});

window.addEventListener("pageshow", (event) => {
  if (sessionStorage.getItem(messageRefreshKey) !== "1") return;
  sessionStorage.removeItem(messageRefreshKey);
  if (event.persisted) window.location.reload();
});
