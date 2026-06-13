const mapElement = document.querySelector("#serviceOrderMap");
const searchInput = document.querySelector("#mapSearch");
const statusSelect = document.querySelector("#mapStatus");
const visibleCount = document.querySelector("#visibleOrderCount");
const progressText = document.querySelector("#geocodeProgress");
const unlocatedContainer = document.querySelector("#unlocatedOrders");
const retryButton = document.querySelector("#retryFailedGeocodes");
const mapConfig = window.serviceOrderMapConfig || {};
const buyersById = new Map((window.serviceOrderMapData || []).map((buyer) => [buyer.id, buyer]));
const markersById = new Map();
const serviceMap = L.map(mapElement, { zoomControl: true }).setView([39.5, -98.35], 4);
const markerLayer = L.layerGroup().addTo(serviceMap);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(serviceMap);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[character]);
}

function money(value) {
  return `$${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function buyerDetails(buyer) {
  const invoices = mapConfig.showInvoiceAmounts ? `
    <dt>发票</dt>
    <dd>${money(buyer.paid_invoice_amount)} / ${money(buyer.completed_invoice_amount)}</dd>
  ` : "";
  return `
    <div class="map-order-popup">
      <strong>${escapeHtml(buyer.name)}</strong>
      <span>${escapeHtml(buyer.detailed_address)}</span>
      <dl>
        <dt>联系人</dt><dd>${escapeHtml(buyer.contact_name || "-")}</dd>
        <dt>联系方式</dt><dd>${escapeHtml(buyer.contact_details || "-")}</dd>
        <dt>工单数</dt><dd>${escapeHtml(buyer.work_order_completed)} / ${escapeHtml(buyer.work_order_total)}</dd>
        ${invoices}
      </dl>
      <a href="${escapeHtml(buyer.detail_url)}">工单查看</a>
    </div>
  `;
}

function hasCoordinates(buyer) {
  if (
    buyer.latitude === null || buyer.latitude === undefined || buyer.latitude === "" ||
    buyer.longitude === null || buyer.longitude === undefined || buyer.longitude === ""
  ) {
    return false;
  }
  const latitude = Number(buyer.latitude);
  const longitude = Number(buyer.longitude);
  return (
    Number.isFinite(latitude) && Number.isFinite(longitude) &&
    latitude >= -90 && latitude <= 90 &&
    longitude >= -180 && longitude <= 180
  );
}

function matchesFilters(buyer) {
  const query = searchInput.value.trim().toLocaleLowerCase();
  const status = statusSelect.value;
  const haystack = [
    buyer.buyer_number, buyer.name, buyer.contact_name, buyer.contact_details,
    buyer.detailed_address, buyer.equipment_manufacturer
  ].join(" ").toLocaleLowerCase();
  return (!query || haystack.includes(query)) && (!status || buyer.status === status);
}

function ensureMarker(buyer, position) {
  let marker = markersById.get(buyer.id);
  const color = buyer.status === "completed" ? "#667085" : "#0f766e";
  if (!marker) {
    marker = L.circleMarker(position, {
      radius: 8, color: "#ffffff", weight: 2, fillColor: color, fillOpacity: 0.95
    });
    marker.bindTooltip(buyerDetails(buyer), { sticky: true, direction: "top", opacity: 0.98 });
    marker.bindPopup(buyerDetails(buyer), { maxWidth: 340 });
    markersById.set(buyer.id, marker);
  } else {
    marker.setLatLng(position);
    marker.setStyle({ fillColor: color });
    marker.setTooltipContent(buyerDetails(buyer));
    marker.setPopupContent(buyerDetails(buyer));
  }
  return marker;
}

function addHeadquartersMarker() {
  const headquarters = mapConfig.headquarters;
  if (!headquarters) return;
  const icon = L.divIcon({
    className: "headquarters-star",
    html: '<span aria-hidden="true">★</span>',
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
  L.marker([headquarters.latitude, headquarters.longitude], { icon, title: headquarters.name })
    .bindTooltip(`<strong>${escapeHtml(headquarters.name)}</strong><br>${escapeHtml(headquarters.address)}`)
    .addTo(serviceMap);
}

function renderUnlocatedBuyers() {
  const unlocated = [...buyersById.values()].filter((buyer) => !hasCoordinates(buyer));
  unlocatedContainer.replaceChildren();
  if (!unlocated.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "所有需方均已定位。";
    unlocatedContainer.appendChild(empty);
  } else {
    unlocated.forEach((buyer) => {
      const item = document.createElement("a");
      item.className = "unlocated-order";
      item.href = buyer.detail_url;
      item.innerHTML = `<strong>${escapeHtml(buyer.name)}</strong><span>${escapeHtml(buyer.detailed_address)}</span><small>${buyer.geocode_status === "failed" ? "无法识别地址" : "等待定位"}</small>`;
      unlocatedContainer.appendChild(item);
    });
  }
  retryButton.hidden = !unlocated.some((buyer) => buyer.geocode_status === "failed");
}

function renderMarkers({ fit = false } = {}) {
  markerLayer.clearLayers();
  const markers = [];
  [...buyersById.values()]
    .filter((buyer) => matchesFilters(buyer) && hasCoordinates(buyer))
    .forEach((buyer) => {
      const marker = ensureMarker(buyer, [Number(buyer.latitude), Number(buyer.longitude)]);
      marker.addTo(markerLayer);
      markers.push(marker);
    });
  visibleCount.textContent = String(markers.length);
  renderUnlocatedBuyers();
  if (fit && markers.length) {
    if (markers.length === 1) serviceMap.setView(markers[0].getLatLng(), 11);
    else serviceMap.fitBounds(L.featureGroup(markers).getBounds().pad(0.15), { maxZoom: 13 });
  }
}

async function geocodePendingBuyers() {
  if (!mapConfig.geocodingEnabled) return;
  try {
    const response = await fetch(mapConfig.geocodeNextUrl, {
      method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" }
    });
    if (!response.ok) throw new Error("geocode request failed");
    const result = await response.json();
    if (result.buyer) buyersById.set(result.buyer.id, result.buyer);
    renderMarkers({ fit: Boolean(result.buyer) });
    if (result.remaining > 0) {
      progressText.textContent = `正在定位，剩余 ${result.remaining} 个`;
      window.setTimeout(geocodePendingBuyers, 250);
    } else progressText.textContent = "";
  } catch (error) {
    progressText.textContent = "定位服务暂时不可用，稍后打开页面会继续";
  }
}

searchInput.addEventListener("input", () => renderMarkers({ fit: true }));
statusSelect.addEventListener("change", () => renderMarkers({ fit: true }));
retryButton.addEventListener("click", async () => {
  retryButton.disabled = true;
  try {
    const response = await fetch(mapConfig.retryFailedUrl, {
      method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" }
    });
    if (!response.ok) throw new Error("retry request failed");
    buyersById.forEach((buyer) => {
      if (buyer.geocode_status === "failed") buyer.geocode_status = "pending";
    });
    await geocodePendingBuyers();
  } finally {
    retryButton.disabled = false;
  }
});

addHeadquartersMarker();
renderMarkers({ fit: true });
window.setTimeout(() => serviceMap.invalidateSize({ pan: false }), 150);
geocodePendingBuyers();
