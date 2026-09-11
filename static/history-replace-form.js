(() => {
  'use strict';
  document.addEventListener('submit', async event => {
    const form = event.target.closest?.('form[data-history-replace]');
    if (!form || event.defaultPrevented) return;
    event.preventDefault();
    const submitter = event.submitter;
    if (submitter) submitter.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: (form.method || 'POST').toUpperCase(),
        body: new FormData(form),
        credentials: 'same-origin',
        headers: {'X-History-Replace': '1', 'Accept': 'application/json'}
      });
      const result = await response.json();
      if (!response.ok || !result.redirect) throw new Error(result.message || '操作未完成，请重试。');
      document.dispatchEvent(new Event('workspace:saved'));
      document.dispatchEvent(new Event('workspace:navigate'));
      window.location.replace(result.redirect);
    } catch (error) {
      document.dispatchEvent(new Event('workspace:save-failed'));
      if (submitter) submitter.disabled = false;
      window.alert(error.message || '操作未完成，请重试。');
    }
  });
})();
