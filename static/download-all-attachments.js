document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-download-all-attachments]");
  if (!button) return;
  const scope = button.closest(".attachment-download-scope") || document;
  const links = Array.from(scope.querySelectorAll("[data-attachment-download]"));
  links.forEach((link) => {
    const download = document.createElement("a");
    download.href = link.href;
    download.download = "";
    download.hidden = true;
    document.body.appendChild(download);
    download.click();
    download.remove();
  });
});
