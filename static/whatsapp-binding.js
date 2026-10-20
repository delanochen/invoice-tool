(() => {
  const root = document.querySelector('[data-whatsapp-binding]');
  if (!root) return;
  const userId = root.dataset.userId;
  const status = root.querySelector('[data-whatsapp-status]');
  const generate = root.querySelector('[data-whatsapp-generate]');
  const unbind = root.querySelector('[data-whatsapp-unbind]');
  const codeBox = root.querySelector('[data-whatsapp-code-box]');
  const code = root.querySelector('[data-whatsapp-code]');

  async function jsonFetch(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  async function refresh() {
    try {
      const data = await jsonFetch(`/api/v1/users/${userId}/whatsapp`);
      if (data.bound) {
        status.textContent = `Bound ${data.phone_number || ''}`.trim();
        unbind.hidden = false;
        generate.textContent = 'Rebind';
      } else {
        status.textContent = 'Not bound';
        unbind.hidden = true;
        generate.textContent = 'Generate binding code';
      }
    } catch (_) { status.textContent = 'Unable to load'; }
  }
  generate.addEventListener('click', async () => {
    try {
      const data = await jsonFetch(`/api/v1/users/${userId}/whatsapp/binding-code`, {method: 'POST'});
      code.textContent = data.code;
      codeBox.hidden = false;
    } catch (_) { alert('Unable to generate WhatsApp binding code'); }
  });
  unbind.addEventListener('click', async () => {
    if (!confirm('Unbind this employee WhatsApp account?')) return;
    try {
      await jsonFetch(`/api/v1/users/${userId}/whatsapp/unbind`, {method: 'POST'});
      codeBox.hidden = true;
      await refresh();
    } catch (_) { alert('Unable to unbind WhatsApp'); }
  });
  refresh();
})();
