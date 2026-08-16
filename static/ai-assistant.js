(() => {
  const shell = document.querySelector(".ai-assistant-shell");
  const form = document.getElementById("aiChatForm");
  const input = document.getElementById("aiChatInput");
  const messageList = document.getElementById("aiChatMessages");
  const submitButton = form?.querySelector('button[type="submit"]');
  const messages = [];

  function appendMessage(role, content, isError = false) {
    const article = document.createElement("article");
    article.className = `ai-message ${role}${isError ? " error" : ""}`;
    const title = document.createElement("strong");
    title.textContent = role === "user" ? "我" : "智能助手";
    const paragraph = document.createElement("p");
    const source = String(content);
    const urlPattern = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s<>()]+)/g;
    let cursor = 0;
    let match;
    while ((match = urlPattern.exec(source)) !== null) {
      paragraph.appendChild(document.createTextNode(source.slice(cursor, match.index)));
      const href = match[2] || match[3];
      if (href) {
        const link = document.createElement("a");
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = match[1] || href;
        paragraph.appendChild(link);
      }
      cursor = urlPattern.lastIndex;
    }
    paragraph.appendChild(document.createTextNode(source.slice(cursor)));
    article.append(title, paragraph);
    messageList.appendChild(article);
    messageList.scrollTop = messageList.scrollHeight;
  }

  async function ask(question) {
    const content = String(question || "").trim();
    if (!content || shell?.dataset.aiEnabled !== "true") return;
    messages.push({ role: "user", content });
    appendMessage("user", content);
    input.value = "";
    submitButton.disabled = true;
    submitButton.textContent = "查询中...";
    try {
      const response = await fetch(window.aiAssistantChatUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `请求失败（${response.status}）`);
      messages.push({ role: "assistant", content: result.answer });
      appendMessage("assistant", result.answer);
    } catch (error) {
      appendMessage("assistant", error.message || "查询失败，请稍后重试。", true);
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "发送";
      input.focus();
    }
  }

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(input.value);
  });
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.querySelectorAll("[data-ai-question]").forEach((button) => {
    button.addEventListener("click", () => ask(button.dataset.aiQuestion));
  });
  document.getElementById("clearAiChat")?.addEventListener("click", () => {
    messages.length = 0;
    messageList.querySelectorAll(".ai-message").forEach((message, index) => {
      if (index > 0) message.remove();
    });
    input?.focus();
  });
})();
