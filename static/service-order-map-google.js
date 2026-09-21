// Interactive Google Maps renderer for the service order map page.
// Shared page logic (filters, legend, unlocated panel, geocode loop,
// static-mode toggle) lives in service-order-map-common.js, which must be
// loaded before this file.

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

function renderMarkers({ fit = false } = {}) {
  if (activeInfoWindow) {
    activeInfoWindow.close();
    activeInfoWindow = null;
  }
  markersByKey.forEach((marker) => marker.setMap(null));
  const visibleBuyers = [...buyersById.values()].filter((buyer) => matchesFilters(buyer));
  const locatedBuyers = visibleBuyers.filter((buyer) => hasCoordinates(buyer));
  const groups = groupVisibleBuyers(locatedBuyers);
  const occupiedRects = [];
  const markers = groups.map((group) => {
    const placement = chooseLabelPlacement(group.position, occupiedRects);
    const marker = ensureMarker(group, placement);
    return marker;
  });
  visibleCount.textContent = String(locatedBuyers.length);
  renderMapLegend(visibleBuyers);
  renderUnlocatedBuyers();
  if (fit && markers.length) {
    const bounds = new google.maps.LatLngBounds();
    markers.forEach((marker) => bounds.extend(marker.getPosition()));
    serviceMap.fitBounds(bounds, 48);
    if (markers.length === 1) serviceMap.setZoom(12);
  }
}

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
  registerServiceOrderMapRenderer(renderMarkers);
  initServiceOrderMapChrome();
  google.maps.event.addListenerOnce(serviceMap, "idle", () => renderMarkers({ fit: true }));
  serviceMap.addListener("idle", () => renderMarkers());
  geocodePendingBuyers();
};
