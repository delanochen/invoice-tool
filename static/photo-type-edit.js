/* Ledger photo-type editing (photo ledger page).
 * Each .photo-type-select posts its new value to
 * POST /api/field/photos/<id>/type and rolls back on failure.
 * CSRF uses the existing X-Field-Token session token already rendered
 * as data-csrf on #photoBatchDownload. */
(function () {
  "use strict";

  function fieldToken() {
    var el = document.getElementById("photoBatchDownload");
    return (el && el.dataset.csrf) || "";
  }

  function rollback(select) {
    var current = select.dataset.current || "";
    var opts = select.querySelectorAll("option");
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].value === current) {
        select.value = current;
        return;
      }
    }
    // current value is a historical label (general/legacy): show placeholder
    select.value = "";
  }

  function flash(message, isError) {
    var bar = document.getElementById("statusBanner");
    if (!bar) {
      window.alert(message);
      return;
    }
    bar.textContent = message;
    bar.style.display = "";
    bar.style.color = isError ? "#b00020" : "#1b5e20";
    window.setTimeout(function () {
      bar.textContent = "";
      bar.style.display = "none";
    }, 4000);
  }

  function save(select) {
    var photoId = select.dataset.photoId;
    var photoType = select.value;
    var token = fieldToken();
    if (!photoId || !photoType || !token) {
      rollback(select);
      flash("无法保存照片类型：缺少必要信息。", true);
      return;
    }
    var previous = select.dataset.current || "";
    select.disabled = true;
    fetch("/api/field/photos/" + encodeURIComponent(photoId) + "/type", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Field-Token": token,
      },
      body: JSON.stringify({ photo_type: photoType }),
    })
      .then(function (resp) {
        return resp.json().then(function (data) {
          return { ok: resp.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          throw new Error(result.data && result.data.error ? result.data.error : "HTTP " + result.status);
        }
        select.dataset.current = photoType;
        select.disabled = false;
        flash("照片类型已更新为「" + (result.data.photo_type_label || photoType) + "」。", false);
      })
      .catch(function (err) {
        select.disabled = false;
        rollback(select);
        flash("保存失败：" + (err && err.message ? err.message : "网络错误") + "（已恢复原类型）", true);
      });
  }

  document.addEventListener("change", function (evt) {
    var select = evt.target;
    if (!select || !select.classList || !select.classList.contains("photo-type-select")) {
      return;
    }
    if (select.value === (select.dataset.current || "")) {
      return; // unchanged
    }
    save(select);
  });
})();
