const registrationFields = document.querySelectorAll("[data-registration-empty]");

const clearUnexpectedAutofill = () => {
  registrationFields.forEach((field) => {
    if (!field.matches(":focus") && field.value) field.value = "";
  });
};

requestAnimationFrame(clearUnexpectedAutofill);
window.setTimeout(clearUnexpectedAutofill, 150);
