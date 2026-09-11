(() => {
  const groups = [...document.querySelectorAll('[data-report-multi]')];
  groups.forEach(group => {
    const update = () => {
      const labels = [...group.querySelectorAll('input:checked')].map(input => input.nextElementSibling.textContent.trim());
      const summary = group.querySelector('summary');
      summary.textContent = labels.length ? labels.join('、') : '全部';
      summary.title = summary.textContent;
    };
    group.addEventListener('change', update);
    group.querySelector('[data-clear-multi]').addEventListener('click', () => {
      group.querySelectorAll('input').forEach(input => { input.checked = false; }); update();
    });
    group.addEventListener('focusout', () => setTimeout(() => { if (!group.contains(document.activeElement)) group.open = false; }, 0));
    group.addEventListener('keydown', event => { if (event.key === 'Escape') { group.open = false; group.querySelector('summary').focus(); } });
    group.addEventListener('toggle', () => { if (group.open) groups.forEach(other => { if (other !== group) other.open = false; }); });
    update();
  });
  document.addEventListener('pointerdown', event => groups.forEach(group => { if (!group.contains(event.target)) group.open = false; }));
  window.addEventListener('blur', () => groups.forEach(group => { group.open = false; }));
})();
