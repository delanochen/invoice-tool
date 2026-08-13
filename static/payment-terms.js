function syncPaymentTermFields(form) {
  const monthly = form.querySelector("[data-payment-rule-type]")?.value === "monthly_cutoff";
  const setEnabled = (field, enabled) => {
    field.classList.toggle("is-disabled", !enabled);
    field.classList.toggle("required-field", enabled);
    field.setAttribute("aria-disabled", enabled ? "false" : "true");
    field.querySelectorAll("input, select, textarea").forEach((control) => {
      control.disabled = !enabled;
      control.required = enabled;
    });
  };
  form.querySelectorAll("[data-fixed-days]").forEach((field) => setEnabled(field, !monthly));
  form.querySelectorAll("[data-monthly-cutoff]").forEach((field) => setEnabled(field, monthly));
}

document.querySelectorAll(".payment-term-fields").forEach((fields) => {
  const form = fields.closest("form");
  const select = fields.querySelector("[data-payment-rule-type]");
  select?.addEventListener("change", () => syncPaymentTermFields(form));
  syncPaymentTermFields(form);
});
