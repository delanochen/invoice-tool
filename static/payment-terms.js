function syncPaymentTermFields(form) {
  const monthly = form.querySelector("[data-payment-rule-type]")?.value === "monthly_cutoff";
  form.querySelectorAll("[data-fixed-days]").forEach((field) => { field.hidden = monthly; });
  form.querySelectorAll("[data-monthly-cutoff]").forEach((field) => { field.hidden = !monthly; });
}

document.querySelectorAll(".payment-term-fields").forEach((fields) => {
  const form = fields.closest("form");
  const select = fields.querySelector("[data-payment-rule-type]");
  select?.addEventListener("change", () => syncPaymentTermFields(form));
  syncPaymentTermFields(form);
});
