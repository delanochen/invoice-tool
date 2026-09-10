(() => {
  for (const popup of document.querySelectorAll('.expense-source-popover')) {
    const content = popup.querySelector('.expense-source-content');
    const place = () => {
      if (!popup.open) return;
      const anchor = popup.querySelector('summary').getBoundingClientRect();
      content.style.left = Math.max(8, Math.min(anchor.left, innerWidth - content.offsetWidth - 8)) + 'px';
      content.style.top = Math.max(8, Math.min(anchor.bottom + 4, innerHeight - content.offsetHeight - 8)) + 'px';
    };
    popup.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') { popup.open = true; place(); } });
    popup.addEventListener('pointerleave', () => { if (!popup.contains(document.activeElement)) popup.open = false; });
    popup.addEventListener('focusin', () => { popup.open = true; place(); });
    popup.addEventListener('focusout', event => { if (!popup.contains(event.relatedTarget)) popup.open = false; });
    popup.addEventListener('toggle', place);
    popup.addEventListener('keydown', event => { if (event.key === 'Escape') popup.open = false; });
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
  }
})();
