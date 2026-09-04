(() => {
  const sidebar = document.querySelector('.sidebar');
  const toggle = document.querySelector('.mobile-menu-toggle');
  const setOpen = (open) => {
    sidebar?.classList.toggle('menu-open', open);
    toggle?.setAttribute('aria-expanded', String(open));
  };
  toggle?.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && toggle?.getAttribute('aria-expanded') === 'true') {
      setOpen(false);
      toggle.focus();
    }
  });
  sidebar?.querySelectorAll('nav a').forEach((link) => link.addEventListener('click', () => setOpen(false)));

  // A restored browser snapshot may predate an approval. Fetch current state,
  // but keep unsaved form input when returning to an unfinished edit.
  let hasUnsavedInput = false;
  document.querySelector('main')?.addEventListener('input', (event) => {
    if (event.target.closest('form[method="post"]')) hasUnsavedInput = true;
  });
  document.addEventListener('submit', () => { hasUnsavedInput = false; });
  window.addEventListener('pageshow', (event) => {
    setOpen(false);
    if (event.persisted && !hasUnsavedInput) window.location.reload();
  });
})();
