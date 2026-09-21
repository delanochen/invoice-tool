// Interactive Leaflet (OpenStreetMap) renderer for the service order map
// page — the fallback used when no Google Maps browser key is configured.
// Shared page logic (filters, legend, unlocated panel, geocode loop,
// static-mode toggle) lives in service-order-map-common.js, which must be
// loaded before this file.

const markersById = new Map();
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
const serviceMap = L.map(mapElement, { zoomControl: true }).setView([39.5, -98.35], 4);
const markerLayer = L.layerGroup().addTo(serviceMap);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(serviceMap);

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
  if (
    !headquarters ||
    typeof headquarters.latitude !== "number" ||
    typeof headquarters.longitude !== "number" ||
    !Number.isFinite(headquarters.latitude) ||
    !Number.isFinite(headquarters.longitude) ||
    headquarters.latitude < -90 ||
    headquarters.latitude > 90 ||
    headquarters.longitude < -180 ||
    headquarters.longitude > 180
  ) return;
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

registerServiceOrderMapRenderer(renderMarkers);
initServiceOrderMapChrome();
addHeadquartersMarker();
renderMarkers({ fit: true });
serviceMap.on("zoomend moveend", () => renderMarkers());
window.setTimeout(() => serviceMap.invalidateSize({ pan: false }), 150);
geocodePendingBuyers();
