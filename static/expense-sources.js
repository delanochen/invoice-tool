// 工单结算：自动转入金额来源明细弹窗
(() => {
  const cells = document.querySelectorAll('.expense-amount-cell');
  if (!cells.length) return;

  let activePopup = null;

  // 解析金额字符串为数字
  function parseAmount(value) {
    if (typeof value === 'number') return value;
    if (!value) return 0;
    return parseFloat(String(value).replace(/[^0-9.\-]/g, '')) || 0;
  }

  // 格式化金额
  function formatAmount(value) {
    const num = parseAmount(value);
    return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // 更新单元格的调整状态
  function updateAdjustedState(cell) {
    const input = cell.querySelector('input[data-auto-amount]');
    if (!input) return;
    const autoAmount = parseAmount(input.dataset.autoAmount);
    const currentAmount = parseAmount(input.value);
    const isAdjusted = autoAmount > 0 && Math.abs(currentAmount - autoAmount) > 0.001;

    input.classList.toggle('is-adjusted', isAdjusted);
    const badge = cell.querySelector('.adjusted-badge');
    if (badge) badge.style.display = isAdjusted ? '' : 'none';

    // 更新弹窗中的汇总
    const popup = cell.querySelector('.expense-source-popup');
    if (popup) {
      const currentDisplay = popup.querySelector('.current-amount-display');
      const diffDisplay = popup.querySelector('.diff-amount');
      const restoreBtn = popup.querySelector('.restore-auto-amount');
      if (currentDisplay) currentDisplay.textContent = '$' + formatAmount(currentAmount);
      if (diffDisplay) {
        const diff = currentAmount - autoAmount;
        diffDisplay.textContent = (diff >= 0 ? '+$' : '-$') + formatAmount(Math.abs(diff));
        diffDisplay.classList.toggle('negative', diff < 0);
      }
      if (restoreBtn) restoreBtn.style.display = isAdjusted ? '' : 'none';
    }
  }

  // 定位弹窗，避免超出视口
  function positionPopup(popup, icon) {
    const rect = icon.getBoundingClientRect();
    const popupRect = popup.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;

    // 水平方向：如果超出右边界，左移
    if (left + popupRect.width > window.innerWidth - 8) {
      left = window.innerWidth - popupRect.width - 8;
    }
    if (left < 8) left = 8;

    // 垂直方向：如果超出下边界，显示在上方
    if (top + popupRect.height > window.innerHeight - 8) {
      top = rect.top - popupRect.height - 4;
    }
    if (top < 8) top = 8;

    popup.style.left = left + 'px';
    popup.style.top = top + 'px';
  }

  // 打开弹窗
  function openPopup(cell) {
    closePopup();
    const popup = cell.querySelector('.expense-source-popup');
    const icon = cell.querySelector('.expense-source-icon');
    if (!popup || !icon) return;

    popup.hidden = false;
    popup.style.position = 'fixed';
    icon.classList.add('active');
    activePopup = { popup, icon, cell };
    positionPopup(popup, icon);
    updateAdjustedState(cell);
  }

  // 关闭弹窗
  function closePopup() {
    if (!activePopup) return;
    activePopup.popup.hidden = true;
    activePopup.icon.classList.remove('active');
    activePopup = null;
  }

  // 绑定每个单元格的交互
  cells.forEach(cell => {
    const icon = cell.querySelector('.expense-source-icon');
    const popup = cell.querySelector('.expense-source-popup');
    const input = cell.querySelector('input[data-auto-amount]');
    if (!icon || !popup) return;

    // 图标点击切换
    icon.addEventListener('click', event => {
      event.stopPropagation();
      if (activePopup && activePopup.cell === cell) {
        closePopup();
      } else {
        openPopup(cell);
      }
    });

    // 电脑端悬停预览（延迟显示，避免误触）
    let hoverTimer = null;
    icon.addEventListener('mouseenter', () => {
      if (window.matchMedia('(pointer: fine)').matches) {
        hoverTimer = setTimeout(() => {
          if (!activePopup) openPopup(cell);
        }, 300);
      }
    });
    icon.addEventListener('mouseleave', () => {
      if (hoverTimer) clearTimeout(hoverTimer);
    });

    // 弹窗内点击不冒泡
    popup.addEventListener('click', event => event.stopPropagation());

    // 关闭按钮
    const closeBtn = popup.querySelector('.expense-source-popup-close');
    if (closeBtn) closeBtn.addEventListener('click', closePopup);

    // 恢复来源金额
    const restoreBtn = popup.querySelector('.restore-auto-amount');
    if (restoreBtn && input) {
      restoreBtn.addEventListener('click', () => {
        input.value = input.dataset.autoAmount;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        updateAdjustedState(cell);
      });
    }

    // input 修改检测
    if (input) {
      input.addEventListener('input', () => updateAdjustedState(cell));
      // 初始化状态
      updateAdjustedState(cell);
    }
  });

  // 点击外部关闭弹窗
  document.addEventListener('click', () => closePopup());

  // ESC 关闭
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closePopup();
  });

  // 滚动和窗口大小变化时重新定位
  window.addEventListener('scroll', () => {
    if (activePopup) positionPopup(activePopup.popup, activePopup.icon);
  }, true);
  window.addEventListener('resize', () => {
    if (activePopup) positionPopup(activePopup.popup, activePopup.icon);
  });
})();
