(() => {
  'use strict';
  const tabs = [], bar = document.getElementById('workspaceTabs'), pages = document.getElementById('workspacePages');
  const notice = document.getElementById('workspaceNotice');
  let active, serial = 0;
  const localUrl = value => {
    try {
      const url = new URL(value, location.origin);
      if (url.origin !== location.origin || url.pathname === '/workspace' || url.pathname === '/logout' || url.pathname === '/login') return null;
      return url.pathname + url.search + url.hash;
    } catch { return null; }
  };
  const key = value => { const url = new URL(value, location.origin); return url.pathname; };
  function paint(tab) {
    tab.button.textContent = (tab.dirty ? '● ' : '') + tab.title;
    tab.button.title = tab.title + (tab.dirty ? '（有未保存内容）' : '');
    tab.close.setAttribute('aria-label', '关闭 ' + tab.title);
  }
  function activate(tab) {
    active = tab;
    tabs.forEach(item => {
      item.frame.hidden = item !== tab;
      item.node.classList.toggle('active', item === tab);
      item.button.setAttribute('aria-selected', String(item === tab));
      item.button.tabIndex = item === tab ? 0 : -1;
    });
    notice.hidden = !tab.stale;
    history.replaceState(null, '', '/workspace#' + encodeURIComponent(tab.url));
    document.title = tab.title + ' - 工作区';
    tab.node.scrollIntoView({block: 'nearest', inline: 'nearest'});
    document.querySelectorAll('.sidebar nav a').forEach(link => {
      const match = key(link.href) === key(tab.url);
      link.classList.toggle('active', match);
      if (match) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
  }
  const allowDiscard = tab => !tab.dirty || confirm('“' + tab.title + '”有未保存的修改或已选文件，是否放弃这些内容？');
  function close(tab) {
    if (!allowDiscard(tab)) return;
    const index = tabs.indexOf(tab);
    tabs.splice(index, 1); tab.node.remove(); tab.frame.remove();
    if (!tabs.length) open('/'); else if (active === tab) activate(tabs[Math.min(index, tabs.length - 1)]);
  }
  function changed(source) {
    tabs.forEach(tab => { if (tab !== source) tab.stale = true; });
    if (active) notice.hidden = !active.stale;
  }
  function attach(tab) {
    let win, doc;
    try { win = tab.frame.contentWindow; doc = win.document; } catch { return; }
    if (!doc || win.location.href === 'about:blank') return;
    if (win.location.pathname === '/login') { location.href = '/login'; return; }
    // Downloads, previews and the camera application keep their original navigation.
    if (!doc.querySelector('.main')) return;
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
      if (tab.stale && !confirm('其他页面可能已更新数据。建议取消并刷新核对；仍要提交当前内容吗？')) {
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
      if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download') || link.hasAttribute('onclick') || link.dataset.imagePreview !== undefined) return;
      const url = localUrl(link.href);
      if (url && /^\/field\/?(?:[?#]|$)/.test(url)) { event.preventDefault(); window.open(url, '_blank', 'noopener'); return; }
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
    const node = document.createElement('div'), button = document.createElement('button'), closeButton = document.createElement('button'), frame = document.createElement('iframe');
    node.className = 'workspace-tab'; button.type = closeButton.type = 'button'; button.setAttribute('role', 'tab');
    frame.id = 'workspace-page-' + (++serial); frame.title = title; frame.setAttribute('role', 'tabpanel');
    frame.setAttribute('allow', 'web-share; clipboard-write');
    button.id = 'workspace-tab-' + serial; button.setAttribute('aria-controls', frame.id); frame.setAttribute('aria-labelledby', button.id);
    closeButton.className = 'workspace-close'; closeButton.textContent = '×';
    const tab = {url, title, node, button, close: closeButton, frame, dirty: false, stale: false};
    button.addEventListener('click', () => activate(tab)); closeButton.addEventListener('click', () => close(tab));
    button.addEventListener('keydown', event => {
      let index = tabs.indexOf(tab);
      if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Delete') { close(tab); return; } else return;
      event.preventDefault(); activate(tabs[index]); tabs[index].button.focus();
    });
    frame.addEventListener('load', () => attach(tab));
    node.append(button, closeButton); bar.append(node); tabs.push(tab); paint(tab);
    frame.src = url; pages.append(frame); activate(tab);
  }
  document.querySelectorAll('.sidebar nav a').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || link.target) return;
    if (/^\/field\/?$/.test(new URL(link.href).pathname)) { event.preventDefault(); window.open(link.href, '_blank', 'noopener'); return; }
    event.preventDefault(); open(link.href, link.textContent.trim());
  }));
  const refresh = () => { if (active && allowDiscard(active)) { active.dirty = false; active.frame.contentWindow.location.reload(); } };
  document.getElementById('workspaceRefresh').addEventListener('click', refresh);
  document.getElementById('workspaceNoticeRefresh').addEventListener('click', refresh);
  document.getElementById('workspaceCloseOthers').addEventListener('click', () => {
    const others = tabs.filter(tab => tab !== active);
    if (others.some(tab => tab.dirty) && !confirm('其他标签中有未保存内容，确认全部放弃并关闭？')) return;
    others.forEach(tab => { tab.dirty = false; close(tab); });
  });
  window.addEventListener('beforeunload', event => {
    if (tabs.some(tab => tab.dirty)) { event.preventDefault(); event.returnValue = ''; }
  });
  function frameTitle(tab) { tab.frame.title = tab.title; }
  window.addEventListener('hashchange', () => { try { open(decodeURIComponent(location.hash.slice(1)) || '/'); } catch { open('/'); } });
  try { open(decodeURIComponent(location.hash.slice(1)) || '/'); } catch { open('/'); }
  if (!tabs.length) open('/');
})();
