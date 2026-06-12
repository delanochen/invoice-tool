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
const usStateCodes = new Set([
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
  "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
  "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
  "VA", "WA", "WV", "WI", "WY", "DC"
]);
let serviceMap;
let googleGeocoder;
let activeInfoWindow;
let stickyMarkerId = null;

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

function addressExpectations(address) {
  const uppercaseAddress = String(address || "").toUpperCase();
  const stateMatches = [...uppercaseAddress.matchAll(/\b[A-Z]{2}\b/g)]
    .map((match) => match[0])
    .filter((token) => usStateCodes.has(token));
  const zipMatches = [...uppercaseAddress.matchAll(/\b(\d{5})(?:-\d{4})?\b/g)];
  return {
    state: stateMatches.at(-1) || "",
    zip: zipMatches.at(-1)?.[1] || ""
  };
}

function googleComponent(result, componentType, shortName = false) {
  const component = result.address_components.find((item) => item.types.includes(componentType));
  if (!component) return "";
  return shortName ? component.short_name : component.long_name;
}

function googleResultMatchesAddress(result, address) {
  const expected = addressExpectations(address);
  const country = googleComponent(result, "country", true).toUpperCase();
  const state = googleComponent(result, "administrative_area_level_1", true).toUpperCase();
  const zip = googleComponent(result, "postal_code").slice(0, 5);
  if (country && country !== "US") return false;
  if (expected.state && state !== expected.state) return false;
  if (expected.zip && zip !== expected.zip) return false;
  return true;
}

function markerPosition(order, index, groupLength) {
  const latitude = Number(order.latitude);
  const longitude = Number(order.longitude);
  if (groupLength === 1) return { lat: latitude, lng: longitude };
  const angle = (Math.PI * 2 * index) / groupLength;
  const latitudeOffset = 0.00018 * Math.sin(angle);
  const longitudeScale = Math.max(Math.cos(latitude * Math.PI / 180), 0.25);
  const longitudeOffset = (0.00018 * Math.cos(angle)) / longitudeScale;
  return { lat: latitude + latitudeOffset, lng: longitude + longitudeOffset };
}

function openOrderInfo(order, marker, sticky = false) {
  if (activeInfoWindow) activeInfoWindow.close();
  activeInfoWindow = new google.maps.InfoWindow({ content: orderDetails(order) });
  activeInfoWindow.open({ map: serviceMap, anchor: marker });
  stickyMarkerId = sticky ? order.id : null;
}

function ensureMarker(order, position) {
  let marker = markersById.get(order.id);
  const icon = {
    path: google.maps.SymbolPath.CIRCLE,
    scale: 8,
    fillColor: markerColor(order),
    fillOpacity: 0.95,
    strokeColor: "#ffffff",
    strokeWeight: 2
  };
  if (!marker) {
    marker = new google.maps.Marker({
      map: serviceMap,
      position,
      title: `${order.order_number} - ${order.client_name}`,
      icon
    });
    marker.addListener("mouseover", () => openOrderInfo(ordersById.get(order.id), marker));
    marker.addListener("mouseout", () => {
      if (stickyMarkerId !== order.id && activeInfoWindow) activeInfoWindow.close();
    });
    marker.addListener("click", () => openOrderInfo(ordersById.get(order.id), marker, true));
    markersById.set(order.id, marker);
  } else {
    marker.setPosition(position);
    marker.setIcon(icon);
    marker.setMap(serviceMap);
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
  markersById.forEach((marker) => marker.setMap(null));
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
      const marker = ensureMarker(order, markerPosition(order, index, group.length));
      visibleMarkers.push(marker);
    });
  });
  visibleCount.textContent = String(visibleMarkers.length);
  renderUnlocatedOrders();
  if (fit && visibleMarkers.length) {
    const bounds = new google.maps.LatLngBounds();
    visibleMarkers.forEach((marker) => bounds.extend(marker.getPosition()));
    serviceMap.fitBounds(bounds, 48);
    if (visibleMarkers.length === 1) {
      google.maps.event.addListenerOnce(serviceMap, "idle", () => {
        if (serviceMap.getZoom() > 13) serviceMap.setZoom(13);
      });
    }
  }
}

function geocodeGoogleOrder(order) {
  return new Promise((resolve, reject) => {
    googleGeocoder.geocode(
      {
        address: order.site_address,
        componentRestrictions: { country: "US" },
        region: "US"
      },
      (results, status) => {
        if (status === "OK") {
          const result = results.find((candidate) => googleResultMatchesAddress(candidate, order.site_address));
          if (!result) {
            resolve(false);
            return;
          }
          const location = result.geometry.location;
          order.latitude = location.lat();
          order.longitude = location.lng();
          order.geocode_status = "success";
          resolve(true);
          return;
        }
        if (status === "ZERO_RESULTS") {
          resolve(false);
          return;
        }
        reject(new Error(`Google geocoding failed: ${status}`));
      }
    );
  });
}

async function geocodeGoogleOrders(onlyFailed = false) {
  if (!mapConfig.geocodingEnabled) {
    progressText.textContent = "地址解析未启用";
    return;
  }
  const orders = [...ordersById.values()].filter(
    (order) => !onlyFailed || order.geocode_status === "failed"
  );
  let completed = 0;
  try {
    for (const order of orders) {
      order.latitude = null;
      order.longitude = null;
      order.geocode_status = "pending";
      const located = await geocodeGoogleOrder(order);
      if (!located) order.geocode_status = "failed";
      completed += 1;
      progressText.textContent = `Google 正在定位，剩余 ${orders.length - completed} 个`;
      renderMarkers({ fit: completed === orders.length });
      await new Promise((resolve) => window.setTimeout(resolve, 120));
    }
    progressText.textContent = "";
  } catch (error) {
    orders.forEach((order) => {
      if (order.geocode_status === "pending") order.geocode_status = "failed";
    });
    progressText.textContent = "Google 定位暂时不可用，请检查 API 配置、配额或域名限制";
    renderMarkers({ fit: true });
  }
}

searchInput.addEventListener("input", () => renderMarkers({ fit: true }));
statusSelect.addEventListener("change", () => renderMarkers({ fit: true }));
retryButton.addEventListener("click", async () => {
  retryButton.disabled = true;
  progressText.textContent = "准备重新定位";
  try {
    await geocodeGoogleOrders(true);
  } catch (error) {
    progressText.textContent = "重新定位失败";
  } finally {
    retryButton.disabled = false;
  }
});

window.initServiceOrderGoogleMap = function initServiceOrderGoogleMap() {
  googleGeocoder = new google.maps.Geocoder();
  serviceMap = new google.maps.Map(mapElement, {
    center: { lat: 39.5, lng: -98.35 },
    zoom: 4,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true
  });
  ordersById.forEach((order) => {
    order.latitude = null;
    order.longitude = null;
    order.geocode_status = "pending";
  });
  renderMarkers();
  geocodeGoogleOrders();
};
