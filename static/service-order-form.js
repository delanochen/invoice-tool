const buyerSelect = document.querySelector("#buyerSelect");
const siteAddress = document.querySelector("#siteAddress");
const fields = {
  contact: document.querySelector("#buyerContact"),
  contactDetails: document.querySelector("#buyerContactDetails"),
  email: document.querySelector("#buyerEmail"),
  owner: document.querySelector("#buyerOwner"),
  size: document.querySelector("#buyerSize"),
  country: document.querySelector("#buyerCountry"),
  manufacturer: document.querySelector("#buyerManufacturer")
};

function fillBuyerDetails(replaceAddress = false) {
  const option = buyerSelect.selectedOptions[0];
  fields.contact.value = option?.dataset.contact || "";
  fields.contactDetails.value = option?.dataset.contactDetails || "";
  fields.email.value = option?.dataset.email || "";
  fields.owner.value = option?.dataset.owner || "";
  fields.size.value = option?.dataset.size || "";
  fields.country.value = option?.dataset.country || "";
  fields.manufacturer.value = option?.dataset.manufacturer || "";
  if (replaceAddress || !siteAddress.value.trim()) {
    siteAddress.value = option?.dataset.address || "";
  }
}

buyerSelect.addEventListener("change", () => fillBuyerDetails(true));
fillBuyerDetails(false);
