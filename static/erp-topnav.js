/* ============================================================
   ERP 顶部菜单交互（v0.1.295）
   C/S 客户端行为：单开下拉、打开后悬浮即切换到相邻菜单、鼠标移开自动收起、
   方向键/Enter/Esc 键盘操作、点击外部收起、移动端汉堡抽屉
   ============================================================ */
(function () {
  'use strict';
  var nav = document.getElementById('topnav');
  if (!nav) return;

  function allGroups() {
    return Array.prototype.slice.call(nav.querySelectorAll('.nav-group'));
  }
  // 收起时连二级（.nav-subgroup）一起复位，下次打开不会残留上次展开的层级
  function closeAll() {
    Array.prototype.forEach.call(
      nav.querySelectorAll('.nav-group[open], .nav-subgroup[open]'),
      function (el) { el.open = false; }
    );
  }
  function summaryOf(group) {
    return group ? group.querySelector(':scope > summary') : null;
  }

  // 点击一级菜单：只开当前组，关掉其他组；点菜单栏空白处收起
  nav.addEventListener('click', function (e) {
    var group = e.target.closest('.nav-group');
    if (!group) {           // 菜单栏上的空白区域
      closeAll();
      return;
    }
    if (e.target.closest('summary')) {
      allGroups().forEach(function (g) {
        if (g !== group && g.open) g.open = false;
      });
      return; // 原生 details 继续 toggle 当前组
    }
    if (e.target.closest('a')) closeAll();
  });

  // C/S 客户端惯例：已经打开某个菜单时，鼠标划过相邻菜单直接切换过去
  nav.addEventListener('mouseover', function (e) {
    var group = e.target.closest('.nav-group');
    if (!group) {
      // 划过菜单栏上的「直接入口」（消息）：它没有下拉可切，把已打开的面板收起来
      if (e.target.closest('.nav-entry') &&
          allGroups().some(function (g) { return g.open; })) {
        closeAll();
      }
      return;
    }
    if (group.open) return;
    if (!allGroups().some(function (g) { return g.open; })) return; // 没打开任何菜单时不自动弹
    allGroups().forEach(function (g) { if (g !== group && g.open) g.open = false; });
    group.open = true;
  });

  // 鼠标移开整个菜单区域（含下拉面板）后自动收起。
  // 下拉面板是 nav 的后代，鼠标在其上不会触发 mouseleave；260ms 的短延时用于兜住
  // 「菜单栏 → 面板」的移动过程，避免菜单刚打开就被收掉。
  if (window.matchMedia && window.matchMedia('(hover: hover)').matches) {
    var closeTimer = null;
    function cancelAutoClose() {
      if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
    }
    nav.addEventListener('mouseenter', cancelAutoClose);
    nav.addEventListener('mousemove', cancelAutoClose);
    nav.addEventListener('mouseleave', function () {
      cancelAutoClose();
      closeTimer = setTimeout(function () { closeTimer = null; closeAll(); }, 260);
    });
    // 键盘/Tab 走到菜单之外时同样收起
    nav.addEventListener('focusout', function (e) {
      if (!nav.contains(e.relatedTarget)) closeAll();
    });
  }

  // ←/→ 的行走序列 = 一级菜单组 + 菜单栏上的直接入口（消息）。
  // v0.1.307 起「消息」不再是 .nav-group（没有下拉面板），不把它算进序列的话，
  // 键盘走遍整条菜单栏时会漏掉它。
  function stops() {
    return Array.prototype.slice.call(nav.querySelectorAll('.nav-group, .nav-entry'));
  }

  // 键盘操作：↓ 打开并聚焦首项、← → 在顶级菜单间移动、Esc 收起
  nav.addEventListener('keydown', function (e) {
    var stop = e.target.closest('.nav-group, .nav-entry');
    if (!stop) return;

    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      var list = stops();
      var at = list.indexOf(stop);
      var next = list[(at + (e.key === 'ArrowRight' ? 1 : list.length - 1)) % list.length];
      closeAll();
      if (next.classList.contains('nav-entry')) {   // 直接入口没有面板可开，聚焦即止
        next.focus();
        return;
      }
      next.open = true;
      var focusTarget = summaryOf(next);
      if (focusTarget) focusTarget.focus();
      return;
    }
    // 消息入口没有下拉：↓/Enter/空格 都交回浏览器（Enter 就是跟随链接跳转）
    if (stop.classList.contains('nav-entry')) return;

    var group = stop;

    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      if (!group.open) {
        e.preventDefault();
        closeAll();
        group.open = true;
        var first = group.querySelector('.nav-submenu a, .nav-submenu summary');
        if (first) first.focus();
      }
      return;
    }
    if (e.key === 'Escape') {
      group.open = false;
      var back = summaryOf(group);
      if (back) back.focus();
    }
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
