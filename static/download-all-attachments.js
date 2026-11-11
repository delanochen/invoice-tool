document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-download-all-attachments]");
  if (!button) return;
  const scope = button.closest(".attachment-download-scope") || document;
  const links = Array.from(scope.querySelectorAll("[data-attachment-download]"));
  // 逐个排队下载：一次性点击 N 个链接会让浏览器同时发起 N 条连接，
  // 国内窄带宽下会把整站拖慢，浏览器还可能拦截「多个文件下载」。
  const queue = links.map((link) => link.href);
  const step = (index) => {
    if (index >= queue.length) return;
    const download = document.createElement("a");
    download.href = queue[index];
    download.download = "";
    download.hidden = true;
    document.body.appendChild(download);
    download.click();
    download.remove();
    setTimeout(() => step(index + 1), 400);
  };
  step(0);
});
