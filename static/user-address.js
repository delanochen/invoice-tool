const externalUserRoles = new Set(["external_manager", "external_employee"]);

document.querySelectorAll("form").forEach((form) => {
  const addressField = form.querySelector("[data-user-address]");
  if (!addressField) return;
  const roleField = form.querySelector('select[name="role"], input[name="role"]');
  const syncRequired = () => {
    const role = roleField?.value || "employee";
    const required = !externalUserRoles.has(role);
    addressField.required = required;
    addressField.closest("label")?.classList.toggle("required-field", required);
  };
  roleField?.addEventListener("change", syncRequired);
  syncRequired();
});
