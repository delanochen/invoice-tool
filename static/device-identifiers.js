(() => {
  const selector = '#positionNumber, #containerNumber, input[name="position_number"], input[name="container_number"]';
  const normalize = input => {
    const value = input.value.trim().toUpperCase();
    if (value !== input.value) input.value = value;
  };
  for (const input of document.querySelectorAll(selector)) {
    input.autocapitalize = 'characters';
    input.spellcheck = false;
    normalize(input);
    input.addEventListener('input', event => { if (!event.isComposing) normalize(input); });
    input.addEventListener('compositionend', () => normalize(input));
    input.addEventListener('blur', () => normalize(input));
  }
})();
