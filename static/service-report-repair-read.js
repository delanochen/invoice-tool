/* 工作日报：工作内容「读取」按钮
 * 从设备维修清单（/api/field/repairs/order/<order_id>）读取每台设备的
 * 位置号 + 铭牌号 + 备注，一台设备一行追加到工作内容；已有内容不会被覆盖。
 * 只读操作，不提交表单。
 */
(function () {
  'use strict';

  function splitLines(text) {
    return String(text || '').split('\n').map(function (line) { return line.trim(); }).filter(Boolean);
  }

  function mergeText(current, addition) {
    const merged = splitLines(current);
    splitLines(addition).forEach(function (line) {
      if (!merged.includes(line)) merged.push(line);
    });
    return merged.join('\n');
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

    button.disabled = true;
    try {
      const response = await fetch('/api/field/repairs/order/' + encodeURIComponent(orderId), {
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
        feedback(button, '无清单', false);
        return;
      }
      const before = field.value;
      field.value = mergeText(before, payload.text);
      field.dispatchEvent(new Event('input', {bubbles: true}));
      field.dispatchEvent(new Event('change', {bubbles: true}));
      const added = splitLines(field.value).length - splitLines(before).length;
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
