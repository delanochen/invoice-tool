const mapElement = document.querySelector("#serviceOrderMap");
const searchInput = document.querySelector("#mapSearch");
const statusSelect = document.querySelector("#mapStatus");
const filterOptionContainers = document.querySelectorAll("[data-map-filter-options]");
const visibleCount = document.querySelector("#visibleOrderCount");
const progressText = document.querySelector("#geocodeProgress");
const unlocatedContainer = document.querySelector("#unlocatedOrders");
const retryButton = document.querySelector("#retryFailedGeocodes");
const mapConfig = window.serviceOrderMapConfig || {};
const t = (value) => window.uiTranslate ? window.uiTranslate(value) : value;
const buyersById = new Map((window.serviceOrderMapData || []).map((buyer) => [buyer.id, buyer]));
const markersByKey = new Map();
let serviceMap;
let activeInfoWindow;
let mapProjectionOverlay;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[character]);
}

function money(value) {
  return `$${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function filterText(value) {
  return String(value || "").trim();
}

function selectedFilterValues(field) {
  return new Set(
    Array.from(document.querySelectorAll(`[data-map-filter-options="${field}"] input:checked`))
      .map((input) => input.value)
  );
}

function renderMapFilterOptions() {
  filterOptionContainers.forEach((container) => {
    const field = container.dataset.mapFilterOptions;
    const values = Array.from(new Set(
      [...buyersById.values()].map((buyer) => filterText(buyer[field])).filter(Boolean)
    )).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
    container.replaceChildren();
    const details = container.closest("details");
    if (details) details.hidden = values.length === 0;
    values.forEach((value) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = value;
      const span = document.createElement("span");
      span.textContent = value;
      label.append(input, span);
      container.appendChild(label);
    });
  });
}

function buyerDetails(buyer) {
  const invoices = mapConfig.showInvoiceAmounts ? `
    <dt>${t("发票")}</dt><dd>${money(buyer.paid_invoice_amount)} / ${money(buyer.completed_invoice_amount)}</dd>
  ` : "";
  return `
    <div class="map-order-popup">
      <strong>${escapeHtml(buyer.name)}</strong>
      <span>${escapeHtml(buyer.detailed_address)}</span>
      <dl>
        <dt>${t("业主")}</dt><dd>${escapeHtml(buyer.owner || "-")}</dd>
        <dt>${t("联系人")}</dt><dd>${escapeHtml(buyer.contact_name || "-")}</dd>
        <dt>${t("联系方式")}</dt><dd>${escapeHtml(buyer.contact_details || "-")}</dd>
        <dt>${t("工单数")}</dt><dd>${escapeHtml(buyer.work_order_completed)} / ${escapeHtml(buyer.work_order_total)}</dd>
        ${invoices}
      </dl>
      <a href="${escapeHtml(buyer.detail_url)}">${t("工单查看")}</a>
    </div>
  `;
}

function buyerClusterDetails(buyers) {
  if (buyers.length === 1) return buyerDetails(buyers[0]);
  const items = buyers.map((buyer) => `
    <a class="map-cluster-buyer" href="${escapeHtml(buyer.detail_url)}">
      <strong>${escapeHtml(buyer.buyer_number)} · ${escapeHtml(buyer.name)}</strong>
      <span>${escapeHtml(buyer.detailed_address)}</span>
    </a>
  `).join("");
  return `
    <div class="map-order-popup map-cluster-popup">
      <strong>${buyers.length} ${t("个需方")}</strong>
      <span>${t("此区域有多个需方地址很近。")}</span>
      <div class="map-cluster-list">${items}</div>
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
  const selectedSites = selectedFilterValues("name");
  const selectedOwners = selectedFilterValues("owner");
  const haystack = [
    buyer.buyer_number, buyer.name, buyer.owner, buyer.contact_name, buyer.contact_details,
    buyer.detailed_address, buyer.equipment_manufacturer
  ].join(" ").toLocaleLowerCase();
  return (
    (!query || haystack.includes(query)) &&
    (!status || buyer.status === status) &&
    (!selectedSites.size || selectedSites.has(filterText(buyer.name))) &&
    (!selectedOwners.size || selectedOwners.has(filterText(buyer.owner)))
  );
}

function buyerLatLng(buyer) {
  return new google.maps.LatLng(Number(buyer.latitude), Number(buyer.longitude));
}

function groupVisibleBuyers(buyers) {
  const projection = mapProjectionOverlay?.getProjection();
  if (!projection) {
    return buyers.map((buyer) => ({
      key: String(buyer.id),
      buyers: [buyer],
      position: { lat: Number(buyer.latitude), lng: Number(buyer.longitude) }
    }));
  }
  const markerRadius = 11;
  const clusterRadius = 14;
  const overlapPadding = 4;
  const groups = [];
  buyers.forEach((buyer) => {
    const latLng = buyerLatLng(buyer);
    const point = projection.fromLatLngToDivPixel(latLng);
    let targetGroup = null;
    for (const group of groups) {
      const groupRadius = group.buyers.length > 1 ? clusterRadius : markerRadius;
      const dx = point.x - group.point.x;
      const dy = point.y - group.point.y;
      const distance = Math.sqrt((dx * dx) + (dy * dy));
      if (distance <= markerRadius + groupRadius + overlapPadding) {
        targetGroup = group;
        break;
      }
    }
    if (!targetGroup) {
      groups.push({ buyers: [], point: { x: point.x, y: point.y } });
      targetGroup = groups[groups.length - 1];
    }
    targetGroup.buyers.push(buyer);
    targetGroup.point.x = ((targetGroup.point.x * (targetGroup.buyers.length - 1)) + point.x) / targetGroup.buyers.length;
    targetGroup.point.y = ((targetGroup.point.y * (targetGroup.buyers.length - 1)) + point.y) / targetGroup.buyers.length;
  });
  return groups.map((group) => {
    const latitude = group.buyers.reduce((sum, buyer) => sum + Number(buyer.latitude), 0) / group.buyers.length;
    const longitude = group.buyers.reduce((sum, buyer) => sum + Number(buyer.longitude), 0) / group.buyers.length;
    const key = group.buyers.map((buyer) => buyer.id).sort((a, b) => Number(a) - Number(b)).join("-");
    return { key, buyers: group.buyers, position: { lat: latitude, lng: longitude } };
  });
}

function openBuyerInfo(buyers, marker) {
  if (activeInfoWindow) activeInfoWindow.close();
  activeInfoWindow = new google.maps.InfoWindow({ content: buyerClusterDetails(buyers) });
  activeInfoWindow.open({ map: serviceMap, anchor: marker });
}

function ensureMarker(group) {
  let marker = markersByKey.get(group.key);
  const hasActiveOrder = group.buyers.some((buyer) => buyer.status !== "completed");
  const color = hasActiveOrder ? "#0f766e" : "#667085";
  const isCluster = group.buyers.length > 1;
  const icon = {
    path: google.maps.SymbolPath.CIRCLE,
    scale: isCluster ? 14 : 11,
    fillColor: color,
    fillOpacity: 0.96,
    strokeColor: "#ffffff",
    strokeWeight: isCluster ? 3 : 2
  };
  const title = group.buyers.map((buyer) => buyer.name).join(", ");
  const label = isCluster ? {
    text: String(group.buyers.length),
    color: "#ffffff",
    fontSize: "14px",
    fontWeight: "700"
  } : null;
  if (!marker) {
    marker = new google.maps.Marker({ position: group.position, title, icon, label, zIndex: isCluster ? 20 : 10 });
    marker.addListener("mouseover", () => openBuyerInfo(group.buyers, marker));
    marker.addListener("click", () => openBuyerInfo(group.buyers, marker));
    markersByKey.set(group.key, marker);
  } else {
    marker.setPosition(group.position);
    marker.setTitle(title);
    marker.setIcon(icon);
    marker.setLabel(label);
    marker.setZIndex(isCluster ? 20 : 10);
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
    label: { text: "★", color: "#b42318", fontSize: "28px", fontWeight: "700" },
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
    empty.textContent = t("所有需方均已定位。");
    unlocatedContainer.appendChild(empty);
  } else {
    unlocated.forEach((buyer) => {
      const item = document.createElement("a");
      item.className = "unlocated-order";
      item.href = buyer.edit_url || buyer.detail_url;
      item.innerHTML = `<strong>${escapeHtml(buyer.buyer_number)} · ${escapeHtml(buyer.name)}</strong><span>${escapeHtml(buyer.detailed_address)}</span><small>${buyer.geocode_status === "failed" ? t("无法识别地址") : t("等待定位")}</small>`;
      unlocatedContainer.appendChild(item);
    });
  }
  retryButton.hidden = !unlocated.some((buyer) => buyer.geocode_status === "failed");
}

function renderMarkers({ fit = false } = {}) {
  if (activeInfoWindow) {
    activeInfoWindow.close();
    activeInfoWindow = null;
  }
  markersByKey.forEach((marker) => marker.setMap(null));
  const visibleBuyers = [...buyersById.values()].filter((buyer) => matchesFilters(buyer) && hasCoordinates(buyer));
  const groups = groupVisibleBuyers(visibleBuyers);
  const markers = groups.map((group) => {
    const marker = ensureMarker(group);
    marker.setMap(serviceMap);
    return marker;
  });
  visibleCount.textContent = String(visibleBuyers.length);
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
    if (result.buyer) {
      buyersById.set(result.buyer.id, result.buyer);
      renderMapFilterOptions();
    }
    renderMarkers({ fit: Boolean(result.buyer) });
    if (result.remaining > 0) {
      progressText.textContent = `${t("正在定位，剩余")} ${result.remaining}`;
      window.setTimeout(geocodePendingBuyers, 250);
    } else {
      progressText.textContent = "";
    }
  } catch (error) {
    progressText.textContent = t("定位服务暂时不可用，稍后打开页面会继续。");
  }
}

searchInput.addEventListener("input", () => renderMarkers({ fit: true }));
statusSelect.addEventListener("change", () => renderMarkers({ fit: true }));
filterOptionContainers.forEach((container) => {
  container.addEventListener("change", () => renderMarkers({ fit: true }));
});
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
  mapProjectionOverlay = new google.maps.OverlayView();
  mapProjectionOverlay.onAdd = function onAdd() {};
  mapProjectionOverlay.draw = function draw() {};
  mapProjectionOverlay.onRemove = function onRemove() {};
  mapProjectionOverlay.setMap(serviceMap);
  addHeadquartersMarker();
  renderMapFilterOptions();
  google.maps.event.addListenerOnce(serviceMap, "idle", () => renderMarkers({ fit: true }));
  serviceMap.addListener("idle", () => renderMarkers());
  geocodePendingBuyers();
};
