/* AI Daily Report - Draft Detail Review (Phase 6)
 * Preview aggregation, inline edit, 409 handling, confirm/cancel/reopen
 */
(function () {
  "use strict";

  const draftId = window.aiDailyReportDraftId;
  const apiBase = window.aiDailyReportApiBase || "/api/ai/daily-report";
  const csrfToken = window.csrfToken || "";

  let currentPreview = null;
  let currentDraftVersion = null;
  let isLoading = false;
  let isMutating = false;

  // DOM
  const statusBanner = document.getElementById("statusBanner");
  const conflictDialog = document.getElementById("conflictDialog");
  const overrideDialog = document.getElementById("overrideDialog");

  // ─── Delegated mutation buttons ─────────────────────────────────────────
  // Buttons are re-rendered by innerHTML on every preview load; a delegated
  // document-level listener survives re-renders and never loses its binding
  // (fixes "发现照片 does nothing" when the per-render binding is lost).
  document.addEventListener("click", function (evt) {
    const el = evt.target;
    if (!el || !el.id) return;
    if (el.id === "discoverPhotosBtn") discoverPhotos();
  });

  // ─── API Helpers ────────────────────────────────────────────────────────

  async function apiGet(path) {
    const resp = await fetch(`${apiBase}${path}`);
    if (resp.status === 409) {
      const data = await resp.json().catch(() => ({}));
      showConflict(data);
      throw new Error("version_conflict");
    }
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  async function apiPost(path, body) {
    const resp = await fetch(`${apiBase}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(body || {}),
    });
    if (resp.status === 409) {
      const data = await resp.json().catch(() => ({}));
      showConflict(data);
      // Re-sync with the server's latest draft version so the next attempt
      // succeeds instead of looping on silent 409s.
      try { await loadPreview(); } catch (_e) { /* ignore */ }
      showStatus("数据已被其他操作更新，页面已刷新，请重试", "error");
      throw new Error("version_conflict");
    }
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      const err = new Error(data.error || `HTTP ${resp.status}`);
      err.data = data;
      throw err;
    }
    const data = await resp.json();
    // Unify the draft-version variables: every mutation response that carries
    // draft_version must update both, or later calls 409 (stale version).
    if (data && typeof data.draft_version === "number") {
      currentDraftVersion = data.draft_version;
      if (currentPreview) currentPreview.draft_version = data.draft_version;
    }
    return data;
  }

  // ─── Load Preview ───────────────────────────────────────────────────────

  async function loadPreview() {
    if (isLoading) return;
    isLoading = true;
    try {
      const data = await apiGet(`/draft/${draftId}/preview`);
      currentPreview = data.preview;
      currentDraftVersion = currentPreview.draft_version;
      renderAll();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`加载失败: ${err.message}`, "error");
      }
    } finally {
      isLoading = false;
    }
  }

  // ─── Render All ─────────────────────────────────────────────────────────

  function renderAll() {
    if (!currentPreview) return;
    renderHeader();
    renderBasicInfo();
    renderWorkers();
    renderMileageEvidence();
    renderTimeline();
    renderSafetyPhoto();
    renderServicePhotos();
    renderWorkItems();
    renderVerification();
    renderValidation();
    renderAttachmentPreparation();
    renderFormalSave();
    renderProvenance();
    renderAudit();
    renderActions();
  }

  function renderHeader() {
    const p = currentPreview;
    const bi = p.basic_info || {};
    document.getElementById("draftTitle").textContent = `${bi.order_number || "Draft"} - ${bi.report_date || ""}`;
    document.getElementById("draftSubtitle").textContent = `状态: ${p.status} | 版本: ${p.draft_version} | ${bi.site_name || ""}`;
  }

  function renderBasicInfo() {
    const bi = currentPreview.basic_info || {};
    document.getElementById("basicInfo").innerHTML = `
      <table style="width:100%; border-collapse:collapse;">
        <tr><td style="padding:4px 8px; font-weight:bold;">报告日期</td><td>${bi.report_date || "-"}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">工单号</td><td>${bi.order_number || "-"}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">站点</td><td>${bi.site_name || "-"}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">站点地址</td><td>${bi.site_address || "-"} <span class="muted-line" style="font-size:0.8rem;">(${bi.site_address_source || "unknown"})</span></td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">创建人</td><td>${bi.created_by || "-"}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">创建时间</td><td>${formatDate(bi.created_at)}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">更新时间</td><td>${formatDate(bi.updated_at)}</td></tr>
      </table>
    `;
  }

  function renderWorkers() {
    const workers = currentPreview.workers || [];
    const canEdit = currentPreview.status === "draft";
    document.getElementById("addWorkerBtn").style.display = canEdit ? "" : "none";
    if (!workers.length) {
      document.getElementById("workersSection").innerHTML = canEdit
        ? '<p class="muted-line">未添加工作人员。点击右上角"添加工作人员"按钮加入。</p>'
        : '<p class="muted-line">未添加工作人员。</p>';
      return;
    }
    let html = workers.map((w, idx) => {
      const routeStatusBadge = getRouteStatusBadge(w.route_status);
      return `
        <div style="border:1px solid #e5e7eb; border-radius:8px; padding:1rem; margin-bottom:0.75rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
            <strong>${w.name || "未知"}</strong>
            ${routeStatusBadge}
          </div>
          <table style="width:100%; font-size:0.9rem;">
            <tr><td style="padding:2px 8px; color:#6b7280;">交通方式</td><td>
              ${canEdit ? `<select data-worker-idx="${idx}" data-field="transportation">
                <option value="self_drive" ${w.transportation === "self_drive" ? "selected" : ""}>自驾</option>
                <option value="carpool" ${w.transportation === "carpool" ? "selected" : ""}>拼车</option>
                <option value="passenger" ${w.transportation === "passenger" ? "selected" : ""}>乘客</option>
                <option value="flight" ${w.transportation === "flight" ? "selected" : ""}>飞机</option>
                <option value="rental_car" ${w.transportation === "rental_car" ? "selected" : ""}>租车</option>
              </select>` : w.transportation}
            </td></tr>
            <tr><td style="padding:2px 8px; color:#6b7280;">出发地</td><td>
              ${canEdit ? `<input type="text" data-worker-idx="${idx}" data-field="origin" value="${escapeHtml(w.origin || "")}" style="width:100%;">` : escapeHtml(w.origin || "-")}
              <span class="muted-line" style="font-size:0.75rem;">来源: ${w.origin_source || "unknown"} ${w.origin_confirmed ? "✓" : "⚠ 未确认"}</span>
            </td></tr>
            <tr><td style="padding:2px 8px; color:#6b7280;">目的地</td><td>${escapeHtml(w.destination || "-")} <span class="muted-line" style="font-size:0.75rem;">(${w.destination_source || "service_order"})</span></td></tr>
            <tr><td style="padding:2px 8px; color:#6b7280;">当天住宿</td><td>
              ${canEdit ? `<select data-worker-idx="${idx}" data-field="overnight_stay">
                <option value="" ${w.overnight_stay === null || w.overnight_stay === undefined ? "selected" : ""}>未选择</option>
                <option value="false" ${w.overnight_stay === false ? "selected" : ""}>不住宿</option>
                <option value="true" ${w.overnight_stay === true ? "selected" : ""}>住宿</option>
              </select>` : (w.overnight_stay === null ? "未选择" : w.overnight_stay ? "住宿" : "不住宿")}
            </td></tr>
            <tr><td style="padding:2px 8px; color:#6b7280;">单程里程</td><td>${w.one_way_miles != null ? w.one_way_miles + " mi" : "-"}</td></tr>
            <tr><td style="padding:2px 8px; color:#6b7280;">日报里程</td><td><strong>${w.reported_miles != null ? w.reported_miles + " mi" : "-"}</strong></td></tr>
            <tr><td style="padding:2px 8px; color:#6b7280;">路线提供商</td><td>${w.route_provider || "-"}</td></tr>
            <tr><td style="padding:2px 8px; color:#6b7280;">查询时间</td><td>${formatDate(w.route_query_time)}</td></tr>
          </table>
          ${canEdit ? `<button type="button" class="secondary" style="margin-top:0.5rem;" data-save-worker="${idx}">保存修改</button>
          <button type="button" class="secondary" style="margin-top:0.5rem; margin-left:0.5rem; color:#b91c1c;" data-remove-worker="${w.user_id}">移除</button>` : ""}
        </div>
      `;
    }).join("");

    document.getElementById("workersSection").innerHTML = html;

    // Bind save / remove buttons
    if (canEdit) {
      document.querySelectorAll("[data-save-worker]").forEach((btn) => {
        btn.addEventListener("click", () => saveWorker(parseInt(btn.dataset.saveWorker, 10)));
      });
      document.querySelectorAll("[data-remove-worker]").forEach((btn) => {
        btn.addEventListener("click", () => removeWorker(parseInt(btn.dataset.removeWorker, 10)));
      });
    }
  }

  function openAddWorkerDialog() {
    const dialog = document.getElementById("addWorkerDialog");
    document.getElementById("staffSearchInput").value = "";
    document.getElementById("staffSearchResults").textContent = "输入关键词后点搜索。";
    dialog.style.display = "flex";
    document.getElementById("staffSearchInput").focus();
  }

  function closeAddWorkerDialog() {
    document.getElementById("addWorkerDialog").style.display = "none";
  }

  async function searchStaff() {
    const q = document.getElementById("staffSearchInput").value.trim();
    if (!q) {
      document.getElementById("staffSearchResults").innerHTML = '<span class="muted-line">请输入关键词。</span>';
      return;
    }
    const resultsEl = document.getElementById("staffSearchResults");
    resultsEl.textContent = "搜索中...";
    try {
      const resp = await fetch(`/api/ai/daily-report/staff?q=${encodeURIComponent(q)}`);
      const data = await resp.json();
      if (!data.ok) {
        resultsEl.textContent = data.error || "搜索失败。";
        return;
      }
      if (!data.staff.length) {
        resultsEl.innerHTML = '<span class="muted-line">未找到匹配的员工。</span>';
        return;
      }
      resultsEl.innerHTML = data.staff
        .map(
          (p) =>
            `<div style="display:flex; justify-content:space-between; align-items:center; padding:0.6rem 0.4rem; border-bottom:1px solid #f3f4f6; cursor:pointer;" data-staff-add="${p.id}">
              <div>
                <strong>${escapeHtml(p.name)}</strong>
                <span class="muted-line" style="font-size:0.75rem; margin-left:0.5rem;">${escapeHtml(p.email)}</span>
              </div>
              <span class="muted-line" style="font-size:0.75rem;">${p.role === "admin" ? "管理员" : p.role === "manager" ? "经理" : "员工"}</span>
            </div>`
        )
        .join("");
      resultsEl.querySelectorAll("[data-staff-add]").forEach((row) => {
        row.addEventListener("click", () => addWorker(parseInt(row.dataset.staffAdd, 10)));
      });
    } catch (err) {
      resultsEl.textContent = "请求失败: " + err;
    }
  }

  async function addWorker(userId) {
    const draftVersion = currentDraftVersion;
    try {
      const resp = await fetch(`/api/ai/daily-report/draft/${draftId}/add-worker`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken || "",
        },
        body: JSON.stringify({ user_id: userId, draft_version: draftVersion }),
      });
      const data = await resp.json();
      if (data.ok) {
        closeAddWorkerDialog();
        currentPreview.draft_version = data.draft_version;
        loadPreview();
      } else if (resp.status === 409) {
        showConflict(data);
      } else {
        alert("添加失败: " + (data.error || "未知错误"));
      }
    } catch (err) {
      alert("请求失败: " + err);
    }
  }

  async function removeWorker(userId) {
    const w = currentPreview.workers.find((x) => x.user_id === userId);
    if (!w) return;
    if (!window.confirm(`确定从本日报移除 ${w.name || "该员工"}？`)) return;
    const draftVersion = currentDraftVersion;
    try {
      const resp = await fetch(`/api/ai/daily-report/draft/${draftId}/remove-worker`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken || "",
        },
        body: JSON.stringify({ user_id: userId, draft_version: draftVersion }),
      });
      const data = await resp.json();
      if (data.ok) {
        currentPreview.draft_version = data.draft_version;
        loadPreview();
      } else if (resp.status === 409) {
        showConflict(data);
      } else {
        alert("移除失败: " + (data.error || "未知错误"));
      }
    } catch (err) {
      alert("请求失败: " + err);
    }
  }

  async function saveWorker(idx) {
    const w = currentPreview.workers[idx];
    const inputs = document.querySelectorAll(`[data-worker-idx="${idx}"]`);
    const updates = { user_id: w.user_id };
    inputs.forEach((inp) => {
      const field = inp.dataset.field;
      if (field === "overnight_stay") {
        updates[field] = inp.value === "" ? null : inp.value === "true";
      } else {
        updates[field] = inp.value;
      }
    });
    updates.draft_version = currentDraftVersion;

    try {
      const result = await apiPost(`/draft/${draftId}/update-worker`, updates);
      currentDraftVersion = result.draft_version;
      showStatus("工作人员信息已更新，里程已失效需重新计算", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`保存失败: ${err.message}`, "error");
      }
    }
  }

  function renderMileageEvidence() {
    const workers = currentPreview.workers || [];
    const evidenceWorkers = workers.filter((w) => w.transportation === "self_drive");

    if (!evidenceWorkers.length) {
      const canEdit0 = currentPreview.status === "draft";
      document.getElementById("mileageEvidenceSection").innerHTML =
        '<p class="muted-line">无自驾人员。</p>' +
        (canEdit0 ? '<button type="button" class="secondary" style="margin-top:0.5rem;" id="recalcMileageBtn">重新计算里程</button>' : "");
      document.getElementById("recalcMileageBtn")?.addEventListener("click", recalcMileage);
      return;
    }

    let html = evidenceWorkers.map((w) => {
      const evidenceId = w.mileage_evidence_id;
      const evidenceReady = w.route_status === "success" && evidenceId;
      return `
        <div style="border:1px solid #e5e7eb; border-radius:8px; padding:1rem; margin-bottom:0.75rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
            <strong>${w.name || "未知"}</strong>
            <span class="muted-line">${w.reported_miles != null ? w.reported_miles + " mi" : "未计算"}</span>
          </div>
          ${evidenceReady
            ? `<img src="/api/ai/daily-report/draft/${draftId}/evidence/${evidenceId}" alt="Mileage Evidence" style="max-width:100%; border-radius:4px; cursor:pointer;" onclick="window.openImagePreview(this.src)">`
            : `<p class="muted-line">里程佐证未生成。状态: ${w.route_status}</p>`}
        </div>
      `;
    }).join("");

    const canEdit = currentPreview.status === "draft";
    html += canEdit ? '<div style="margin-top:0.75rem;"><button type="button" class="secondary" id="recalcMileageBtn">重新计算里程</button></div>' : "";
    document.getElementById("mileageEvidenceSection").innerHTML = html;
    document.getElementById("recalcMileageBtn")?.addEventListener("click", recalcMileage);
  }

  function renderTimeline() {
    const t = currentPreview.timeline || {};
    const canEdit = currentPreview.status === "draft";

    document.getElementById("timelineSection").innerHTML = `
      <table style="width:100%;">
        <tr><td style="padding:4px 8px; font-weight:bold;">到达时间</td><td>
          ${canEdit ? `<input type="time" id="arrivalTimeInput" value="${escapeHtml(t.arrival_time || "")}">` : (t.arrival_time || "-")}
          <span class="muted-line" style="font-size:0.75rem;">来源: ${t.arrival_time_source || "unknown"}</span>
        </td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">离场时间</td><td>
          ${canEdit ? `<input type="time" id="departureTimeInput" value="${escapeHtml(t.departure_time || "")}">` : (t.departure_time || "-")}
          <span class="muted-line" style="font-size:0.75rem;">来源: ${t.departure_time_source || "unknown"}</span>
        </td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">时间线状态</td><td>${getTimelineStatusBadge(t.photo_timeline_status)}</td></tr>
        <tr><td style="padding:4px 8px; font-weight:bold;">照片总数</td><td>${t.total_photos || 0}</td></tr>
      </table>
      ${canEdit ? `<div style="margin-top:0.5rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
        <button type="button" class="secondary" id="discoverPhotosBtn">发现照片</button>
        ${t.total_photos > 0 ? `<button type="button" class="secondary" id="confirmTimelineBtn">确认时间线</button>` : ""}
        <button type="button" class="secondary" id="saveTimelineBtn">保存时间修改</button>
      </div>` : ""}
    `;

    if (canEdit) {
      // discoverPhotosBtn is handled by the delegated listener at the top of
      // this script (it survives re-renders). Other buttons bind directly.
      document.getElementById("confirmTimelineBtn")?.addEventListener("click", () => confirmTimeline(false));
      document.getElementById("saveTimelineBtn")?.addEventListener("click", saveTimeline);
    }
  }

  async function saveTimeline() {
    const body = {
      arrival_time: document.getElementById("arrivalTimeInput")?.value || null,
      departure_time: document.getElementById("departureTimeInput")?.value || null,
      draft_version: currentDraftVersion,
    };
    try {
      const result = await apiPost(`/draft/${draftId}/update-arrival-departure`, body);
      currentDraftVersion = result.draft_version;
      showStatus("时间已更新", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`保存失败: ${err.message}`, "error");
      }
    }
  }

  // ─── Phase 4/5/3 Actions: Discover / Confirm / Classify / Recalc ──────

  async function discoverPhotos() {
    if (isMutating) return;
    isMutating = true;
    const btn = document.getElementById("discoverPhotosBtn");
    if (btn) { btn.disabled = true; btn.textContent = "扫描中..."; }
    try {
      const result = await apiPost(`/draft/${draftId}/discover-photos`, { draft_version: currentDraftVersion });
      currentDraftVersion = result.draft_version;
      if (result.photo_count > 0) {
        showStatus(`照片扫描完成，发现 ${result.photo_count} 张照片，已生成时间线候选`, "success");
      } else {
        showStatus("未发现照片：请确认工单号与日报日期对应的照片目录存在（shared-photos/<工单号>/pictures/<日期>/）", "error");
      }
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`发现照片失败: ${err.message}`, "error");
      }
    } finally {
      isMutating = false;
    }
  }

  async function confirmTimeline(force) {
    if (isMutating) return;
    isMutating = true;
    try {
      const result = await apiPost(`/draft/${draftId}/confirm-photo-timeline`, { draft_version: currentDraftVersion, force: force || false });
      currentDraftVersion = result.draft_version;
      showStatus("时间线已确认，到达/离场时间已应用", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        const msg = String(err.message || "");
        if (!force && (msg.includes("force") || msg.includes("确认") || msg.includes("时间线"))) {
          if (window.confirm("时间线状态需要强制确认（照片时间可能不完整）。是否继续？")) {
            return confirmTimeline(true);
          }
        }
        showStatus(`确认时间线失败: ${msg}`, "error");
      }
    } finally {
      isMutating = false;
    }
  }

  async function classifyPhotos() {
    if (isMutating) return;
    isMutating = true;
    const btn = document.getElementById("classifyPhotosBtn");
    if (btn) { btn.disabled = true; btn.textContent = "分类中..."; }
    try {
      const result = await apiPost(`/draft/${draftId}/classify-photos`, { draft_version: currentDraftVersion });
      currentDraftVersion = result.draft_version;
      showStatus("照片分类完成", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`分类失败: ${err.message}`, "error");
      }
    } finally {
      isMutating = false;
    }
  }

  async function recalcMileage() {
    if (isMutating) return;
    isMutating = true;
    const btn = document.getElementById("recalcMileageBtn");
    if (btn) { btn.disabled = true; btn.textContent = "计算中..."; }
    try {
      const result = await apiPost(`/draft/${draftId}/recalculate-mileage`, { draft_version: currentDraftVersion });
      currentDraftVersion = result.draft_version;
      showStatus(result.message || "里程计算完成", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        const missing = (err.data && err.data.missing) || [];
        let msg = `里程计算失败: ${err.message}`;
        if (missing.length) {
          msg += " — 缺少: " + missing.join(", ");
        }
        showStatus(msg, "error");
      }
    } finally {
      isMutating = false;
    }
  }

  function renderSafetyPhoto() {
    const s = currentPreview.safety_photo || {};
    const canEdit = currentPreview.status === "draft";

    if (!s.selected_photo_id) {
      document.getElementById("safetyPhotoSection").innerHTML = `
        <p class="muted-line">未选择安全自检照片。</p>
        ${s.candidates_count ? `<p class="muted-line">候选照片: ${s.candidates_count} 张</p>` : ""}
      `;
      return;
    }

    document.getElementById("safetyPhotoSection").innerHTML = `
      <div style="display:flex; gap:1rem; align-items:flex-start;">
        <img src="/api/ai/daily-report/draft/${draftId}/photo/${s.selected_photo_id}" alt="Safety Photo" style="max-width:300px; border-radius:4px; cursor:pointer;" onclick="window.openImagePreview(this.src)">
        <div>
          <p><strong>来源:</strong> ${s.selected_source || "unknown"}</p>
          <p><strong>置信度:</strong> ${s.confidence != null ? (s.confidence * 100).toFixed(1) + "%" : "-"}</p>
          <p><strong>分类:</strong> ${s.sub_category || "-"}</p>
          <p><strong>候选数:</strong> ${s.candidates_count}</p>
        </div>
      </div>
    `;
  }

  function renderServicePhotos() {
    const s = currentPreview.service_photos || {};
    const selected = s.selected || [];
    const canEdit = currentPreview.status === "draft";

    if (!selected.length) {
      document.getElementById("servicePhotosSection").innerHTML = `
        <p class="muted-line">未选择施工照片。</p>
        ${s.candidates_count ? `<p class="muted-line">候选照片: ${s.candidates_count} 张</p>` : ""}
        ${canEdit ? `<button type="button" class="secondary" style="margin-top:0.5rem;" id="classifyPhotosBtn">AI 分类照片</button>` : ""}
      `;
      document.getElementById("classifyPhotosBtn")?.addEventListener("click", classifyPhotos);
      return;
    }

    let html = `<p class="muted-line" style="margin-bottom:0.5rem;">已选 ${selected.length}/10 张 | 候选 ${s.candidates_count} 张</p>`;
    html += '<div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap:0.5rem;">';
    selected.forEach((photo) => {
      html += `
        <div style="border:1px solid #e5e7eb; border-radius:4px; overflow:hidden;">
          <img src="/api/ai/daily-report/draft/${draftId}/photo/${photo.photo_id}" alt="Service Photo" style="width:100%; height:120px; object-fit:cover; cursor:pointer;" onclick="window.openImagePreview(this.src)">
          <div style="padding:4px; font-size:0.75rem;">
            <div>${photo.sub_category || "-"}</div>
            <div class="muted-line">${photo.selected_source || "ai"} ${photo.confidence != null ? (photo.confidence * 100).toFixed(0) + "%" : ""}</div>
          </div>
        </div>
      `;
    });
    html += "</div>";

    html += canEdit ? '<div style="margin-top:0.75rem;"><button type="button" class="secondary" id="classifyPhotosBtn">AI 分类照片</button></div>' : "";
    document.getElementById("servicePhotosSection").innerHTML = html;
    document.getElementById("classifyPhotosBtn")?.addEventListener("click", classifyPhotos);
  }

  function renderWorkItems() {
    const items = currentPreview.work_items || [];
    const canEdit = currentPreview.status === "draft";

    if (!items.length) {
      document.getElementById("workItemsSection").innerHTML = '<p class="muted-line">未填写施工内容。</p>';
      return;
    }

    let html = '<table style="width:100%; border-collapse:collapse;">';
    html += "<tr><th style='text-align:left; padding:4px 8px; border-bottom:1px solid #e5e7eb;'>设备</th><th style='text-align:left; padding:4px 8px; border-bottom:1px solid #e5e7eb;'>动作</th><th style='text-align:left; padding:4px 8px; border-bottom:1px solid #e5e7eb;'>保险丝</th><th style='text-align:left; padding:4px 8px; border-bottom:1px solid #e5e7eb;'>来源</th></tr>";
    items.forEach((item) => {
      html += `<tr>
        <td style="padding:4px 8px; border-bottom:1px solid #f3f4f6;">${escapeHtml(item.equipment || "-")}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #f3f4f6;">${escapeHtml(item.action || "-")}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #f3f4f6;">${item.fuse_number != null ? "#" + item.fuse_number : "-"}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #f3f4f6;"><span class="muted-line">${item.provenance || "unknown"}</span></td>
      </tr>`;
    });
    html += "</table>";

    document.getElementById("workItemsSection").innerHTML = html;
  }

  function renderVerification() {
    const checklist = currentPreview.verification_checklist || [];
    if (!checklist.length) {
      document.getElementById("verificationSection").innerHTML = '<p class="muted-line">无验证项。</p>';
      return;
    }

    const statusIcons = { ready: "✓", warning: "⚠", missing: "✗" };
    const statusColors = { ready: "color:#059669;", warning: "color:#d97706;", missing: "color:#dc2626;" };

    let html = '<ul style="list-style:none; padding:0;">';
    checklist.forEach((item) => {
      const icon = statusIcons[item.status] || "?";
      const color = statusColors[item.status] || "";
      html += `<li style="padding:4px 0; border-bottom:1px solid #f3f4f6;">
        <span style="${color} font-weight:bold;">${icon}</span>
        <strong>${escapeHtml(item.field)}</strong>: ${escapeHtml(item.message)}
        <span class="muted-line" style="font-size:0.75rem;">[${item.status}]</span>
      </li>`;
    });
    html += "</ul>";

    document.getElementById("verificationSection").innerHTML = html;
  }

  // ─── Phase 7: Validation Result ────────────────────────────────────────

  function renderValidation() {
    const v = currentPreview.validation_result;
    const el = document.getElementById("validationSection");
    if (!v) {
      el.innerHTML = '<p class="muted-line">无校验结果。</p>';
      return;
    }

    const canEdit = currentPreview.status === "draft";
    const sevBadge = {
      error: '<span style="background:#fef2f2;color:#dc2626;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:bold;">ERROR</span>',
      warning: '<span style="background:#fffbeb;color:#d97706;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:bold;">WARNING</span>',
      info: '<span style="background:#eff6ff;color:#2563eb;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:bold;">INFO</span>',
    };

    const validBadge = v.is_valid
      ? '<span style="background:#ecfdf5;color:#059669;padding:4px 12px;border-radius:4px;font-weight:bold;">✓ 有效</span>'
      : '<span style="background:#fef2f2;color:#dc2626;padding:4px 12px;border-radius:4px;font-weight:bold;">✗ 无效</span>';
    const proceedBadge = v.can_proceed
      ? '<span style="background:#ecfdf5;color:#059669;padding:4px 12px;border-radius:4px;font-weight:bold;">可继续</span>'
      : '<span style="background:#fef2f2;color:#dc2626;padding:4px 12px;border-radius:4px;font-weight:bold;">需修复</span>';

    let html = `
      <div style="margin-bottom:1rem; display:flex; gap:1rem; align-items:center; flex-wrap:wrap;">
        ${validBadge} ${proceedBadge}
        <span class="muted-line" style="font-size:0.8rem;">
          ERROR: ${v.error_count} | WARNING: ${v.warning_count} | INFO: ${v.info_count}
          | Engine v${v.engine_version || "?"} | 指纹: ${(v.validation_fingerprint || "").substring(0, 12)}...
        </span>
      </div>
    `;

    const issues = v.issues || [];
    if (!issues.length) {
      html += '<p style="color:#059669;">✓ 未发现任何问题。</p>';
    } else {
      html += '<ul style="list-style:none; padding:0;">';
      issues.forEach((issue) => {
        const badge = sevBadge[issue.severity] || issue.severity;
        const ackStyle = issue.acknowledged
          ? 'background:#ecfdf5;color:#059669;padding:2px 6px;border-radius:3px;font-size:0.7rem;'
          : '';
        const ackLabel = issue.acknowledged ? ' ✓ 已确认' : '';
        const ackBtn = (issue.severity === "warning" && issue.acknowledgement_required && !issue.acknowledged && canEdit)
          ? `<button type="button" class="secondary" style="font-size:0.75rem;padding:2px 8px;margin-left:8px;" data-ack-issue="${escapeHtml(issue.issue_key)}">确认</button>`
          : '';
        html += `<li style="padding:6px 0; border-bottom:1px solid #f3f4f6;">
          ${badge}
          <strong>${escapeHtml(issue.rule_id)}</strong>
          <span class="muted-line" style="font-size:0.75rem;">[${escapeHtml(issue.category)}]</span>
          ${ackLabel ? `<span style="${ackStyle}">${ackLabel}</span>` : ''}
          <br>
          <span style="font-size:0.9rem;">${escapeHtml(issue.message)}</span>
          <span class="muted-line" style="font-size:0.7rem; display:block; margin-top:2px;">
            key: ${escapeHtml(issue.issue_key)} | subject: ${escapeHtml(issue.subject_type)}/${escapeHtml(issue.subject_id)}
          </span>
          ${ackBtn}
        </li>`;
      });
      html += '</ul>';
    }

    el.innerHTML = html;

    // Bind acknowledge buttons
    if (canEdit) {
      document.querySelectorAll("[data-ack-issue]").forEach((btn) => {
        btn.addEventListener("click", () => acknowledgeWarning(btn.dataset.ackIssue));
      });
    }
  }

  async function acknowledgeWarning(issueKey) {
    const draftVersion = currentDraftVersion;
    try {
      const data = await apiPost(`/draft/${draftId}/acknowledge`, {
        issue_key: issueKey,
        draft_version: draftVersion,
      });
      if (data.ok) {
        currentPreview.validation_result = data.validation;
        currentDraftVersion = data.draft_version;
        currentPreview.draft_version = data.draft_version;
        renderValidation();
      } else {
        showStatus("确认失败: " + (data.error || "未知错误"), "error");
      }
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus("确认失败: " + err.message, "error");
      }
    }
  }

  function renderProvenance() {
    const ai = currentPreview.ai_metadata || {};
    document.getElementById("provenanceSection").innerHTML = `
      <table style="width:100%; font-size:0.9rem;">
        <tr><td style="padding:4px 8px; color:#6b7280;">AI 生成</td><td>${ai.ai_generated ? "是" : "否"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">文本 AI 模型</td><td>${escapeHtml(ai.ai_model || "-")}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">Vision 模型</td><td>${escapeHtml(ai.vision_model || "-")}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">Vision 分析版本</td><td>${ai.vision_analysis_version || "-"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">AI 创建时间</td><td>${formatDate(ai.ai_created_at)}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">AI 生成字段</td><td>${(ai.fields_ai_generated || []).join(", ") || "-"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">用户修改字段</td><td>${(ai.fields_user_modified || []).join(", ") || "-"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">需验证字段</td><td>${(ai.verification_required_fields || []).join(", ") || "无"}</td></tr>
      </table>
    `;
  }

  function renderAudit() {
    const a = currentPreview.audit || {};
    document.getElementById("auditSection").innerHTML = `
      <table style="width:100%; font-size:0.9rem;">
        <tr><td style="padding:4px 8px; color:#6b7280;">确认人</td><td>${a.confirmed_by || "-"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">确认时间</td><td>${formatDate(a.confirmed_at)}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">验证覆盖</td><td>${a.verification_override ? "是" : "否"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">覆盖字段</td><td>${(a.override_fields || []).join(", ") || "-"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">重新打开人</td><td>${a.reopened_by || "-"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">重新打开时间</td><td>${formatDate(a.reopened_at)}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">取消人</td><td>${a.cancelled_by || "-"}</td></tr>
        <tr><td style="padding:4px 8px; color:#6b7280;">取消时间</td><td>${formatDate(a.cancelled_at)}</td></tr>
      </table>
    `;
  }

  function renderActions() {
    const status = currentPreview.status;
    const actionsEl = document.getElementById("draftActions");
    let html = "";

    if (status === "draft") {
      html += `<button type="button" class="primary" id="confirmBtn">确认 Draft</button>`;
      html += `<button type="button" class="secondary" id="cancelBtn">取消 Draft</button>`;
    } else if (status === "confirmed") {
      html += `<button type="button" class="secondary" id="reopenBtn">重新打开</button>`;
      html += `<button type="button" class="secondary" id="cancelBtn">取消 Draft</button>`;
    }

    actionsEl.innerHTML = html;

    document.getElementById("confirmBtn")?.addEventListener("click", handleConfirm);
    document.getElementById("cancelBtn")?.addEventListener("click", handleCancel);
    document.getElementById("reopenBtn")?.addEventListener("click", handleReopen);
  }

  // ─── Actions ────────────────────────────────────────────────────────────

  async function handleConfirm() {
    const ai = currentPreview.ai_metadata || {};
    if (ai.has_verification_required) {
      // Show override dialog
      const list = document.getElementById("overrideFieldsList");
      list.innerHTML = (ai.verification_required_fields || []).map((f) => `<li>${escapeHtml(f)}</li>`).join("");
      overrideDialog.style.display = "flex";
      return;
    }
    doConfirm(false, []);
  }

  async function doConfirm(override, fields) {
    try {
      await apiPost(`/draft/${draftId}/confirm`, {
        override_verification: override,
        override_fields: fields,
        draft_version: currentDraftVersion,
      });
      showStatus("Draft 已确认", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`确认失败: ${err.message}`, "error");
      }
    }
  }

  async function handleCancel() {
    if (!confirm("确定要取消此 Draft 吗？取消后保留审计记录，但 Draft 将变为只读。")) return;
    try {
      await apiPost(`/draft/${draftId}/cancel`, { draft_version: currentDraftVersion });
      showStatus("Draft 已取消", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`取消失败: ${err.message}`, "error");
      }
    }
  }

  async function handleReopen() {
    try {
      await apiPost(`/draft/${draftId}/reopen`, { draft_version: currentDraftVersion });
      showStatus("Draft 已重新打开，可以继续修改", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`重新打开失败: ${err.message}`, "error");
      }
    }
  }

  // ─── 409 Conflict ───────────────────────────────────────────────────────

  function showConflict(data) {
    const details = document.getElementById("conflictDetails");
    details.textContent = data.error || "Draft 版本冲突。";
    conflictDialog.style.display = "flex";
  }

  document.getElementById("conflictReload")?.addEventListener("click", () => {
    conflictDialog.style.display = "none";
    loadPreview();
  });

  document.getElementById("conflictClose")?.addEventListener("click", () => {
    conflictDialog.style.display = "none";
  });

  // ─── Add Worker Dialog ─────────────────────────────────────────────────

  document.getElementById("addWorkerBtn")?.addEventListener("click", openAddWorkerDialog);
  document.getElementById("staffSearchBtn")?.addEventListener("click", searchStaff);
  document.getElementById("addWorkerClose")?.addEventListener("click", closeAddWorkerDialog);
  document.getElementById("staffSearchInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") searchStaff();
  });

  // ─── Override Dialog ────────────────────────────────────────────────────

  document.getElementById("overrideConfirm")?.addEventListener("click", () => {
    overrideDialog.style.display = "none";
    const ai = currentPreview.ai_metadata || {};
    doConfirm(true, ai.verification_required_fields || []);
  });

  document.getElementById("overrideCancel")?.addEventListener("click", () => {
    overrideDialog.style.display = "none";
  });

  // ─── Helpers ────────────────────────────────────────────────────────────

  function showStatus(message, type) {
    statusBanner.textContent = message;
    statusBanner.className = `flash ${type === "error" ? "error" : type === "success" ? "success" : ""}`;
    statusBanner.style.display = "block";
    setTimeout(() => { statusBanner.style.display = "none"; }, 5000);
  }

  function getRouteStatusBadge(status) {
    const styles = {
      success: "background:#d1fae5; color:#065f46;",
      not_calculated: "background:#f3f4f6; color:#374151;",
      verification_required: "background:#fef3c7; color:#92400e;",
      failed: "background:#fee2e2; color:#991b1b;",
    };
    const labels = {
      success: "已计算",
      not_calculated: "未计算",
      verification_required: "需确认",
      failed: "失败",
    };
    return `<span style="${styles[status] || ""} padding:2px 8px; border-radius:4px; font-size:0.8rem;">${labels[status] || status}</span>`;
  }

  function getTimelineStatusBadge(status) {
    const styles = {
      ready: "background:#d1fae5; color:#065f46;",
      not_scanned: "background:#f3f4f6; color:#374151;",
      no_photos: "background:#fee2e2; color:#991b1b;",
      insufficient_photos: "background:#fef3c7; color:#92400e;",
      suspicious: "background:#fef3c7; color:#92400e;",
      verification_required: "background:#fef3c7; color:#92400e;",
      failed: "background:#fee2e2; color:#991b1b;",
    };
    const labels = {
      ready: "正常",
      not_scanned: "未扫描",
      no_photos: "无照片",
      insufficient_photos: "照片不足",
      suspicious: "可疑",
      verification_required: "需确认",
      failed: "失败",
    };
    return `<span style="${styles[status] || ""} padding:2px 8px; border-radius:4px; font-size:0.8rem;">${labels[status] || status}</span>`;
  }

  function formatDate(iso) {
    if (!iso) return "-";
    try {
      const d = new Date(iso);
      return d.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch {
      return iso;
    }
  }

  function escapeHtml(str) {
    if (str == null) return "";
    const div = document.createElement("div");
    div.textContent = String(str);
    return div.innerHTML;
  }

  // Image preview helper (uses attachment-preview.js if available)
  window.openImagePreview = function (src) {
    // Try to use existing attachment preview lightbox
    const dialog = document.getElementById("imageAttachmentPreviewDialog");
    if (dialog) {
      const img = dialog.querySelector("img");
      if (img) {
        img.src = src;
        dialog.style.display = "flex";
      }
    } else {
      window.open(src, "_blank");
    }
  };

  // ─── Phase 8: Attachment Preparation ───────────────────────────────────

  function renderAttachmentPreparation() {
    const ap = currentPreview.attachment_preparation;
    const el = document.getElementById("attachmentPrepSection");
    if (!el) return;
    if (!ap) {
      el.innerHTML = '<p class="muted-line">无附件准备信息。</p>';
      return;
    }
    const m = ap.current;
    const status = currentPreview.status;
    const canMutate = status === "confirmed";
    let html = "";

    if (!m) {
      html += '<p class="muted-line">尚未生成附件清单。确认 Draft 后可准备附件。</p>';
    } else {
      const st = m.status;
      const styleMap = {
        ready: "background:#d1fae5; color:#065f46;",
        preparing: "background:#dbeafe; color:#1e40af;",
        verification_required: "background:#fef3c7; color:#92400e;",
        failed: "background:#fee2e2; color:#991b1b;",
        stale: "background:#f3f4f6; color:#6b7280;",
        cancelled: "background:#f3f4f6; color:#6b7280;",
      };
      const labelMap = {
        ready: "已就绪", preparing: "准备中", verification_required: "需核验",
        failed: "失败", stale: "已过期", cancelled: "已取消",
      };
      html += `<p><strong>Manifest:</strong> <span style="${styleMap[st] || ""} padding:2px 8px; border-radius:4px; font-size:0.8rem;">${labelMap[st] || st}</span>`;
      html += ` <span class="muted-line" style="font-size:0.8rem;">版本 ${m.draft_version} | 指纹 ${(m.manifest_fingerprint || "").substring(0, 12)}...</span></p>`;
      html += `<p class="muted-line" style="font-size:0.9rem;">物理文件 ${m.asset_count} | 来源 ${m.source_count} | 逻辑角色 ${m.role_count}</p>`;
      if (m.has_compliance_block) {
        html += `<p style="color:#92400e;">⚠ 合规审查待办（${m.compliance_summary.length} 项，均需人工 review，Phase 8 不会自动批准）</p>`;
      }
      if (m.assets && m.assets.length && st === "ready") {
        html += '<div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap:0.5rem;">';
        m.assets.forEach((a) => {
          html += `<div style="border:1px solid #e5e7eb; border-radius:4px; overflow:hidden;">
            <img src="/api/ai/daily-report/draft/${draftId}/manifest/${m.manifest_id}/asset/${a.asset_id}" alt="Prepared Asset" style="width:100%; height:100px; object-fit:cover; cursor:pointer;" onclick="window.openImagePreview(this.src)">
            <div style="padding:4px; font-size:0.7rem;" class="muted-line">${(a.prepared_sha256 || "").substring(0, 12)}... | ${a.file_size} B</div>
          </div>`;
        });
        html += "</div>";
      }
    }

    if (canMutate) {
      html += `<div style="margin-top:0.75rem;">`;
      html += `<button type="button" class="primary" id="prepareAttachmentsBtn">${m ? "重新准备附件" : "准备附件"}</button>`;
      if (m && m.status !== "ready") {
        html += `<button type="button" class="secondary" id="cancelManifestBtn" style="margin-left:0.5rem;">取消准备</button>`;
      }
      html += `</div>`;
      html += `<p class="muted-line" style="font-size:0.75rem; margin-top:0.4rem;">点击准备将执行服务端 Phase 7 校验并生成确定性指纹；重复准备相同内容会复用已有清单。</p>`;
    }

    el.innerHTML = html;

    document.getElementById("prepareAttachmentsBtn")?.addEventListener("click", handlePrepareAttachments);
    document.getElementById("cancelManifestBtn")?.addEventListener("click", handleCancelManifest);
  }

  async function handlePrepareAttachments() {
    try {
      const result = await apiPost(`/draft/${draftId}/prepare-attachments`, { draft_version: currentDraftVersion });
      showStatus("附件清单已准备完成", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`准备失败: ${err.message}`, "error");
      }
    }
  }



  // ─── Phase 9: Formal Save ────────────────────────────────────────────────

  function renderFormalSave() {
    const fs = currentPreview.formal_save;
    const el = document.getElementById("formalSaveSection");
    if (!el) return;
    if (!fs) {
      el.innerHTML = '<p class="muted-line">无正式保存信息。</p>';
      return;
    }
    const status = currentPreview.status;
    const m = currentPreview.attachment_preparation && currentPreview.attachment_preparation.current;
    const canMutate = fs.allowed && status === "confirmed" && m && m.status === "ready";
    let html = "";

    if (fs.status === "saved" && fs.service_report_id) {
      html += `<p style="color:#065f46;"><strong>Formal Report Created</strong></p>`;
      html += `<p>Report ID: <strong>${fs.service_report_id}</strong></p>`;
      html += `<p><a href="/edit_service_report/${fs.service_report_id}" target="_blank" class="primary" style="display:inline-block; padding:0.4rem 0.9rem; text-decoration:none;">查看正式日报</a></p>`;
      if (fs.commit_status) {
        html += `<p class="muted-line" style="font-size:0.75rem;">commit status: ${fs.commit_status}${fs.committed_at ? " | " + fs.committed_at : ""}</p>`;
      }
    } else if (fs.status === "commit_committing") {
      html += '<p style="color:#1e40af;">正式保存正在进行中，请稍候刷新。</p>';
    } else if (fs.status === "commit_failed") {
      html += '<p style="color:#991b1b;">上次正式保存失败（已恢复清理），可重试。</p>';
    } else if (fs.status === "not_saved") {
      if (!m) {
        html += '<p class="muted-line">请先准备附件清单（Phase 8）后再正式保存。</p>';
      } else if (m.status === "verification_required" || m.status === "failed") {
        html += '<p class="muted-line">附件清单存在完整性/合规问题，需先处理后再正式保存。</p>';
      } else if (m.status === "ready") {
        html += '<p class="muted-line">附件清单已就绪，可执行正式保存。</p>';
      } else {
        html += '<p class="muted-line">附件清单未就绪，无法正式保存。</p>';
      }
    } else {
      html += '<p class="muted-line">当前状态不可正式保存。</p>';
    }

    if (canMutate) {
      html += `<div style="margin-top:0.75rem;">`;
      html += `<button type="button" class="primary" id="formalSaveBtn">正式保存</button>`;
      html += `</div>`;
      html += `<p class="muted-line" style="font-size:0.75rem; margin-top:0.4rem;">正式保存是确定性事务：仅消费冻结的 Draft + 已就绪清单，不会重新调用 AI/路线/照片识别，也不会重复生成正式日报。</p>`;
    }

    el.innerHTML = html;
    document.getElementById("formalSaveBtn")?.addEventListener("click", handleFormalSave);
  }

  async function handleFormalSave() {
    const m = currentPreview.attachment_preparation && currentPreview.attachment_preparation.current;
    if (!m) return;
    if (!confirm("确认正式保存为正式日报？此操作不可撤销，且不会生成两份正式日报。")) return;
    try {
      const result = await apiPost(`/draft/${draftId}/formal-save`, {
        draft_version: currentDraftVersion,
        manifest_id: m.manifest_id,
      });
      showStatus("正式日报已创建", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`正式保存失败: ${err.message}`, "error");
      }
    }
  }

  async function handleCancelManifest() {
    const m = currentPreview.attachment_preparation && currentPreview.attachment_preparation.current;
    if (!m) return;
    if (!confirm("确定取消此附件清单？仅删除 Phase 8 staging 文件，原始照片与佐证不受影响。")) return;
    try {
      const resp = await fetch(`${apiBase}/draft/${draftId}/manifest/${m.manifest_id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.status === 409) { showConflict(data); throw new Error("version_conflict"); }
      if (!resp.ok) { throw new Error(data.error || `HTTP ${resp.status}`); }
      showStatus("附件清单已取消", "success");
      await loadPreview();
    } catch (err) {
      if (err.message !== "version_conflict") {
        showStatus(`取消失败: ${err.message}`, "error");
      }
    }
  }

  // Initial load
  loadPreview();
})();
