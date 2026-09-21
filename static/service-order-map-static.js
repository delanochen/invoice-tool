// Static-image renderer for the service order map page.
//
// Used when the visitor switches the page to "静态模式": instead of the
// interactive Google Maps JS / Leaflet map, the currently filtered sites are
// sent to the server, drawn with Google Static Maps (coordinates already
// stored — no geocoding), and returned as a base64 image plus a labeled
// site list. No third-party domain is contacted from the browser in this
// mode, so the page works from networks where Google is unreachable.
//
// Shared page logic (filters, legend, unlocated panel, geocode loop, the
// mode toggle) lives in service-order-map-common.js, which must be loaded
// before this file.

const staticPanel = document.querySelector("#staticMapPanel");
const staticImage = document.querySelector("#staticMapImage");
const staticStatus = document.querySelector("#staticMapStatus");
const staticSiteList = document.querySelector("#staticSiteList");

const STATIC_STATUS_COLORS = {
  overdue: "#d92d20",
  warning: "#f79009",
  fresh: "#0f766e",
  none: "#667085"
};
const STATIC_HEADQUARTERS_COLOR = "#7c3aed";
const STATIC_RENDER_DEBOUNCE_MS = 400;

let staticRenderTimer = null;
let staticRenderInFlight = false;
let staticRenderQueued = false;
let lastStaticSignature = "";

function staticErrorMessage(code) {
  const messages = {
    static_maps_api_not_configured: t("服务器未配置 Google Static Maps 密钥，请在系统设置中填写后重试。"),
    static_maps_rate_limit: t("静态地图请求过于频繁，请稍后再试。"),
    no_visible_sites: t("当前筛选条件下没有可定位的站点。"),
    request_failed: t("静态地图生成失败，请稍后重试。")
  };
  return messages[code] || t("静态地图生成失败，请稍后重试。");
}

function staticHeadquartersValid() {
  const headquarters = mapConfig.headquarters;
  return Boolean(
    headquarters &&
    typeof headquarters.latitude === "number" &&
    typeof headquarters.longitude === "number" &&
    Number.isFinite(headquarters.latitude) &&
    Number.isFinite(headquarters.longitude) &&
    headquarters.latitude >= -90 && headquarters.latitude <= 90 &&
    headquarters.longitude >= -180 && headquarters.longitude <= 180
  );
}

function staticVisibleLocatedBuyers() {
  return [...buyersById.values()]
    .filter((buyer) => matchesFilters(buyer) && hasCoordinates(buyer))
    .sort((a, b) =>
      String(a.buyer_number || "").localeCompare(String(b.buyer_number || "")) ||
      String(a.name || "").localeCompare(String(b.name || ""))
    );
}

function renderStaticSiteList(entries, headquartersIncluded) {
  const items = [];
  if (headquartersIncluded) {
    const headquartersName = escapeHtml(mapConfig.headquarters?.name || t("公司总部"));
    items.push(`
      <span class="static-site-item is-headquarters">
        <span class="static-marker-chip" style="background:${STATIC_HEADQUARTERS_COLOR}"></span>
        <strong>${headquartersName}</strong>
      </span>
    `);
  }
  entries.forEach((entry) => {
    const chipLabel = entry.label ? escapeHtml(entry.label) : "";
    items.push(`
      <a class="static-site-item" href="${escapeHtml(entry.buyer.detail_url)}">
        <span class="static-marker-chip" style="background:${entry.color}">${chipLabel}</span>
        <strong>${escapeHtml(entry.buyer.buyer_number)} · ${escapeHtml(entry.buyer.name)}</strong>
      </a>
    `);
  });
  staticSiteList.innerHTML = items.join("");
  staticSiteList.hidden = items.length === 0;
}

async function renderStaticMap({ fit = false } = {}) {
  if (!staticPanel || staticPanel.hidden) return;
  if (window.serviceOrderMapGeocoding) {
    staticStatus.textContent = t("站点定位进行中，完成后自动生成静态图…");
    return;
  }
  const visibleBuyers = [...buyersById.values()].filter((buyer) => matchesFilters(buyer));
  const located = staticVisibleLocatedBuyers();
  visibleCount.textContent = String(located.length);
  renderMapLegend(visibleBuyers);
  renderUnlocatedBuyers();
  if (!located.length) {
    lastStaticSignature = "";
    staticImage.hidden = true;
    staticImage.removeAttribute("src");
    staticSiteList.hidden = true;
    staticSiteList.innerHTML = "";
    staticStatus.textContent = t("当前筛选条件下没有可定位的站点。");
    return;
  }
  const payload = {
    points: located.slice(0, 200).map((buyer) => ({
      latitude: Number(buyer.latitude),
      longitude: Number(buyer.longitude),
      inspection_status: buyer.inspection_status || "none"
    })),
    include_headquarters: staticHeadquartersValid()
  };
  const signature = JSON.stringify(payload);
  if (signature === lastStaticSignature && !staticImage.hidden) return;
  if (staticRenderInFlight) {
    staticRenderQueued = true;
    return;
  }
  staticRenderInFlight = true;
  staticStatus.textContent = t("正在生成静态地图…");
  try {
    const response = await fetch(mapConfig.staticImageUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest"
      },
      body: JSON.stringify(payload)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.available || !result.image) {
      lastStaticSignature = "";
      staticImage.hidden = true;
      staticImage.removeAttribute("src");
      staticSiteList.hidden = true;
      staticSiteList.innerHTML = "";
      staticStatus.textContent = staticErrorMessage(result.error);
      return;
    }
    const labels = Array.isArray(result.labels) ? result.labels : [];
    const entries = located.slice(0, labels.length).map((buyer, index) => ({
      buyer,
      label: labels[index] || "",
      color: STATIC_STATUS_COLORS[buyer.inspection_status || "none"] || STATIC_STATUS_COLORS.none
    }));
    staticImage.src = result.image;
    staticImage.hidden = false;
    renderStaticSiteList(entries, Boolean(result.headquarters));
    const notes = [`${t("共")} ${result.count} ${t("个站点")}`];
    if (result.truncated) {
      notes.push(t("站点较多，仅显示前 200 个，建议用筛选缩小范围"));
    } else if (!result.labeled) {
      notes.push(t("站点较多，图中未编号，颜色代表巡检状态"));
    } else {
      notes.push(t("图上编号与下方列表一致"));
    }
    if (result.headquarters) notes.push(t("紫色为公司总部"));
    staticStatus.textContent = notes.join(" · ");
    lastStaticSignature = signature;
  } catch (error) {
    lastStaticSignature = "";
    staticStatus.textContent = staticErrorMessage("request_failed");
  } finally {
    staticRenderInFlight = false;
    if (staticRenderQueued) {
      staticRenderQueued = false;
      renderStaticMap({ fit: true });
    }
  }
}

function scheduleStaticRender() {
  if (staticRenderTimer) window.clearTimeout(staticRenderTimer);
  staticRenderTimer = window.setTimeout(() => {
    staticRenderTimer = null;
    renderStaticMap({ fit: true });
  }, STATIC_RENDER_DEBOUNCE_MS);
}

function showStaticPanel() {
  if (!staticPanel) return;
  staticPanel.hidden = false;
  if (mapElement) mapElement.hidden = true;
  registerServiceOrderMapRenderer(() => scheduleStaticRender());
  initServiceOrderMapChrome();
  renderStaticMap({ fit: true });
  geocodePendingBuyers();
}

showStaticPanel();
