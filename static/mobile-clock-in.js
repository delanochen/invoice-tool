(() => {
  const shell = document.querySelector(".clock-shell");
  const site = document.getElementById("clockSite");
  const input = document.getElementById("clockCamera");
  const cameraButton = document.getElementById("cameraButton");
  const timeText = document.getElementById("clockTime");
  const offsetText = document.getElementById("clockOffset");
  const locationText = document.getElementById("locationText");
  const status = document.getElementById("clockStatus");
  const canvas = document.getElementById("clockPreview");
  let offsetMinutes = 0;
  let position = null;
  let busy = false;

  const adjustedDate = () => new Date(Date.now() + offsetMinutes * 60000);
  const formatDate = date => new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);

  function updateTime() {
    timeText.textContent = formatDate(adjustedDate());
    offsetText.textContent = offsetMinutes === 0 ? "未调整" : `已${offsetMinutes > 0 ? "向后" : "向前"}调整 ${Math.abs(offsetMinutes)} 分钟`;
  }

  function updateReady() {
    const ready = Boolean(site.value && position && !busy);
    input.disabled = !ready;
    cameraButton.classList.toggle("disabled", !ready);
  }

  function showStatus(message, error = false) {
    status.textContent = message;
    status.className = `clock-status show${error ? " error" : ""}`;
  }

  function locate() {
    position = null;
    locationText.textContent = "正在获取坐标…";
    updateReady();
    if (!navigator.geolocation) { locationText.textContent = "设备不支持定位"; showStatus("无法获取 GPS 坐标，不能打卡。", true); return; }
    navigator.geolocation.getCurrentPosition(result => {
      position = result.coords;
      locationText.textContent = `${position.latitude.toFixed(6)}, ${position.longitude.toFixed(6)}（±${Math.round(position.accuracy)}m）`;
      updateReady();
    }, error => {
      locationText.textContent = "定位失败";
      showStatus(`请允许 App 使用定位后重试：${error.message}`, true);
      updateReady();
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 15000 });
  }

  function loadImage(file) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      const url = URL.createObjectURL(file);
      image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("无法读取照片")); };
      image.src = url;
    });
  }

  async function watermark(file, capturedAt) {
    const image = await loadImage(file);
    const maxSide = 2400;
    const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.round(image.naturalWidth * scale);
    canvas.height = Math.round(image.naturalHeight * scale);
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const fontSize = Math.max(22, Math.round(canvas.width * .025));
    const padding = Math.round(fontSize * .75);
    const lines = [
      site.selectedOptions[0].dataset.name,
      `操作员：${shell.dataset.operator}`,
      `时间：${formatDate(capturedAt)}  调整：${offsetMinutes >= 0 ? "+" : ""}${offsetMinutes}分钟`,
      `坐标：${position.latitude.toFixed(6)}, ${position.longitude.toFixed(6)}  精度：±${Math.round(position.accuracy)}m`
    ];
    const lineHeight = Math.round(fontSize * 1.35);
    const boxHeight = lines.length * lineHeight + padding * 2;
    context.fillStyle = "rgba(0, 0, 0, .66)";
    context.fillRect(0, canvas.height - boxHeight, canvas.width, boxHeight);
    context.fillStyle = "#fff";
    context.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
    lines.forEach((line, index) => context.fillText(line, padding, canvas.height - boxHeight + padding + lineHeight * (index + .8), canvas.width - padding * 2));
    canvas.hidden = false;
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("照片处理失败")), "image/jpeg", .88));
  }

  async function capture(file) {
    busy = true; updateReady(); showStatus("正在加水印并上传 NAS…");
    const capturedAt = adjustedDate();
    try {
      const blob = await watermark(file, capturedAt);
      const data = new FormData();
      data.append("site_id", site.value);
      data.append("captured_at", capturedAt.toISOString());
      data.append("time_offset_minutes", String(offsetMinutes));
      data.append("latitude", String(position.latitude));
      data.append("longitude", String(position.longitude));
      data.append("accuracy", String(position.accuracy || 0));
      data.append("photo", blob, `clock-in-${Date.now()}.jpg`);
      const response = await fetch(shell.dataset.uploadUrl, { method: "POST", body: data, credentials: "same-origin" });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.error || "上传失败");
      showStatus(result.message || "打卡成功，照片已上传 NAS。");
    } catch (error) { showStatus(error.message || "打卡失败，请重试。", true); }
    finally { busy = false; input.value = ""; updateReady(); }
  }

  document.querySelectorAll("[data-adjust]").forEach(button => button.addEventListener("click", () => {
    offsetMinutes = Math.max(-1440, Math.min(1440, offsetMinutes + Number(button.dataset.adjust))); updateTime();
  }));
  document.getElementById("clockReset").addEventListener("click", () => { offsetMinutes = 0; updateTime(); });
  document.getElementById("refreshLocation").addEventListener("click", locate);
  site.addEventListener("change", updateReady);
  input.addEventListener("change", () => { if (input.files[0]) capture(input.files[0]); });
  updateTime(); setInterval(updateTime, 1000); locate();
})();
