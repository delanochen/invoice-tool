/* 站点州简写自动解析：用户在维护站点地址时，实时从详细地址解析美国州简写填入州字段。
 * 规则与 invoice_tool/customers/services.py 的 state_code_from_address 保持一致：
 *   - 优先匹配地址尾部的 "州简写 + 邮编"（如 "Houston, TX 77001" → TX）；
 *   - 兜底匹配任意独立出现的美国州简写；
 *   - 只认 US_STATE_CODES 清单，街道后缀（ST/RD/NW）与非美国地址不会误判。
 */
(() => {
  const US_STATE_CODES = new Set([
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
  ]);
  const TAIL_RE = /(?:^|[\s,])([A-Z]{2})\s*(?:[,.]?\s*\d{5}(?:-\d{4})?)?\s*$/;
  const STANDALONE_RE = /(?<![A-Z])([A-Z]{2})(?![A-Z])/g;

  const parseStateCode = (address) => {
    const text = String(address || "").trim().toUpperCase().replace(/\s+/g, " ");
    if (!text) return "";
    const tail = text.match(TAIL_RE);
    if (tail && US_STATE_CODES.has(tail[1])) return tail[1];
    STANDALONE_RE.lastIndex = 0;
    for (let m = STANDALONE_RE.exec(text); m; m = STANDALONE_RE.exec(text)) {
      if (US_STATE_CODES.has(m[1])) return m[1];
    }
    return "";
  };

  const wireAutoFill = (root) => {
    (root || document).querySelectorAll('textarea[name="detailed_address"], input[name="detailed_address"]').forEach((el) => {
      if (el.dataset.stateAutoFill) return;
      el.dataset.stateAutoFill = "1";
      const form = el.closest("form");
      const stateInput = form && form.querySelector('input[name="state_code"]');
      if (!stateInput) return;
      const fill = () => {
        const code = parseStateCode(el.value);
        if (code && code !== stateInput.value.trim().toUpperCase()) {
          stateInput.value = code;
        }
        // 联动更新州名（当前语言的本地化州名，由模板注入 window.BUYER_STATE_NAMES）
        if (code && window.BUYER_STATE_NAMES) {
          const nameEl = stateInput.parentElement
            ? stateInput.parentElement.querySelector("[data-state-name]")
            : null;
          if (nameEl) nameEl.textContent = window.BUYER_STATE_NAMES[code] || "";
        }
      };
      el.addEventListener("input", fill);
      fill(); // 页面加载时若地址已有可解析的州简写，也立即填入
    });
  };

  document.addEventListener("DOMContentLoaded", () => wireAutoFill(document));
  window.buyerStateCode = { parseStateCode, wireAutoFill };
})();
