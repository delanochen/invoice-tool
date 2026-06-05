const multiSelects = Array.from(document.querySelectorAll(".multi-select"));

function closeOtherMultiSelects(current) {
  multiSelects.forEach((item) => {
    if (item !== current) {
      item.open = false;
    }
  });
}

multiSelects.forEach((item) => {
  item.addEventListener("toggle", () => {
    if (item.open) {
      closeOtherMultiSelects(item);
    }
  });

  item.addEventListener("focusout", () => {
    window.setTimeout(() => {
      if (!item.contains(document.activeElement)) {
        item.open = false;
      }
    }, 0);
  });
});

document.addEventListener("click", (event) => {
  multiSelects.forEach((item) => {
    if (!item.contains(event.target)) {
      item.open = false;
    }
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    multiSelects.forEach((item) => {
      item.open = false;
    });
  }
});
