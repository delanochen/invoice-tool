const mapElement = document.querySelector("#serviceOrderMap");
const searchInput = document.querySelector("#mapSearch");
const statusSelect = document.querySelector("#mapStatus");
const visibleCount = document.querySelector("#visibleOrderCount");
const progressText = document.querySelector("#geocodeProgress");
const unlocatedContainer = document.querySelector("#unlocatedOrders");
const retryButton = document.querySelector("#retryFailedGeocodes");
const mapConfig = window.serviceOrderMapConfig || {};
const ordersById = new Map((window.serviceOrderMapData || []).map((order) => [order.id, order]));
const markersById = new Map();

const serviceMap = L.map(mapElement, { zoomControl: true }).setView([39.5, -98.35], 4);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(serviceMap);
const markerLayer = L.layerGroup().addTo(serviceMap);
let resizeTimer;
window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => serviceMap.invalidateSize({ pan: false }), 120);
});

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  })[character]);
}

function markerColor(order) {
  return order.status === "closed" ? "#667085" : "#0f766e";
}

function orderDetails(order) {
  const invoiceLabel = order.invoice_count > 0 ? `已关联 ${order.invoice_count} 张` : "未关联";
  return `
    <div class="map-order-popup">
      <strong>${escapeHtml(order.order_number)}</strong>
      <span>${escapeHtml(order.client_name)}</span>
      <span>${escapeHtml(order.site_address)}</span>
      <dl>
        <dt>服务订单</dt><dd>${escapeHtml(order.client_order_number)}</dd>
        <dt>状态</dt><dd>${escapeHtml(order.status_label)}</dd>
        <dt>日报</dt><dd>${escapeHtml(order.report_count)}</dd>
        <dt>发票</dt><dd>${escapeHtml(invoiceLabel)}</dd>
      </dl>
      <a href="${escapeHtml(order.detail_url)}">查看工单详情</a>
    </div>
  `;
}

function hasCoordinates(order) {
  return Number.isFinite(Number(order.latitude)) && Number.isFinite(Number(order.longitude));
}

function matchesFilters(order) {
  const query = searchInput.value.trim().toLocaleLowerCase();
  const status = statusSelect.value;
  const haystack = [
    order.order_number,
    order.client_name,
    order.client_order_number,
    order.site_address
  ].join(" ").toLocaleLowerCase();
  return (!query || haystack.includes(query)) && (!status || order.status === status);
}

function ensureMarker(order, displayPosition) {
  if (!hasCoordinates(order)) return null;
  let marker = markersById.get(order.id);
  if (!marker) {
    marker = L.circleMarker(displayPosition, {
      radius: 8,
      color: "#ffffff",
      weight: 2,
      fillColor: markerColor(order),
      fillOpacity: 0.95
    });
    marker.bindTooltip(orderDetails(order), { sticky: true, direction: "top", opacity: 0.98 });
    marker.bindPopup(orderDetails(order), { maxWidth: 320 });
    markersById.set(order.id, marker);
  } else {
    marker.setLatLng(displayPosition);
    marker.setStyle({ fillColor: markerColor(order) });
    marker.setTooltipContent(orderDetails(order));
    marker.setPopupContent(orderDetails(order));
  }
  return marker;
}

function renderUnlocatedOrders() {
  const unlocated = [...ordersById.values()].filter((order) => !hasCoordinates(order));
  unlocatedContainer.replaceChildren();
  if (!unlocated.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "所有工单均已定位。";
    unlocatedContainer.appendChild(empty);
  } else {
    unlocated.forEach((order) => {
      const item = document.createElement("a");
      item.className = "unlocated-order";
      item.href = order.detail_url;
      const title = document.createElement("strong");
      title.textContent = order.order_number;
      const address = document.createElement("span");
      address.textContent = order.site_address;
      const status = document.createElement("small");
      status.textContent = order.geocode_status === "failed" ? "无法识别地址" : "等待定位";
      item.append(title, address, status);
      unlocatedContainer.appendChild(item);
    });
  }
  retryButton.hidden = !unlocated.some((order) => order.geocode_status === "failed");
}

function renderMarkers({ fit = false } = {}) {
  markerLayer.clearLayers();
  const visibleMarkers = [];
  const visibleOrders = [...ordersById.values()].filter(
    (order) => matchesFilters(order) && hasCoordinates(order)
  );
  const locationGroups = new Map();
  visibleOrders.forEach((order) => {
    const key = `${Number(order.latitude).toFixed(6)},${Number(order.longitude).toFixed(6)}`;
    if (!locationGroups.has(key)) locationGroups.set(key, []);
    locationGroups.get(key).push(order);
  });
  locationGroups.forEach((group) => {
    group.forEach((order, index) => {
      const latitude = Number(order.latitude);
      const longitude = Number(order.longitude);
      let displayPosition = [latitude, longitude];
      if (group.length > 1) {
        const angle = (Math.PI * 2 * index) / group.length;
        const latitudeOffset = 0.00018 * Math.sin(angle);
        const longitudeScale = Math.max(Math.cos(latitude * Math.PI / 180), 0.25);
        const longitudeOffset = (0.00018 * Math.cos(angle)) / longitudeScale;
        displayPosition = [latitude + latitudeOffset, longitude + longitudeOffset];
      }
      const marker = ensureMarker(order, displayPosition);
      marker.addTo(markerLayer);
      visibleMarkers.push(marker);
    });
  });
  visibleCount.textContent = String(visibleMarkers.length);
  renderUnlocatedOrders();
  if (fit && visibleMarkers.length) {
    const uniqueLocations = new Map();
    visibleMarkers.forEach((marker) => {
      const point = marker.getLatLng();
      uniqueLocations.set(`${point.lat.toFixed(6)},${point.lng.toFixed(6)}`, point);
    });
    if (uniqueLocations.size === 1) {
      serviceMap.setView([...uniqueLocations.values()][0], 11);
    } else {
      const bounds = L.featureGroup(visibleMarkers).getBounds();
      serviceMap.fitBounds(bounds.pad(0.15), { maxZoom: 13 });
    }
  }
}

async function geocodePendingOrders() {
  if (!mapConfig.geocodingEnabled) {
    progressText.textContent = "地址解析未启用";
    return;
  }
  try {
    const response = await fetch(mapConfig.geocodeNextUrl, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    });
    if (!response.ok) throw new Error("geocode request failed");
    const result = await response.json();
    if (result.order) {
      ordersById.set(result.order.id, result.order);
      renderMarkers({ fit: markersById.size === 0 });
    }
    if (result.remaining > 0) {
      progressText.textContent = `正在定位，剩余 ${result.remaining} 个`;
      window.setTimeout(geocodePendingOrders, 250);
    } else {
      progressText.textContent = "";
      renderMarkers({ fit: markersById.size > 0 });
    }
  } catch (error) {
    progressText.textContent = "定位服务暂时不可用，稍后打开页面会继续";
  }
}

searchInput.addEventListener("input", () => renderMarkers({ fit: true }));
statusSelect.addEventListener("change", () => renderMarkers({ fit: true }));
retryButton.addEventListener("click", async () => {
  retryButton.disabled = true;
  progressText.textContent = "准备重新定位";
  try {
    const response = await fetch(mapConfig.retryFailedUrl, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    });
    if (!response.ok) throw new Error("retry request failed");
    ordersById.forEach((order) => {
      if (order.geocode_status === "failed") order.geocode_status = "pending";
    });
    renderUnlocatedOrders();
    await geocodePendingOrders();
  } catch (error) {
    progressText.textContent = "重新定位失败";
  } finally {
    retryButton.disabled = false;
  }
});

renderMarkers({ fit: true });
window.setTimeout(() => {
  serviceMap.invalidateSize({ pan: false });
  renderMarkers({ fit: true });
}, 150);
geocodePendingOrders();
