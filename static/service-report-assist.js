// 工作日报「辅助填写」：只读预览 -> 用户勾选 -> 应用到表单。
//
// 设计要点：
// 1. 照片走 service-report.js 已有的 NAS 通道（nasSelections + renderSelectedNasPhotos），
//    不新增上传/落库路径，保证「服务器选择」和「辅助填写」两种来源行为一致。
// 2. 只填空值：里程、交通时长、出发地如果人工已经填过（非 0 非空），不覆盖。
// 3. 一切都等用户点「应用到表单」才写进表单（保存前仍可撤销）。

const assistPlanDialog = document.getElementById("assistPlanDialog");
const assistFillButton = document.getElementById("assistFillBtn");
const assistPlanBody = document.getElementById("assistPlanBody");
const assistPlanSummary = document.getElementById("assistPlanSummary");
const applyAssistButton = document.getElementById("applyAssistPlan");
const closeAssistDialogButton = document.getElementById("closeAssistPlanDialog");
const cancelAssistButton = document.getElementById("cancelAssistPlan");
const mileageEvidenceButton = document.getElementById("generateMileageEvidenceBtn");
const mileageEvidenceStatus = document.getElementById("mileageEvidenceStatus");

let assistPlan = null;

const ASSIST_CATEGORY_LABELS = {
  arrival: "现场到达时间照片",
  departure: "离开现场时间照片",
  self_check: "自检照片",
  site: "现场服务照片",
};
const ASSIST_ORIGIN_SOURCE_LABELS = {
  user_input: "已手填",
  employee_default: "员工主数据地址",
  report_fallback: "日报出发地址",
  none: "未提供",
};

function assistWorkerRows() {
  const table = document.getElementById("serviceWorkersTable");
  if (!table) return [];
  return [...table.querySelectorAll("tbody tr")];
}

function collectAssistWorkers() {
  return assistWorkerRows()
    .map((row) => ({
      row,
      user_id: row.querySelector('[name="worker_user_id"]')?.value || "",
      travel_mode: row.querySelector('[name="worker_travel_mode"]')?.value || "",
      trip_type: row.querySelector('[name="worker_trip_type"]')?.value || "",
      current_origin: row.querySelector('[name="worker_origin"]')?.value || "",
    }))
    .filter((item) => item.user_id);
}

function fireChange(input) {
  input?.dispatchEvent(new Event("input", { bubbles: true }));
  input?.dispatchEvent(new Event("change", { bubbles: true }));
}

function isBlankNumber(value) {
  return value === "" || value === null || Number.isNaN(Number(value)) || Number(value) === 0;
}

function fillNumber(input, value) {
  if (value === null || value === undefined) return;
  if (!isBlankNumber(input.value)) return; // 人工已填，不覆盖
  input.value = String(value);
  fireChange(input);
}

function fillText(input, value) {
  if (!value) return;
  if ((input.value || "").trim()) return; // 人工已填，不覆盖
  input.value = value;
  fireChange(input);
}

function setTimePart(select, value) {
  if (!select || !value) return;
  select.value = value;
  fireChange(select);
}

function applyTime(prefix, value) {
  if (!value) return;
  const [hour, minute] = value.split(":");
  setTimePart(serviceReportForm?.elements[`${prefix}_hour`], hour);
  setTimePart(serviceReportForm?.elements[`${prefix}_minute`], minute);
}

function photoCheckbox(labelText, meta, dataset) {
  const wrapper = document.createElement("label");
  wrapper.className = "nas-photo-option";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = true;
  Object.entries(dataset).forEach(([key, value]) => {
    checkbox.dataset[key] = value;
  });
  const image = document.createElement("img");
  image.src = meta.thumbnail || "";
  image.alt = labelText;
  image.loading = "lazy";
  const caption = document.createElement("span");
  caption.textContent = `${labelText}${meta.capture_time ? ` · ${meta.capture_time.slice(11, 16)}` : " · 时间不明"}`;
  wrapper.append(checkbox, image, caption);
  return wrapper;
}

function renderSection(title, children, note, grid) {
  const section = document.createElement("section");
  section.className = "assist-plan-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.appendChild(heading);
  if (note) {
    const hint = document.createElement("p");
    hint.className = "muted-line";
    hint.textContent = note;
    section.appendChild(hint);
  }
  if (!children.length) {
    const empty = document.createElement("p");
    empty.className = "muted-line";
    empty.textContent = "没有可填写的内容。";
    section.appendChild(empty);
  } else if (grid) {
    const gridBox = document.createElement("div");
    gridBox.className = "nas-photo-browser";
    children.forEach((node) => gridBox.appendChild(node));
    section.appendChild(gridBox);
  } else {
    children.forEach((node) => section.appendChild(node));
  }
  return section;
}

function renderAssistPlan(plan) {
  assistPlanBody.replaceChildren();

  const photoPlan = plan.photo_plan || {};
  for (const [category, label] of Object.entries(ASSIST_CATEGORY_LABELS)) {
    const photos = (photoPlan.photos || {})[category] || [];
    let note = "";
    if (category === "site" && photoPlan.site_truncated) {
      note = `当天共 ${photoPlan.site_total} 张候选，已按拍摄时间取前 ${photos.length} 张（可在下方取消勾选）。`;
    }
    const children = photos.map((photo) =>
      photoCheckbox(photo.file_name || photo.relative_path, photo, {
        assistPhoto: "1",
        assistCategory: category,
        assistPath: photo.relative_path,
      })
    );
    assistPlanBody.appendChild(renderSection(label, children, note, true));
  }

  const timeChildren = [];
  [["arrival_time", "现场到达时间", photoPlan.arrival_time], ["departure_time", "离开现场时间", photoPlan.departure_time]]
    .forEach(([prefix, label, value]) => {
      if (!value) return;
      const wrapper = document.createElement("label");
      wrapper.className = "assist-plan-row";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = true;
      checkbox.dataset.assistTime = prefix;
      checkbox.dataset.assistTimeValue = value;
      const text = document.createElement("span");
      text.textContent = `${label}：${value}`;
      wrapper.append(checkbox, text);
      timeChildren.push(wrapper);
    });
  assistPlanBody.appendChild(renderSection("进出场时间", timeChildren));

  const workerNodes = (plan.workers || []).map((worker) => {
    const wrapper = document.createElement("label");
    wrapper.className = "assist-plan-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = worker.route_status === "success";
    checkbox.disabled = worker.route_status !== "success";
    checkbox.dataset.assistWorker = String(worker.user_id);
    const text = document.createElement("span");
    if (worker.route_status === "success") {
      text.textContent = `${worker.name}：${worker.origin} → ${plan.destination || "目的地"}，${worker.reported_miles} 英里 / ${Number(worker.travel_hours || 0).toFixed(1)} 小时（${
        worker.trip_type === "one_way" ? "单程" : "往返"
      }）`;
    } else {
      text.textContent = `${worker.name}：跳过（${
        {
          following_or_flight: "随行或飞机不算驾车里程",
          missing_origin: "缺少出发地",
          missing_destination: "缺少目的地",
          routes_unavailable: "未配置 Google Routes",
          skipped: "本次未计算路线",
        }[worker.reason] || worker.reason || "无法计算"
      }）`;
    }
    wrapper.append(checkbox, text);
    return wrapper;
  });
  assistPlanBody.appendChild(renderSection("员工出行", workerNodes));

  const warnings = plan.warnings || [];
  const warningTexts = {
    arrival_time_unknown: "进场照片取不到水印时间，未自动填写到达时间。",
    departure_time_unknown: "离场照片取不到水印时间，未自动填写离开时间。",
    worker_origin_missing: "部分员工没有出发地，请手动补充。",
    destination_missing: "没有目的地（场地地址），里程无法计算。",
    route_failed: "部分路线请求失败，相关里程未填写。",
    photo_time_enrichment_failed: "照片拍摄时间读取失败，已按文件名排序。",
  };
  if (warnings.length) {
    const list = document.createElement("ul");
    list.className = "assist-warnings";
    warnings
      .map((key) => warningTexts[key] || key)
      .forEach((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        list.appendChild(item);
      });
    assistPlanBody.appendChild(list);
  }

  if (photoPlan.status === "no_photos") {
    assistPlanSummary.textContent = "当天没有找到照片，仅填写了能算出的员工出行数据。";
  } else if (photoPlan.status === "failed") {
    assistPlanSummary.textContent = "照片读取失败，请稍后重试或手动选择照片。";
  } else {
    assistPlanSummary.textContent = "核对后应用到表单，取消勾选的项目不会被填写。";
  }
  applyAssistButton.disabled = false;
}

function selectedAssistPhotos() {
  const result = {};
  assistPlanBody.querySelectorAll("input[data-assist-photo]").forEach((checkbox) => {
    if (!checkbox.checked) return;
    const category = checkbox.dataset.assistCategory;
    const entry = (assistPlan?.photo_plan?.photos?.[category] || []).find(
      (photo) => photo.relative_path === checkbox.dataset.assistPath
    );
    if (!entry) return;
    result[category] = result[category] || [];
    result[category].push(entry);
  });
  return result;
}

function applyAssistPhotos(selections) {
  for (const [category, photos] of Object.entries(selections)) {
    if (!photos.length) continue;
    let selected = nasSelections.get(category);
    if (!(selected instanceof Map)) {
      selected = new Map();
      nasSelections.set(category, selected);
    }
    photos.forEach((photo) => {
      selected.set(photo.relative_path, {
        name: photo.file_name,
        thumbnail: photo.thumbnail,
        preview: photo.preview,
      });
    });
    renderSelectedNasPhotos(category);
  }
}

function applyAssistPlanToForm() {
  if (!assistPlan) return;

  applyAssistPhotos(selectedAssistPhotos());

  assistPlanBody.querySelectorAll("input[data-assist-time]").forEach((checkbox) => {
    if (checkbox.checked) applyTime(checkbox.dataset.assistTime, checkbox.dataset.assistTimeValue);
  });

  const byUserId = new Map((assistPlan.workers || []).map((worker) => [String(worker.user_id), worker]));
  assistPlanBody.querySelectorAll("input[data-assist-worker]").forEach((checkbox) => {
    if (!checkbox.checked) return;
    const worker = byUserId.get(checkbox.dataset.assistWorker);
    const row = assistWorkerRows().find(
      (candidate) => candidate.querySelector('[name="worker_user_id"]')?.value === checkbox.dataset.assistWorker
    );
    if (!worker || !row) return;
    fillText(row.querySelector('[name="worker_origin"]'), worker.origin);
    fillNumber(row.querySelector('[name="worker_driving_miles"]'), worker.reported_miles);
    fillNumber(row.querySelector('[name="worker_travel_hours"]'), worker.travel_hours);
  });

  assistPlanDialog?.close();
}

async function requestAssistPlan() {
  if (!serviceReportForm || !assistPlanDialog) return;
  const reportDate = serviceReportForm.elements["report_date"]?.value;
  if (!reportDate) {
    window.alert("请先填写报告日期。");
    serviceReportForm.elements["report_date"]?.focus();
    return;
  }
  const workers = collectAssistWorkers();
  if (!workers.length) {
    window.alert("请先在员工清单里添加服务人员。");
    return;
  }

  assistFillButton.disabled = true;
  assistFillButton.dataset.busy = "1";
  try {
    const response = await fetch(assistPlanDialog.dataset.planUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        order_id: assistPlanDialog.dataset.orderId,
        report_date: reportDate,
        workers: workers.map(({ row, ...rest }) => rest),
        site_address: serviceReportForm.elements["site_address"]?.value || "",
        departure_address: serviceReportForm.elements["departure_address"]?.value || "",
        include_routes: true,
      }),
    });
    if (!response.ok) {
      window.alert("辅助填写失败，请稍后重试。");
      return;
    }
    const plan = await response.json();
    if (!plan.ok) {
      window.alert(plan.error || "辅助填写失败。");
      return;
    }
    assistPlan = plan;
    renderAssistPlan(plan);
    assistPlanDialog.showModal();
  } catch (error) {
    window.alert("辅助填写失败，请检查网络后重试。");
  } finally {
    assistFillButton.disabled = false;
    delete assistFillButton.dataset.busy;
  }
}

async function generateMileageEvidence(force) {
  if (!mileageEvidenceButton?.dataset.generateUrl) return;
  const label = mileageEvidenceButton.textContent;
  mileageEvidenceButton.disabled = true;
  if (mileageEvidenceStatus) mileageEvidenceStatus.textContent = "正在生成里程佐证…";
  try {
    const response = await fetch(mileageEvidenceButton.dataset.generateUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: force ? 1 : 0 }),
    });
    // 响应可能是网关/错误页 HTML，不能直接假设是 JSON
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || !payload.ok) {
      let message = (payload && payload.error) || "";
      if (!message) {
        if (response.status === 502 || response.status === 504) {
          message = "生成耗时过长，网关中断了请求。请稍等几分钟再试；如果反复出现，请联系管理员。";
        } else if (response.status === 401 || response.status === 403) {
          message = "当前登录状态不足，请刷新页面重新登录后再试。";
        } else if (response.status >= 500) {
          message = `服务器错误（HTTP ${response.status}），数据没有改动。请稍后重试；如果反复出现，请联系管理员。`;
        } else if (!response.ok) {
          message = `请求失败（HTTP ${response.status}），请稍后重试。`;
        } else {
          message = "生成失败，服务器返回了无法识别的响应，请刷新页面后重试。";
        }
      }
      if (mileageEvidenceStatus) mileageEvidenceStatus.textContent = message;
      return;
    }
    const skippedText = (payload.results || [])
      .filter((item) => item.outcome === "failed")
      .map((item) => `${item.name}：${item.reason}`)
      .join("；");
    if (window.uiConfirm) {
      window.uiConfirm(
        `里程佐证已生成 ${payload.generated} 份，复用 ${payload.reused} 份，失败 ${payload.failed} 份。${
          skippedText ? `（${skippedText}）` : ""
        }页面将刷新以显示附件。`
      );
    }
    window.location.reload();
  } catch (error) {
    // fetch 本身抛错：连接被服务器/网关切断（生成超时）或本机断网
    if (mileageEvidenceStatus) {
      mileageEvidenceStatus.textContent = !navigator.onLine
        ? "网络已断开，请检查网络后重试。"
        : "与服务器中断了连接：生成耗时过长时连接会被切断。请稍等几分钟再试；如果反复出现，请联系管理员。";
    }
  } finally {
    mileageEvidenceButton.disabled = false;
    mileageEvidenceButton.textContent = label;
  }
}

assistFillButton?.addEventListener("click", requestAssistPlan);
applyAssistButton?.addEventListener("click", applyAssistPlanToForm);
closeAssistDialogButton?.addEventListener("click", () => assistPlanDialog?.close());
cancelAssistButton?.addEventListener("click", () => assistPlanDialog?.close());
mileageEvidenceButton?.addEventListener("click", () => generateMileageEvidence(false));
