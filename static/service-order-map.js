const mapElement = document.querySelector("#serviceOrderMap");
const searchInput = document.querySelector("#mapSearch");
const filterOptionContainers = document.querySelectorAll("[data-map-filter-options]");
const visibleCount = document.querySelector("#visibleOrderCount");
const progressText = document.querySelector("#geocodeProgress");
const legendContainer = document.querySelector("#mapLegend");
const unlocatedPanel = document.querySelector(".map-unlocated-panel");
const unlocatedContainer = document.querySelector("#unlocatedOrders");
const retryButton = document.querySelector("#retryFailedGeocodes");
const mapConfig = window.serviceOrderMapConfig || {};
const t = (value) => window.uiTranslate ? window.uiTranslate(value) : value;
const buyersById = new Map((window.serviceOrderMapData || []).map((buyer) => [buyer.id, buyer]));
const markersById = new Map();
const inspectionStatusOptions = [
  { value: "overdue", label: "超期" },
  { value: "warning", label: "预警到期" },
  { value: "fresh", label: "未超期" },
  { value: "none", label: "无工单" }
];
const workOrderStatusOptions = [
  { value: "active", label: "有进行中工单" },
  { value: "completed", label: "工单全部完成" }
];
const serviceMap = L.map(mapElement, { zoomControl: true }).setView([39.5, -98.35], 4);
const markerLayer = L.layerGroup().addTo(serviceMap);
const labelPlacements = [
  { name: "top", dx: -54, dy: -34, width: 108, height: 24 },
  { name: "bottom", dx: -54, dy: 12, width: 108, height: 24 },
  { name: "right", dx: 12, dy: -12, width: 108, height: 24 },
  { name: "left", dx: -120, dy: -12, width: 108, height: 24 },
  { name: "top-right", dx: 10, dy: -34, width: 108, height: 24 },
  { name: "top-left", dx: -118, dy: -34, width: 108, height: 24 },
  { name: "bottom-right", dx: 10, dy: 12, width: 108, height: 24 },
  { name: "bottom-left", dx: -118, dy: 12, width: 108, height: 24 }
];

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

function buyerDirectionsUrl(buyer) {
  const origin = mapConfig.routeOriginAddress || mapConfig.companyAddress || mapConfig.headquarters?.address || "";
  const destination = hasCoordinates(buyer)
    ? `${Number(buyer.latitude)},${Number(buyer.longitude)}`
    : buyer.detailed_address;
  return `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(destination || "")}&travelmode=driving`;
}

function buyerAddressLink(buyer) {
  return `
    <a class="map-directions-link" href="${escapeHtml(buyerDirectionsUrl(buyer))}" target="_blank" rel="noopener" title="${t("打开 Google 地图导航")}">
      ${escapeHtml(buyer.detailed_address || "-")}
    </a>
  `;
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

function updateMapFilterSummary(container) {
  const details = container.closest("details");
  const summary = details?.querySelector("summary");
  if (!summary) return;
  if (!summary.dataset.label) summary.dataset.label = summary.textContent.trim();
  const selectedLabels = Array.from(container.querySelectorAll("input:checked"))
    .map((input) => input.nextElementSibling?.textContent || input.value);
  if (!selectedLabels.length) {
    summary.textContent = summary.dataset.label;
    summary.classList.remove("has-active-filter");
  } else {
    summary.textContent = selectedLabels.length <= 2
      ? `${summary.dataset.label}：${selectedLabels.join("、")}`
      : `${summary.dataset.label}：${selectedLabels.length}`;
    summary.classList.add("has-active-filter");
  }
}

function updateMapFilterSummaries() {
  filterOptionContainers.forEach(updateMapFilterSummary);
}

function renderMapFilterOptions() {
  filterOptionContainers.forEach((container) => {
    const field = container.dataset.mapFilterOptions;
    const values = field === "status"
      ? workOrderStatusOptions.filter((option) => [...buyersById.values()].some((buyer) => buyer.status === option.value))
      : field === "inspection_status"
        ? inspectionStatusOptions.filter((option) => [...buyersById.values()].some((buyer) => (buyer.inspection_status || "none") === option.value))
        : Array.from(new Set(
        [...buyersById.values()].map((buyer) => filterText(buyer[field])).filter(Boolean)
      )).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" })).map((value) => ({ value, label: value }));
    container.replaceChildren();
    const details = container.closest("details");
    if (details) {
      details.hidden = values.length === 0;
      const summary = details.querySelector("summary");
      if (summary && !summary.dataset.label) summary.dataset.label = summary.textContent.trim();
    }
    values.forEach((option) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = option.value;
      const span = document.createElement("span");
      span.textContent = t(option.label);
      label.append(input, span);
      container.appendChild(label);
    });
    updateMapFilterSummary(container);
  });
}

function setupMapFilterAutoClose() {
  document.addEventListener("pointerdown", (event) => {
    document.querySelectorAll(".map-filter-select[open]").forEach((details) => {
      if (!details.contains(event.target)) details.removeAttribute("open");
    });
  });
  document.querySelectorAll(".map-filter-select").forEach((details) => {
    details.addEventListener("focusout", () => {
      window.setTimeout(() => {
        if (!details.contains(document.activeElement)) details.removeAttribute("open");
      }, 0);
    });
  });
}

function buyerDetails(buyer) {
  const invoices = mapConfig.showInvoiceAmounts ? `
    <dt>${t("发票")}</dt>
    <dd>${money(buyer.paid_invoice_amount)} / ${money(buyer.completed_invoice_amount)}</dd>
  ` : "";
  return `
    <div class="map-order-popup">
      <strong>${escapeHtml(buyer.name)}</strong>
      <span>${buyerAddressLink(buyer)}</span>
      <dl>
        <dt>${t("联系人")}</dt><dd>${escapeHtml(buyer.contact_name || "-")}</dd>
        <dt>${t("联系方式")}</dt><dd>${escapeHtml(buyer.contact_details || "-")}</dd>
        <dt>${t("工单数")}</dt><dd>${escapeHtml(buyer.work_order_completed)} / ${escapeHtml(buyer.work_order_total)}</dd>
        ${invoices}
      </dl>
      <a href="${escapeHtml(buyer.detail_url)}">${t("工单查看")}</a>
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
  const selectedStatuses = selectedFilterValues("status");
  const selectedSites = selectedFilterValues("name");
  const selectedOwners = selectedFilterValues("owner");
  const selectedInspectionStatuses = selectedFilterValues("inspection_status");
  const haystack = [
    buyer.buyer_number, buyer.name, buyer.owner, buyer.contact_name, buyer.contact_details, buyer.email,
    buyer.detailed_address, buyer.equipment_manufacturer
  ].join(" ").toLocaleLowerCase();
  return (
    (!query || haystack.includes(query)) &&
    (!selectedStatuses.size || selectedStatuses.has(buyer.status)) &&
    (!selectedSites.size || selectedSites.has(filterText(buyer.name))) &&
    (!selectedOwners.size || selectedOwners.has(filterText(buyer.owner))) &&
    (!selectedInspectionStatuses.size || selectedInspectionStatuses.has(buyer.inspection_status || "none"))
  );
}

function renderMapLegend(visibleBuyers) {
  if (!legendContainer) return;
  const counts = { overdue: 0, warning: 0, fresh: 0, none: 0, unlocated: 0 };
  visibleBuyers.forEach((buyer) => {
    if (!hasCoordinates(buyer)) {
      counts.unlocated += 1;
      return;
    }
    counts[buyer.inspection_status || "none"] = (counts[buyer.inspection_status || "none"] || 0) + 1;
  });
  const items = [
    { key: "overdue", label: "超期", className: "inspection-overdue", always: true },
    { key: "warning", label: "预警", className: "inspection-warning", always: true },
    { key: "fresh", label: "正常", className: "inspection-fresh", always: true },
    { key: "none", label: "无工单", className: "inspection-none", always: false },
    { key: "unlocated", label: "未定位", className: "inspection-unlocated", always: false }
  ].filter((item) => item.always || counts[item.key] > 0);
  legendContainer.innerHTML = items.map((item) => `
    <span><i class="legend-dot ${item.className}"></i>${t(item.label)}：${counts[item.key]}</span>
  `).join("");
}

function rectOverlapArea(a, b) {
  const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  return x * y;
}

function chooseLabelPlacement(buyer, occupiedRects) {
  const point = serviceMap.latLngToContainerPoint([Number(buyer.latitude), Number(buyer.longitude)]);
  let best = labelPlacements[0];
  let bestScore = Number.POSITIVE_INFINITY;
  labelPlacements.forEach((placement) => {
    const rect = {
      left: point.x + placement.dx,
      top: point.y + placement.dy,
      right: point.x + placement.dx + placement.width,
      bottom: point.y + placement.dy + placement.height
    };
    const overflow =
      Math.max(0, -rect.left) + Math.max(0, -rect.top) +
      Math.max(0, rect.right - mapElement.clientWidth) +
      Math.max(0, rect.bottom - mapElement.clientHeight);
    const overlap = occupiedRects.reduce((sum, occupied) => sum + rectOverlapArea(rect, occupied), 0);
    const score = overlap + overflow * 50;
    if (score < bestScore) {
      best = placement;
      bestScore = score;
    }
  });
  occupiedRects.push({
    left: point.x + best.dx,
    top: point.y + best.dy,
    right: point.x + best.dx + best.width,
    bottom: point.y + best.dy + best.height
  });
  return best.name;
}

function siteMarkerHtml(buyer, placement) {
  const statusClass = `inspection-${buyer.inspection_status || "none"}`;
  return `
    <span class="map-site-pin ${statusClass}" aria-hidden="true"></span>
    <span class="map-site-label label-${placement}">${escapeHtml(buyer.name)}</span>
  `;
}

function siteMarkerIcon(buyer, placement) {
  return L.divIcon({
    className: "map-site-marker",
    html: siteMarkerHtml(buyer, placement),
    iconSize: [1, 1],
    iconAnchor: [0, 0]
  });
}

function ensureMarker(buyer, position, placement) {
  let marker = markersById.get(buyer.id);
  const zIndexOffset = buyer.inspection_status === "overdue" ? 60 : buyer.inspection_status === "warning" ? 45 : buyer.inspection_status === "fresh" ? 30 : 10;
  if (!marker) {
    marker = L.marker(position, { icon: siteMarkerIcon(buyer, placement), title: buyer.name, zIndexOffset });
    marker.bindPopup(buyerDetails(buyer), { maxWidth: 340 });
    markersById.set(buyer.id, marker);
  } else {
    marker.setLatLng(position);
    marker.setIcon(siteMarkerIcon(buyer, placement));
    marker.options.title = buyer.name;
    marker.setZIndexOffset(zIndexOffset);
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
  if (unlocatedPanel) unlocatedPanel.hidden = unlocated.length === 0;
  if (!unlocated.length) {
    retryButton.hidden = true;
    return;
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
  markerLayer.clearLayers();
  const markers = [];
  const occupiedRects = [];
  const visibleBuyers = [...buyersById.values()].filter((buyer) => matchesFilters(buyer));
  visibleBuyers
    .filter((buyer) => hasCoordinates(buyer))
    .sort((a, b) => Number(a.latitude) - Number(b.latitude) || Number(a.longitude) - Number(b.longitude))
    .forEach((buyer) => {
      const placement = chooseLabelPlacement(buyer, occupiedRects);
      const marker = ensureMarker(buyer, [Number(buyer.latitude), Number(buyer.longitude)], placement);
      marker.addTo(markerLayer);
      markers.push(marker);
    });
  visibleCount.textContent = String(markers.length);
  renderMapLegend(visibleBuyers);
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
    if (result.buyer) {
      buyersById.set(result.buyer.id, result.buyer);
      renderMapFilterOptions();
    }
    renderMarkers({ fit: Boolean(result.buyer) });
    if (result.remaining > 0) {
      progressText.textContent = `${t("正在定位，剩余")} ${result.remaining}`;
      window.setTimeout(geocodePendingBuyers, 250);
    } else progressText.textContent = "";
  } catch (error) {
    progressText.textContent = t("定位服务暂时不可用，稍后打开页面会继续。");
  }
}

searchInput.addEventListener("input", () => renderMarkers({ fit: true }));
filterOptionContainers.forEach((container) => {
  container.addEventListener("change", () => {
    updateMapFilterSummaries();
    renderMarkers({ fit: true });
  });
});
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
renderMapFilterOptions();
setupMapFilterAutoClose();
renderMarkers({ fit: true });
serviceMap.on("zoomend moveend", () => renderMarkers());
window.setTimeout(() => serviceMap.invalidateSize({ pan: false }), 150);
geocodePendingBuyers();
