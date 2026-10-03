(() => {
  'use strict';
  const tabs = [], bar = document.getElementById('workspaceTabs'), pages = document.getElementById('workspacePages');
  const notice = document.getElementById('workspaceNotice');
  const AUTO_RETRY_DELAYS = [1200, 3000, 7000];
  const LOAD_TIMEOUT = 15000;
  let active, serial = 0;
  const localUrl = value => {
    try {
      const url = new URL(value, location.origin);
      if (url.origin !== location.origin || url.pathname === '/workspace' || url.pathname === '/logout' || url.pathname === '/login') return null;
      return url.pathname + url.search + url.hash;
    } catch { return null; }
  };
  const key = value => { const url = new URL(value, location.origin); return url.pathname + url.search; };
  function paint(tab) {
    tab.button.textContent = (tab.dirty ? '● ' : '') + tab.title;
    tab.button.title = tab.title + (tab.dirty ? '（有未保存内容）' : '');
    tab.close.setAttribute('aria-label', '关闭 ' + tab.title);
  }
  function activate(tab) {
    active = tab;
    tabs.forEach(item => {
      item.frame.hidden = item !== tab || item.failed;
      item.failure.hidden = item !== tab || !item.failed;
      item.node.classList.toggle('active', item === tab);
      item.button.setAttribute('aria-selected', String(item === tab));
      item.button.tabIndex = item === tab ? 0 : -1;
    });
    notice.hidden = !tab.stale;
    history.replaceState(null, '', '/workspace#' + encodeURIComponent(tab.url));
    document.title = tab.title + ' - 工作区';
    tab.node.scrollIntoView({block: 'nearest', inline: 'nearest'});
    document.querySelectorAll('#topnav a').forEach(link => {
      const match = key(link.href) === key(tab.url);
      link.classList.toggle('active', match);
      if (match) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
  }
  const allowDiscard = tab => !tab.dirty || window.uiConfirm('"' + tab.title + '"有未保存的修改或已选文件，是否放弃这些内容？');
  function close(tab) {
    if (!allowDiscard(tab)) return;
    const index = tabs.indexOf(tab);
    if (tab.retryTimer) clearTimeout(tab.retryTimer);
    if (tab.loadTimer) clearTimeout(tab.loadTimer);
    tabs.splice(index, 1); tab.node.remove(); tab.frame.remove(); tab.failure.remove();
    if (!tabs.length) open('/'); else if (active === tab) activate(tabs[Math.min(index, tabs.length - 1)]);
  }
  function changed(source) {
    tabs.forEach(tab => { if (tab !== source) tab.stale = true; });
    if (active) notice.hidden = !active.stale;
  }
  function showLoadFailure(tab, final = false, message = '') {
    tab.failed = true;
    tab.failure.querySelector('[data-workspace-load-message]').textContent = message || (final
      ? '该页面未能从服务器加载。可能是网络或 Cloudflare 隧道短暂中断；如果正在上传附件，也请确认所选本地文件仍然存在。'
      : '页面连接短暂中断，系统正在自动重新加载…');
    tab.failure.querySelector('[data-workspace-retry]').hidden = !final;
    if (active === tab) activate(tab);
  }
  function retryLoad(tab, automatic = false) {
    if (tab.retryTimer) clearTimeout(tab.retryTimer);
    if (tab.loadTimer) clearTimeout(tab.loadTimer);
    tab.retryTimer = null;
    tab.loadTimer = null;
    if (!automatic) tab.loadFailures = 0;
    tab.failed = true;
    tab.failure.querySelector('[data-workspace-load-message]').textContent = '正在重新连接服务器并加载页面…';
    tab.failure.querySelector('[data-workspace-retry]').hidden = true;
    if (active === tab) activate(tab);
    tab.frame.src = tab.url;
    tab.loadTimer = setTimeout(() => pageLoadFailed(tab), LOAD_TIMEOUT);
  }
  function pageLoadFailed(tab) {
    if (tab.retryTimer) return;
    if (tab.loadTimer) clearTimeout(tab.loadTimer);
    tab.loadTimer = null;
    tab.loadFailures += 1;
    if (!navigator.onLine) {
      showLoadFailure(tab, true, '当前网络不可用。网络恢复后系统会自动重新加载，也可点击按钮手动重试。');
      return;
    }
    const delay = AUTO_RETRY_DELAYS[tab.loadFailures - 1];
    if (delay !== undefined) {
      showLoadFailure(tab, false, '页面连接短暂中断，系统将在 ' + Math.ceil(delay / 1000) + ' 秒后自动重试…');
      tab.retryTimer = setTimeout(() => retryLoad(tab, true), delay);
      return;
    }
    showLoadFailure(tab, true);
  }
  function attach(tab) {
    let win, doc;
    try { win = tab.frame.contentWindow; doc = win.document; } catch { pageLoadFailed(tab); return; }
    if (!doc || win.location.href === 'about:blank') return;
    if (win.location.pathname === '/login') { location.href = '/login'; return; }
    // Workspace URLs are application documents and must contain .main. Browser/network
    // error documents and proxy error pages do not, so replace them with our retry UI.
    if (!doc.querySelector('.main')) { pageLoadFailed(tab); return; }
    if (tab.retryTimer) clearTimeout(tab.retryTimer);
    if (tab.loadTimer) clearTimeout(tab.loadTimer);
    tab.retryTimer = null; tab.loadTimer = null; tab.loadFailures = 0; tab.failed = false;
    if (active === tab) activate(tab);
    // 让页面内的脚本也能请求「在工作区里新开一个标签页」（而不是自己 location.href 跳走，
    // 那会把当前标签的页面整个换掉）。工作区内所有站内跳转都应走这里，见
    // selectable-table-actions.js 的 detailUrl 按钮。
    try { win.workspaceOpen = (url, title) => open(url, title); } catch { /* 跨源等异常忽略 */ }
    if (tab.submitted) changed(tab);
    tab.submitted = false; tab.dirty = false; tab.stale = false;
    tab.url = localUrl(win.location.href) || tab.url;
    tab.title = doc.title.replace(/\s*-\s*发票工具$/, '').trim() || doc.querySelector('h1')?.textContent.trim() || '页面';
    const record = doc.querySelector('.page-header')?.textContent.match(/\b(?:SO|EX|SR)\d{5,}\b/)?.[0];
    if (record && !tab.title.includes(record)) tab.title += ' · ' + record;
    frameTitle(tab);
    paint(tab);
    if (active === tab) activate(tab);
    const dirty = event => {
      if (event.target.closest?.('form')?.method.toLowerCase() === 'post') { tab.dirty = true; paint(tab); }
    };
    doc.addEventListener('input', dirty); doc.addEventListener('change', dirty);
    doc.addEventListener('submit', event => {
      if (event.target.method.toLowerCase() !== 'post') return;
      if (tab.stale && !window.uiConfirm('其他页面可能已更新数据。建议取消并刷新核对；仍要提交当前内容吗？')) {
        event.preventDefault(); event.stopImmediatePropagation(); return;
      }
      tab.submitted = true;
      queueMicrotask(() => { if (event.defaultPrevented) tab.submitted = false; });
      // Keep the dirty marker until a successful navigation, including AJAX errors.
    }, true);
    doc.addEventListener('workspace:saved', () => changed(tab));
    doc.addEventListener('workspace:navigate', () => { tab.submitted = true; });
    doc.addEventListener('workspace:save-failed', () => { tab.submitted = false; });
    win.addEventListener('beforeunload', event => {
      if (tab.dirty && !tab.submitted) { event.preventDefault(); event.returnValue = ''; }
    });
    doc.addEventListener('click', event => {
      const link = event.target.closest?.('a[href]');
      if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.hasAttribute('download') || link.hasAttribute('onclick') || link.dataset.imagePreview !== undefined) return;
      const url = localUrl(link.href);
      // 现场工作（/field/）是独立的手机端页面，不放进工作区 iframe，也不另开浏览器标签：
      // iframe 内的原生导航只会替换当前 iframe，因此必须由外壳主动执行顶层导航。
      // 返回走页面内的「进入管理系统」。工作区外壳的未保存改动由 beforeunload 兜底提示。
      if (url && /^\/field\/?(?:[?#]|$)/.test(url)) { event.preventDefault(); location.assign(url); return; }
      if (link.target) return;
      if (!url || link.getAttribute('href').startsWith('#') || /\.(?:pdf|zip|xlsx?|csv|jpe?g|png|webp)(?:[?#]|$)|\/(?:download|preview|print|export)(?:[/?-]|$)|^\/field\/?(?:[?#]|$)/i.test(url)) return;
      // List/detail/edit links open independently. GET filter forms stay in their tab.
      event.preventDefault(); open(url, link.textContent.trim());
    });
  }
  function open(value, title = '加载中…') {
    const url = localUrl(value);
    if (!url) return;
    const existing = tabs.find(tab => key(tab.url) === key(url));
    if (existing) { activate(existing); return; }
    const node = document.createElement('div'), button = document.createElement('button'), closeButton = document.createElement('button'), frame = document.createElement('iframe'), failure = document.createElement('section');
    node.className = 'workspace-tab'; button.type = closeButton.type = 'button'; button.setAttribute('role', 'tab');
    frame.id = 'workspace-page-' + (++serial); frame.title = title; frame.setAttribute('role', 'tabpanel');
    frame.setAttribute('allow', 'web-share; clipboard-write');
    failure.className = 'workspace-load-failure'; failure.hidden = true;
    failure.innerHTML = '<div class="workspace-load-failure-card"><div class="workspace-load-failure-icon" aria-hidden="true">!</div><h2>页面加载失败</h2><p data-workspace-load-message></p><button type="button" data-workspace-retry hidden>重新加载</button><small>如果仍然失败，请检查网络后稍候再试；已经选择的本地附件可能需要重新选择。</small></div>';
    button.id = 'workspace-tab-' + serial; button.setAttribute('aria-controls', frame.id); frame.setAttribute('aria-labelledby', button.id);
    closeButton.className = 'workspace-close'; closeButton.textContent = '×';
    const tab = {url, title, node, button, close: closeButton, frame, failure, dirty: false, stale: false, failed: false, loadFailures: 0, retryTimer: null, loadTimer: null};
    button.addEventListener('click', () => activate(tab)); closeButton.addEventListener('click', () => close(tab));
    failure.querySelector('[data-workspace-retry]').addEventListener('click', () => retryLoad(tab));
    button.addEventListener('keydown', event => {
      let index = tabs.indexOf(tab);
      if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Delete') { close(tab); return; } else return;
      event.preventDefault(); activate(tabs[index]); tabs[index].button.focus();
    });
    frame.addEventListener('load', () => attach(tab));
    frame.addEventListener('error', () => pageLoadFailed(tab));
    node.append(button, closeButton); bar.append(node); tabs.push(tab); paint(tab);
    pages.append(frame, failure); activate(tab);
    tab.loadTimer = setTimeout(() => pageLoadFailed(tab), LOAD_TIMEOUT);
    frame.src = url;
  }
  document.querySelectorAll('#topnav a').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || link.target) return;
    // 现场工作（/field/）：交还浏览器原生导航整页进入，不另开浏览器标签页，也不塞进工作区 iframe。
    if (/^\/field\/?$/.test(new URL(link.href).pathname)) return;
    event.preventDefault(); open(link.href, link.textContent.trim());
  }));
  const refresh = () => { if (active && allowDiscard(active)) { active.dirty = false; active.frame.contentWindow.location.reload(); } };
  document.getElementById('workspaceRefresh').addEventListener('click', refresh);
  document.getElementById('workspaceNoticeRefresh').addEventListener('click', refresh);
  document.getElementById('workspaceCloseOthers').addEventListener('click', () => {
    const others = tabs.filter(tab => tab !== active);
    if (others.some(tab => tab.dirty) && !window.uiConfirm('其他标签中有未保存内容，确认全部放弃并关闭？')) return;
    others.forEach(tab => { tab.dirty = false; close(tab); });
  });
  window.addEventListener('beforeunload', event => {
    if (tabs.some(tab => tab.dirty)) { event.preventDefault(); event.returnValue = ''; }
  });
  const retryFailedTabs = () => {
    if (!navigator.onLine) return;
    tabs.filter(tab => tab.failed).forEach(tab => retryLoad(tab));
  };
  window.addEventListener('online', retryFailedTabs);
  window.addEventListener('pageshow', retryFailedTabs);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) retryFailedTabs(); });
  function frameTitle(tab) { tab.frame.title = tab.title; }
  window.addEventListener('hashchange', () => { try { open(decodeURIComponent(location.hash.slice(1)) || '/'); } catch { open('/'); } });
  try { open(decodeURIComponent(location.hash.slice(1)) || '/'); } catch { open('/'); }
  if (!tabs.length) open('/');
})();
