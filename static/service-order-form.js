const buyerSelect = document.querySelector("#buyerSelect");
const siteAddress = document.querySelector("#siteAddress");
const fields = {
  contact: document.querySelector("#buyerContact"),
  contactDetails: document.querySelector("#buyerContactDetails"),
  owner: document.querySelector("#buyerOwner"),
  country: document.querySelector("#buyerCountry"),
  manufacturer: document.querySelector("#buyerManufacturer")
};

function fillBuyerDetails(replaceAddress = false) {
  const option = buyerSelect.selectedOptions[0];
  fields.contact.value = option?.dataset.contact || "";
  fields.contactDetails.value = option?.dataset.contactDetails || "";
  fields.owner.value = option?.dataset.owner || "";
  fields.country.value = option?.dataset.country || "";
  fields.manufacturer.value = option?.dataset.manufacturer || "";
  if (replaceAddress || !siteAddress.value.trim()) {
    siteAddress.value = option?.dataset.address || "";
  }
}

buyerSelect.addEventListener("change", () => fillBuyerDetails(true));
fillBuyerDetails(false);
