// Shared logic for the service order map page.
//
// Loaded in BOTH modes (interactive Google / interactive Leaflet / static
// image). Owns: page element refs, filter state & rendering, legend,
// unlocated panel, the geocode-next loop, and the static-mode toggle.
// The active renderer (Google map, Leaflet map, or the static-image
// renderer) registers itself via registerServiceOrderMapRenderer(); the
// shared controls then call serviceOrderMapRefresh({ fit }) on every
// filter change, exactly as the two former per-provider copies did.

const mapElement = document.querySelector("#serviceOrderMap");
const searchInput = document.querySelector("#mapSearch");
const filterOptionContainers = document.querySelectorAll("[data-map-filter-options]");
const visibleCount = document.querySelector("#visibleOrderCount");
const progressText = document.querySelector("#geocodeProgress");
const legendContainer = document.querySelector("#mapLegend");
const unlocatedPanel = document.querySelector(".map-unlocated-panel");
const unlocatedContainer = document.querySelector("#unlocatedOrders");
const retryButton = document.querySelector("#retryFailedGeocodes");
const staticModeToggle = document.querySelector("#staticModeToggle");
const mapConfig = window.serviceOrderMapConfig || {};
const t = (value) => window.uiTranslate ? window.uiTranslate(value) : value;
const buyersById = new Map((window.serviceOrderMapData || []).map((buyer) => [buyer.id, buyer]));

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

window.serviceOrderMapGeocoding = false;

// Set to true as soon as an interactive renderer (Google Maps JS or Leaflet)
// registers itself. The page script uses it to detect that the third-party
// map script never arrived (blocked network, invalid key, ...) and falls back
// to the server-rendered static image instead of leaving an empty map.
window.serviceOrderMapRendererRegistered = false;

let serviceOrderMapRenderer = null;

function registerServiceOrderMapRenderer(renderer) {
  serviceOrderMapRenderer = renderer;
  window.serviceOrderMapRendererRegistered = true;
}

function serviceOrderMapRefresh(options = {}) {
  if (serviceOrderMapRenderer) serviceOrderMapRenderer(options);
}

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
    <dt>${t("发票")}</dt><dd>${money(buyer.paid_invoice_amount)} / ${money(buyer.completed_invoice_amount)}</dd>
  ` : "";
  return `
    <div class="map-order-popup">
      <strong>${escapeHtml(buyer.name)}</strong>
      <span>${buyerAddressLink(buyer)}</span>
      <dl>
        <dt>${t("联系人")}</dt><dd>${escapeHtml(buyer.contact_name || "-")}</dd>
        <dt>${t("联系方式")}</dt><dd>${escapeHtml(buyer.contact_details || "-")}</dd>
        <dt>${t("邮箱")}</dt><dd>${escapeHtml(buyer.email || "-")}</dd>
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
  const selectedStatuses = selectedFilterValues("status");
  const selectedSites = selectedFilterValues("name");
  const selectedOwners = selectedFilterValues("owner");
  const selectedManufacturers = selectedFilterValues("equipment_manufacturer");
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
    (!selectedManufacturers.size || selectedManufacturers.has(filterText(buyer.equipment_manufacturer))) &&
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

async function geocodePendingBuyers() {
  if (!mapConfig.geocodingEnabled) return;
  window.serviceOrderMapGeocoding = true;
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
    if (result.remaining > 0) {
      progressText.textContent = `${t("正在定位，剩余")} ${result.remaining}`;
    } else {
      progressText.textContent = "";
      window.serviceOrderMapGeocoding = false;
    }
    serviceOrderMapRefresh({ fit: Boolean(result.buyer) });
    if (result.remaining > 0) {
      window.setTimeout(geocodePendingBuyers, 250);
    }
  } catch (error) {
    window.serviceOrderMapGeocoding = false;
    progressText.textContent = t("定位服务暂时不可用，稍后打开页面会继续。");
  }
}

function setupServiceOrderMapControls() {
  searchInput.addEventListener("input", () => serviceOrderMapRefresh({ fit: true }));
  filterOptionContainers.forEach((container) => {
    container.addEventListener("change", () => {
      updateMapFilterSummaries();
      serviceOrderMapRefresh({ fit: true });
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
}

function initServiceOrderMapChrome() {
  renderMapFilterOptions();
  setupMapFilterAutoClose();
  setupServiceOrderMapControls();
  if (staticModeToggle) {
    staticModeToggle.checked = window.serviceOrderMapMode === "static";
    staticModeToggle.addEventListener("change", () => {
      try {
        localStorage.setItem("serviceOrderMapMode", staticModeToggle.checked ? "static" : "interactive");
      } catch (error) {
        /* storage unavailable; still toggle for this visit */
      }
      window.location.reload();
    });
  }
}
