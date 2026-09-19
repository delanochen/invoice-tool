document.querySelectorAll("[data-dialog-open]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = document.getElementById(button.dataset.dialogOpen);
    if (dialog) dialog.showModal();
  });
});

document.querySelectorAll("[data-dialog-close]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = button.closest("dialog");
    if (dialog) dialog.close();
  });
});

document.querySelectorAll(".modal-dialog").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    // v0.1.247: only close on genuine backdrop clicks. With native <dialog>,
    // clicks anywhere on the dialog's own padding/gaps ALSO report the dialog
    // as target, which kept closing the edit-user form when users clicked
    // blank areas inside it. A backdrop click lands OUTSIDE the dialog box,
    // so compare coordinates against the content rect.
    const rect = dialog.getBoundingClientRect();
    const insideContent =
      event.clientX >= rect.left && event.clientX <= rect.right &&
      event.clientY >= rect.top && event.clientY <= rect.bottom;
    if (!insideContent) dialog.close();
  });
});

document.querySelectorAll(".modal-dialog form").forEach((form) => {
  form.addEventListener("submit", () => {
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
    });
  });
});
