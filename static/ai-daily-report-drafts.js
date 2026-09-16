/* AI Daily Report - Draft List (Review Center)
 * Phase 6: List, filter, pagination, 409 handling
 */
(function () {
  "use strict";

  const apiBase = window.aiDailyReportApiBase || "/api/ai/daily-report";
  const csrfToken = window.csrfToken || "";

  let currentPage = 1;
  const perPage = 20;
  let currentFilters = {};

  // DOM elements
  const draftsLoading = document.getElementById("draftsLoading");
  const draftsError = document.getElementById("draftsError");
  const draftsContainer = document.getElementById("draftsContainer");
  const pagination = document.getElementById("pagination");
  const conflictDialog = document.getElementById("conflictDialog");

  // Status badge styles
  const statusStyles = {
    draft: "background:#fef3c7; color:#92400e;",
    confirmed: "background:#d1fae5; color:#065f46;",
    cancelled: "background:#fee2e2; color:#991b1b;",
    saved: "background:#dbeafe; color:#1e40af;",
  };

  const statusLabels = {
    draft: "编辑中",
    confirmed: "已确认",
    cancelled: "已取消",
    saved: "已保存",
  };

  async function fetchDrafts(page) {
    draftsLoading.style.display = "block";
    draftsError.style.display = "none";
    draftsContainer.innerHTML = "";

    const params = new URLSearchParams();
    params.set("page", page);
    params.set("per_page", perPage);
    if (currentFilters.status) params.set("status", currentFilters.status);
    if (currentFilters.date_from) params.set("date_from", currentFilters.date_from);
    if (currentFilters.date_to) params.set("date_to", currentFilters.date_to);
    if (currentFilters.service_order_id) params.set("service_order_id", currentFilters.service_order_id);

    try {
      const resp = await fetch(`${apiBase}/drafts?${params.toString()}`);
      if (resp.status === 409) {
        showConflict(await resp.json());
        return;
      }
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      renderDrafts(data.drafts || []);
      renderPagination(data);
    } catch (err) {
      draftsError.textContent = `加载失败: ${err.message}`;
      draftsError.style.display = "block";
    } finally {
      draftsLoading.style.display = "none";
    }
  }

  function renderDrafts(drafts) {
    if (!drafts.length) {
      draftsContainer.innerHTML = '<p class="muted-line" style="padding:1rem;">没有找到 Draft。</p>';
      return;
    }

    const html = drafts.map((d) => {
      const statusStyle = statusStyles[d.status] || "";
      const statusLabel = statusLabels[d.status] || d.status;
      const verificationBadge = d.has_verification_required
        ? `<span style="background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px; font-size:0.8rem;">需确认</span>`
        : "";
      // Soft-delete (cancel) is offered only for editable temporary reports.
      // cancelled/saved drafts are read-only and keep their audit trail.
      const canDelete = d.status === "draft" || d.status === "confirmed";
      const deleteBtn = canDelete
        ? `<button type="button" class="secondary" style="margin-top:0.5rem; display:block;" data-delete-draft="${d.id}" data-delete-label="${(d.order_number || "")}">删除</button>`
        : "";
      return `
        <div class="draft-card" style="border:1px solid #e5e7eb; border-radius:8px; padding:1rem; margin-bottom:0.75rem; display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;">
          <div style="flex:1;">
            <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.5rem;">
              <strong>${d.order_number || "未知工单"}</strong>
              <span style="${statusStyle} padding:2px 8px; border-radius:4px; font-size:0.8rem;">${statusLabel}</span>
              ${verificationBadge}
            </div>
            <div class="muted-line" style="font-size:0.9rem; margin-bottom:0.25rem;">
              日期: ${d.report_date || "未知"} | ${d.client_name || ""}
            </div>
            <div class="muted-line" style="font-size:0.85rem;">
              工作人员: ${d.worker_count} 人 | 照片: ${d.photo_count} 张 | 更新于: ${formatDate(d.updated_at)}
            </div>
            ${d.verification_fields && d.verification_fields.length
              ? `<div class="muted-line" style="font-size:0.8rem; color:#92400e; margin-top:0.25rem;">待确认: ${d.verification_fields.join(", ")}</div>`
              : ""}
          </div>
          <div>
            <a href="/ai-daily-report/drafts/${d.id}" class="button primary" style="text-decoration:none;">查看详情</a>
            ${deleteBtn}
          </div>
        </div>
      `;
    }).join("");

    draftsContainer.innerHTML = html;
  }

  // Hard-delete a temporary draft from the Review Center list (cancel API).
  async function handleDeleteDraft(id, label) {
    if (!confirm(`确定删除「${label}」的临时日报吗？删除后不可恢复。`)) return;
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    try {
      const resp = await fetch(`${apiBase}/draft/${id}/cancel`, {
        method: "POST",
        headers,
        body: JSON.stringify({}),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      fetchDrafts(currentPage);
    } catch (err) {
      draftsError.textContent = `删除失败: ${err.message}`;
      draftsError.style.display = "block";
    }
  }

  draftsContainer.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-delete-draft]");
    if (!btn) return;
    handleDeleteDraft(btn.getAttribute("data-delete-draft"), btn.getAttribute("data-delete-label") || "");
  });

  function renderPagination(data) {
    if (!data.total || data.total <= perPage) {
      pagination.innerHTML = "";
      return;
    }

    const totalPages = data.total_pages || 1;
    let html = `<span class="muted-line">共 ${data.total} 条，第 ${data.page}/${totalPages} 页</span>`;

    if (data.page > 1) {
      html += `<button type="button" class="secondary" data-page="${data.page - 1}">上一页</button>`;
    }
    if (data.page < totalPages) {
      html += `<button type="button" class="secondary" data-page="${data.page + 1}">下一页</button>`;
    }

    pagination.innerHTML = html;

    pagination.querySelectorAll("button[data-page]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentPage = parseInt(btn.dataset.page, 10);
        fetchDrafts(currentPage);
      });
    });
  }

  function formatDate(iso) {
    if (!iso) return "未知";
    try {
      const d = new Date(iso);
      return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch {
      return iso;
    }
  }

  function showConflict(data) {
    const details = document.getElementById("conflictDetails");
    details.textContent = data.error || "Draft 版本冲突。";
    conflictDialog.style.display = "flex";
  }

  // ─── New AI Daily Report ─────────────────────────────────────────────
  const newDraftDialog = document.getElementById("newDraftDialog");
  const newDraftOrderSearch = document.getElementById("newDraftOrderSearch");
  const newDraftOrderOptions = document.getElementById("newDraftOrderOptions");
  const newDraftDate = document.getElementById("newDraftDate");
  const newDraftMessage = document.getElementById("newDraftMessage");
  const newDraftError = document.getElementById("newDraftError");

  let selectedOrderId = null;
  let orderSearchTimer = null;

  function openNewDraftDialog() {
    selectedOrderId = null;
    newDraftOrderSearch.value = "";
    newDraftDate.value = new Date().toISOString().slice(0, 10);
    newDraftMessage.value = "";
    newDraftError.style.display = "none";
    newDraftOrderOptions.innerHTML = "";
    newDraftDialog.style.display = "flex";
    newDraftOrderSearch.focus();
    searchOrders("");
  }

  function closeNewDraftDialog() {
    newDraftDialog.style.display = "none";
  }

  function renderOrderOptions(orders) {
    if (!orders.length) {
      newDraftOrderOptions.innerHTML = '<p class="muted-line" style="padding:0.5rem;">没有找到工单。</p>';
      return;
    }
    newDraftOrderOptions.innerHTML = orders.map((o) => {
      const sel = selectedOrderId === o.id ? "border:2px solid #1d4ed8; background:#eff6ff;" : "";
      return `<button type="button" class="order-option" data-id="${o.id}"
        style="display:block; width:100%; text-align:left; padding:0.6rem; border:1px solid #e5e7eb; border-radius:6px; margin-bottom:0.4rem; cursor:pointer; ${sel}">
        <strong>${o.order_number}</strong>
        <div class="muted-line" style="font-size:0.85rem;">${o.client_name || ""}${o.site_address ? " — " + o.site_address : ""}</div>
      </button>`;
    }).join("");
    newDraftOrderOptions.querySelectorAll(".order-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedOrderId = parseInt(btn.dataset.id, 10);
        newDraftOrderSearch.value = btn.querySelector("strong").textContent;
        renderOrderOptions(orders);
      });
    });
  }

  async function searchOrders(q) {
    try {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      params.set("limit", "20");
      const resp = await fetch(`${apiBase}/service-orders?${params.toString()}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      renderOrderOptions(data.orders || []);
    } catch (err) {
      newDraftOrderOptions.innerHTML = `<p class="muted-line" style="padding:0.5rem;">加载失败: ${err.message}</p>`;
    }
  }

  newDraftOrderSearch.addEventListener("input", () => {
    clearTimeout(orderSearchTimer);
    const q = newDraftOrderSearch.value.trim();
    orderSearchTimer = setTimeout(() => searchOrders(q), 300);
  });

  async function submitNewDraft() {
    newDraftError.style.display = "none";
    if (!selectedOrderId) {
      newDraftError.textContent = "请选择一个工单。";
      newDraftError.style.display = "block";
      return;
    }
    const date = newDraftDate.value;
    const message = newDraftMessage.value.trim();
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

    const btn = document.getElementById("newDraftSubmit");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "创建中…";
    try {
      let draftId = null;
      if (message) {
        const resp = await fetch(`${apiBase}/chat`, {
          method: "POST",
          headers,
          body: JSON.stringify({ message, service_order_id: selectedOrderId, report_date: date }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          throw new Error(data.error || `HTTP ${resp.status}`);
        }
        draftId = data.draft_id;
      } else {
        const resp = await fetch(`${apiBase}/draft`, {
          method: "POST",
          headers,
          body: JSON.stringify({ service_order_id: selectedOrderId, report_date: date }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          throw new Error(data.error || `HTTP ${resp.status}`);
        }
        draftId = data.draft_id;
      }
      if (!draftId) throw new Error("创建失败：未返回 draft_id");
      window.location.href = `/ai-daily-report/drafts/${draftId}`;
    } catch (err) {
      newDraftError.textContent = `创建失败: ${err.message}`;
      newDraftError.style.display = "block";
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  document.getElementById("newDraftBtn")?.addEventListener("click", openNewDraftDialog);
  document.getElementById("newDraftCancel")?.addEventListener("click", closeNewDraftDialog);
  document.getElementById("newDraftSubmit")?.addEventListener("click", submitNewDraft);
  newDraftDialog?.addEventListener("click", (e) => {
    if (e.target === newDraftDialog) closeNewDraftDialog();
  });

  // Event listeners
  document.getElementById("refreshDrafts")?.addEventListener("click", () => fetchDrafts(currentPage));

  document.getElementById("applyFilters")?.addEventListener("click", () => {
    currentFilters = {
      status: document.getElementById("filterStatus").value,
      date_from: document.getElementById("filterDateFrom").value,
      date_to: document.getElementById("filterDateTo").value,
      service_order_id: document.getElementById("filterOrder").value,
    };
    currentPage = 1;
    fetchDrafts(1);
  });

  document.getElementById("clearFilters")?.addEventListener("click", () => {
    document.getElementById("filterStatus").value = "";
    document.getElementById("filterDateFrom").value = "";
    document.getElementById("filterDateTo").value = "";
    document.getElementById("filterOrder").value = "";
    currentFilters = {};
    currentPage = 1;
    fetchDrafts(1);
  });

  document.getElementById("conflictReload")?.addEventListener("click", () => {
    conflictDialog.style.display = "none";
    fetchDrafts(currentPage);
  });

  document.getElementById("conflictClose")?.addEventListener("click", () => {
    conflictDialog.style.display = "none";
  });

  // Initial load
  fetchDrafts(1);
})();
