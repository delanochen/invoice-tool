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
let serviceMap;
let activeInfoWindow;
let mapProjectionOverlay;
let SiteMarkerOverlay;

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
  const lastInspection = buyer.last_actual_date
    ? `${escapeHtml(buyer.last_actual_date)} · ${escapeHtml(buyer.days_since_last_actual)} ${t("天前")}`
    : (Number(buyer.work_order_total || 0) > 0 ? t("无实际日期") : t("无工单"));
  return `
    <div class="map-order-popup">
      <strong>${escapeHtml(buyer.name)}</strong>
      <span>${buyerAddressLink(buyer)}</span>
      <dl>
        <dt>${t("业主")}</dt><dd>${escapeHtml(buyer.owner || "-")}</dd>
        <dt>${t("联系人")}</dt><dd>${escapeHtml(buyer.contact_name || "-")}</dd>
        <dt>${t("联系方式")}</dt><dd>${escapeHtml(buyer.contact_details || "-")}</dd>
        <dt>${t("电子邮箱地址")}</dt><dd>${escapeHtml(buyer.email || "-")}</dd>
        <dt>${t("工单数")}</dt><dd>${escapeHtml(buyer.work_order_completed)} / ${escapeHtml(buyer.work_order_total)}</dd>
        <dt>${t("最近实际日期")}</dt><dd>${lastInspection}</dd>
        <dt>${t("预警到期")}</dt><dd>${escapeHtml(buyer.inspection_warning_days)} ${t("天")}</dd>
        <dt>${t("巡检周期")}</dt><dd>${escapeHtml(buyer.inspection_cycle_days)} ${t("天")}</dd>
        ${invoices}
      </dl>
      <a href="${escapeHtml(buyer.detail_url)}">${t("工单查看")}</a>
    </div>
  `;
}

function buyerClusterDetails(buyers) {
  if (buyers.length === 1) return buyerDetails(buyers[0]);
  const items = buyers.map((buyer) => `
    <div class="map-cluster-buyer">
      <strong><a href="${escapeHtml(buyer.detail_url)}">${escapeHtml(buyer.buyer_number)} · ${escapeHtml(buyer.name)}</a></strong>
      <span>${buyerAddressLink(buyer)}</span>
    </div>
  `).join("");
  return `
    <div class="map-order-popup map-cluster-popup">
      <strong>${buyers.length} ${t("个站点")}</strong>
      <span>${t("此区域有多个站点地址很近。")}</span>
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
    buyer.buyer_number, buyer.name, buyer.owner, buyer.contact_name, buyer.contact_details, buyer.email,
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
  const markerRadius = 9;
  const clusterRadius = 12;
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

function rectOverlapArea(a, b) {
  const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  return x * y;
}

function chooseLabelPlacement(position, occupiedRects) {
  const projection = mapProjectionOverlay?.getProjection();
  const point = projection?.fromLatLngToDivPixel(new google.maps.LatLng(position.lat, position.lng));
  if (!point) return "top";
  let best = labelPlacements[0];
  let bestScore = Number.POSITIVE_INFINITY;
  labelPlacements.forEach((placement) => {
    const rect = {
      left: point.x + placement.dx,
      top: point.y + placement.dy,
      right: point.x + placement.dx + placement.width,
      bottom: point.y + placement.dy + placement.height
    };
    const overlap = occupiedRects.reduce((sum, occupied) => sum + rectOverlapArea(rect, occupied), 0);
    if (overlap < bestScore) {
      best = placement;
      bestScore = overlap;
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

function siteLabelForGroup(group) {
  if (group.buyers.length === 1) return group.buyers[0].name;
  return `${group.buyers.length} ${t("个站点")}`;
}

function siteMarkerHtml(group, placement) {
  const statuses = group.buyers.map((buyer) => buyer.inspection_status || "none");
  const statusClass = statuses.includes("overdue")
    ? "inspection-overdue"
    : statuses.includes("warning")
      ? "inspection-warning"
    : statuses.includes("fresh")
      ? "inspection-fresh"
      : "inspection-none";
  const clusterClass = group.buyers.length > 1 ? " is-cluster" : "";
  return `
    <span class="map-site-pin ${statusClass}${clusterClass}" aria-hidden="true">${group.buyers.length > 1 ? group.buyers.length : ""}</span>
    <span class="map-site-label label-${placement}">${escapeHtml(siteLabelForGroup(group))}</span>
  `;
}

function groupInspectionPriority(group) {
  const statuses = group.buyers.map((buyer) => buyer.inspection_status || "none");
  if (statuses.includes("overdue")) return 30;
  if (statuses.includes("warning")) return 25;
  if (statuses.includes("fresh")) return 20;
  return 10;
}

function defineSiteMarkerOverlay() {
  if (SiteMarkerOverlay) return;
  SiteMarkerOverlay = class extends google.maps.OverlayView {
    constructor(group, placement) {
      super();
      this.group = group;
      this.placement = placement;
      this.div = null;
      this.setMap(serviceMap);
    }

    onAdd() {
      this.div = document.createElement("button");
      this.div.type = "button";
      this.div.className = "map-site-marker google-map-site-marker";
      this.div.addEventListener("mouseover", () => openBuyerInfo(this.group.buyers, this));
      this.div.addEventListener("click", () => openBuyerInfo(this.group.buyers, this));
      this.getPanes().overlayMouseTarget.appendChild(this.div);
      this.update(this.group, this.placement);
    }

    draw() {
      if (!this.div) return;
      const projection = this.getProjection();
      const point = projection.fromLatLngToDivPixel(new google.maps.LatLng(this.group.position.lat, this.group.position.lng));
      this.div.style.transform = `translate(${point.x}px, ${point.y}px)`;
    }

    onRemove() {
      this.div?.remove();
      this.div = null;
    }

    getPosition() {
      return new google.maps.LatLng(this.group.position.lat, this.group.position.lng);
    }

    update(group, placement) {
      this.group = group;
      this.placement = placement;
      if (this.div) {
        this.div.title = group.buyers.map((buyer) => buyer.name).join(", ");
        this.div.innerHTML = siteMarkerHtml(group, placement);
        this.div.style.zIndex = String(groupInspectionPriority(group) + (group.buyers.length > 1 ? 5 : 0));
        this.draw();
      }
    }
  };
}

function openBuyerInfo(buyers, marker) {
  if (activeInfoWindow) activeInfoWindow.close();
  activeInfoWindow = new google.maps.InfoWindow({ content: buyerClusterDetails(buyers) });
  activeInfoWindow.setPosition(marker.getPosition());
  activeInfoWindow.open({ map: serviceMap });
}

function ensureMarker(group, placement) {
  let marker = markersByKey.get(group.key);
  if (!marker) {
    marker = new SiteMarkerOverlay(group, placement);
    markersByKey.set(group.key, marker);
  } else {
    marker.update(group, placement);
    if (!marker.getMap()) marker.setMap(serviceMap);
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
    empty.textContent = t("所有站点均已定位。");
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
  const occupiedRects = [];
  const markers = groups.map((group) => {
    const placement = chooseLabelPlacement(group.position, occupiedRects);
    const marker = ensureMarker(group, placement);
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
  defineSiteMarkerOverlay();
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
