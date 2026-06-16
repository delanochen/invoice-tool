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
let serviceMap;
let activeInfoWindow;

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
    <dt>发票</dt><dd>${money(buyer.paid_invoice_amount)} / ${money(buyer.completed_invoice_amount)}</dd>
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

function openBuyerInfo(buyer, marker) {
  if (activeInfoWindow) activeInfoWindow.close();
  activeInfoWindow = new google.maps.InfoWindow({ content: buyerDetails(buyer) });
  activeInfoWindow.open({ map: serviceMap, anchor: marker });
}

function ensureMarker(buyer) {
  let marker = markersById.get(buyer.id);
  const icon = {
    path: google.maps.SymbolPath.CIRCLE,
    scale: 8,
    fillColor: buyer.status === "completed" ? "#667085" : "#0f766e",
    fillOpacity: 0.95,
    strokeColor: "#ffffff",
    strokeWeight: 2
  };
  const position = { lat: Number(buyer.latitude), lng: Number(buyer.longitude) };
  if (!marker) {
    marker = new google.maps.Marker({ position, title: buyer.name, icon });
    marker.addListener("mouseover", () => openBuyerInfo(buyersById.get(buyer.id), marker));
    marker.addListener("click", () => openBuyerInfo(buyersById.get(buyer.id), marker));
    markersById.set(buyer.id, marker);
  } else {
    marker.setPosition(position);
    marker.setIcon(icon);
  }
  return marker;
}

function addHeadquartersMarker() {
  const headquarters = mapConfig.headquarters;
  if (!headquarters) return;
  const marker = new google.maps.Marker({
    map: serviceMap,
    position: { lat: headquarters.latitude, lng: headquarters.longitude },
    title: headquarters.name,
    label: { text: "★", color: "#b42318", fontSize: "20px", fontWeight: "700" },
    icon: {
      path: google.maps.SymbolPath.CIRCLE,
      scale: 1,
      fillOpacity: 0,
      strokeOpacity: 0
    }
  });
  const info = new google.maps.InfoWindow({
    content: `<strong>${escapeHtml(headquarters.name)}</strong><br>${escapeHtml(headquarters.address)}`
  });
  marker.addListener("mouseover", () => info.open({ map: serviceMap, anchor: marker }));
  marker.addListener("mouseout", () => info.close());
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
  markersById.forEach((marker) => marker.setMap(null));
  const markers = [];
  [...buyersById.values()]
    .filter((buyer) => matchesFilters(buyer) && hasCoordinates(buyer))
    .forEach((buyer) => {
      const marker = ensureMarker(buyer);
      marker.setMap(serviceMap);
      markers.push(marker);
    });
  visibleCount.textContent = String(markers.length);
  renderUnlocatedBuyers();
  if (fit && markers.length) {
    const bounds = new google.maps.LatLngBounds();
    markers.forEach((marker) => bounds.extend(marker.getPosition()));
    serviceMap.fitBounds(bounds, 48);
    if (markers.length === 1) serviceMap.setZoom(12);
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
    } else {
      progressText.textContent = "";
    }
  } catch (error) {
    progressText.textContent = "定位服务暂时不可用，稍后打开页面会继续。";
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
      if (buyer.geocode_status === "failed") {
        buyer.latitude = null;
        buyer.longitude = null;
        buyer.geocode_status = "pending";
      }
    });
    await geocodePendingBuyers();
  } finally {
    retryButton.disabled = false;
  }
});

window.initServiceOrderGoogleMap = function initServiceOrderGoogleMap() {
  serviceMap = new google.maps.Map(mapElement, {
    center: { lat: 39.5, lng: -98.35 },
    zoom: 4,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true
  });
  addHeadquartersMarker();
  renderMarkers({ fit: true });
  geocodePendingBuyers();
};
