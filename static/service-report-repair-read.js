/* 工作日报：现场服务描述「读取」按钮
 * 从设备维修清单（/api/field/repairs/order/<order_id>?date=<报告日期>）读取该工单
 * 将当天设备的位置号、机组编号、维修前故障及工作内容追加到现场服务描述；
 * 已有内容不会被覆盖。只读操作，不提交表单。
 */
(function () {
  'use strict';

  function reportDate() {
    const input = document.querySelector('input[name="report_date"]');
    return input && input.value ? input.value.trim() : '';
  }

  function mergeText(current, addition) {
    const devicePrefix = /^(?:[①-⑳]|\d+\.\s+)位置号：/;
    const existing = current.match(/^(?:[①-⑳]|\d+\.[ \t]+)位置号：[^\r\n]*\r?\n机组编号：[^\r\n]*\r?\n故障现象及处理：[^\r\n]*/gm) || [];
    const blockContent = block => block.trim().replace(/\r\n/g, '\n').replace(devicePrefix, '位置号：');
    const seen = new Set(existing.map(blockContent));
    const added = [];
    String(addition || '').split(/\r?\n\r?\n/).forEach(function (block) {
      if (!block.trim()) return;
      const content = blockContent(block);
      if (seen.has(content)) return;
      seen.add(content);
      const index = existing.length + added.length + 1;
      const number = index <= 20 ? String.fromCharCode(0x245F + index) : index + '. ';
      added.push(devicePrefix.test(block) ? number + content : content);
    });
    return {
      text: added.length ? current + (current.trim() ? '\n\n' : '') + added.join('\n\n') : current,
      count: added.length,
    };
  }

  function feedback(button, message, isError) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = message;
    window.setTimeout(function () {
      button.disabled = false;
      button.textContent = original;
    }, 2200);
    if (isError) window.alert(message);
  }

  async function readRepairList(button) {
    const cell = button.closest('td') || button.parentElement;
    const field = cell ? cell.querySelector('[data-work-desc]') : null;
    const orderId = button.dataset.orderId;
    if (!field || !orderId) return;

    const day = reportDate();
    const url = '/api/field/repairs/order/' + encodeURIComponent(orderId) +
      (day ? '?date=' + encodeURIComponent(day) : '');

    button.disabled = true;
    try {
      const response = await fetch(url, {
        headers: {'Accept': 'application/json'},
        credentials: 'same-origin',
      });
      const payload = await response.json().catch(function () { return {}; });
      if (!response.ok || payload.ok === false) {
        feedback(button, '读取失败', true);
        window.alert(payload.error || '读取设备维修清单失败。');
        return;
      }
      if (!payload.count) {
        feedback(button, day ? '当天无清单' : '无清单', false);
        return;
      }
      const merged = mergeText(field.value, payload.text);
      field.value = merged.text;
      field.dispatchEvent(new Event('input', {bubbles: true}));
      field.dispatchEvent(new Event('change', {bubbles: true}));
      const added = merged.count;
      feedback(button, added ? '已添加 ' + added + ' 台' : '无新增', false);
    } catch (error) {
      feedback(button, '读取失败', true);
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-read-repair]');
    if (button) {
      event.preventDefault();
      readRepairList(button);
    }
  });
})();
