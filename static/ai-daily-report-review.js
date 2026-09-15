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

  // DOM
  const statusBanner = document.getElementById("statusBanner");
  const conflictDialog = document.getElementById("conflictDialog");
  const overrideDialog = document.getElementById("overrideDialog");

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
      throw new Error("version_conflict");
    }
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    return resp.json();
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
    if (!workers.length) {
      document.getElementById("workersSection").innerHTML = '<p class="muted-line">未添加工作人员。</p>';
      return;
    }

    const canEdit = currentPreview.status === "draft";
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
          ${canEdit ? `<button type="button" class="secondary" style="margin-top:0.5rem;" data-save-worker="${idx}">保存修改</button>` : ""}
        </div>
      `;
    }).join("");

    document.getElementById("workersSection").innerHTML = html;

    // Bind save buttons
    if (canEdit) {
      document.querySelectorAll("[data-save-worker]").forEach((btn) => {
        btn.addEventListener("click", () => saveWorker(parseInt(btn.dataset.saveWorker, 10)));
      });
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
      document.getElementById("mileageEvidenceSection").innerHTML = '<p class="muted-line">无自驾人员。</p>';
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

    document.getElementById("mileageEvidenceSection").innerHTML = html;
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
      ${canEdit ? `<button type="button" class="secondary" style="margin-top:0.5rem;" id="saveTimelineBtn">保存时间修改</button>` : ""}
    `;

    if (canEdit) {
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

    if (!selected.length) {
      document.getElementById("servicePhotosSection").innerHTML = `
        <p class="muted-line">未选择施工照片。</p>
        ${s.candidates_count ? `<p class="muted-line">候选照片: ${s.candidates_count} 张</p>` : ""}
      `;
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

    document.getElementById("servicePhotosSection").innerHTML = html;
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

  // Initial load
  loadPreview();
})();
