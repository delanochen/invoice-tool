/* ============================================================
   ERP 顶部菜单交互（v0.1.287）
   单开下拉、点击外部关闭、移动端汉堡抽屉
   ============================================================ */
(function () {
  'use strict';
  var nav = document.getElementById('topnav');
  if (!nav) return;

  function allGroups() {
    return Array.prototype.slice.call(nav.querySelectorAll('.nav-group'));
  }
  function closeAll() {
    allGroups().forEach(function (g) { if (g.open) g.open = false; });
  }

  // 点击一级菜单：只开当前组，关掉其他组
  nav.addEventListener('click', function (e) {
    var group = e.target.closest('.nav-group');
    if (!group) return;
    if (e.target.closest('summary')) {
      allGroups().forEach(function (g) {
        if (g !== group && g.open) g.open = false;
      });
      return; // 原生 details 继续 toggle 当前组
    }
    if (e.target.closest('a')) closeAll();
  });

  // 点击导航以外的区域：收起所有下拉
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.topbar')) closeAll();
  });

  // 移动端：汉堡按钮展开/收起抽屉
  var toggle = document.querySelector('.mobile-menu-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    // 抽屉内点击链接后收起
    nav.addEventListener('click', function (e) {
      if (e.target.closest('a')) nav.classList.remove('open');
    });
  }
})();
