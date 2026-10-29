/* A viewport scrollbar for long photo ledgers; original table scrolling stays intact. */
(() => {
  function init() {
    const dock = document.createElement('div');
    dock.className = 'photo-ledger-scrollbar';
    dock.hidden = true;
    dock.tabIndex = 0;
    dock.setAttribute('role', 'region');
    dock.setAttribute('aria-label', '照片台账横向滚动');
    const track = document.createElement('div');
    dock.append(track);
    document.body.append(dock);
    let active = null, frame = null;
    const observed = new Set();
    const schedule = () => {
      if (frame === null) frame = requestAnimationFrame(update);
    };
    const resize = new ResizeObserver(schedule);
    function update() {
      frame = null;
      const viewport = window.visualViewport;
      const viewTop = viewport?.offsetTop || 0;
      const viewBottom = viewTop + (viewport?.height || window.innerHeight);
      const targets = new Set();
      let choice = null;
      for (const root of document.querySelectorAll('.photo-ledger-page .erp-grid-wrap, #ledgerList')) {
        const source = root.querySelector('.tabulator-tableholder') || root;
        targets.add(root); targets.add(source);
        const rect = source.getBoundingClientRect();
        if (!source.clientHeight || source.scrollWidth <= source.clientWidth + 1) continue;
        let top = Math.max(viewTop, rect.top), bottom = Math.min(viewBottom, rect.bottom);
        let left = Math.max(0, rect.left), right = Math.min(window.innerWidth, rect.right);
        for (let parent = source.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
          const style = getComputedStyle(parent), clip = parent.getBoundingClientRect();
          if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
            top = Math.max(top, clip.top); bottom = Math.min(bottom, clip.bottom);
          }
          if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
            left = Math.max(left, clip.left); right = Math.min(right, clip.right);
          }
        }
        for (const footer of document.querySelectorAll('.photo-ledger-page > .erp-summary, .photo-ledger-page > .erp-status, .bottom-tabs')) {
          const f = footer.getBoundingClientRect();
          if (f.height && f.top >= top && f.top < bottom && f.left < right && f.right > left) bottom = f.top;
        }
        // Use the native bar whenever its bottom edge is already accessible.
        if (bottom - top < 36 || right - left < 80 || rect.bottom <= bottom + 1) continue;
        choice = {source, left, width: right - left, bottom};
        break;
      }
      for (const node of observed) if (!targets.has(node)) { resize.unobserve(node); observed.delete(node); }
      for (const node of targets) if (!observed.has(node)) { resize.observe(node); observed.add(node); }
      if (!choice) {
        active = null;
        if (!dock.hidden) dock.hidden = true;
        return;
      }
      active = choice.source;
      dock.style.left = choice.left + 'px';
      dock.style.width = choice.width + 'px';
      dock.style.top = (choice.bottom - 18) + 'px';
      track.style.width = (choice.width - 2 + active.scrollWidth - active.clientWidth) + 'px';
      if (dock.hidden) dock.hidden = false;
      if (Math.abs(dock.scrollLeft - active.scrollLeft) > 1) dock.scrollLeft = active.scrollLeft;
    }
    dock.addEventListener('scroll', () => {
      if (active && Math.abs(active.scrollLeft - dock.scrollLeft) > 1) active.scrollLeft = dock.scrollLeft;
    });
    document.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('scroll', schedule);
    new MutationObserver(schedule).observe(document.body, {subtree:true, childList:true, attributes:true, attributeFilter:['hidden']});
    schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
  else init();
})();
