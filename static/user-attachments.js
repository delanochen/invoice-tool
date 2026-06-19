document.querySelectorAll("[data-delete-user-attachment]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("确定删除这个附件吗？")) return;

    button.disabled = true;
    try {
      const response = await fetch(button.dataset.deleteUserAttachment, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json",
        },
      });
      if (!response.ok) throw new Error("delete failed");

      const row = button.closest("[data-attachment-row]");
      const section = button.closest("[data-user-attachments]");
      if (row) row.remove();
      if (section && !section.querySelector("[data-attachment-row]")) {
        const empty = section.querySelector("[data-attachment-empty]");
        if (empty) empty.hidden = false;
      }
    } catch (error) {
      button.disabled = false;
      window.alert("附件删除失败，请重试。");
    }
  });
});
