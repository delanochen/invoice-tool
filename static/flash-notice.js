/* 服务端提示浮层（.flash-stack）的竖直定位。
 *
 * 为什么需要这个脚本：提示是 `position: fixed; top: <固定值>` 的浮层，而 ERP 页面
 * 顶部有一条常驻 sticky 工具栏（.erp-toolbar，z-index 12）。两者都贴视口顶部：
 *   - 提示层级低 → 整块被工具栏盖住，只剩一圈边框（用户两次报的就是这个）
 *   - 提示层级高但 top 写死 → 反过来压住工具栏那排按钮（刷新/打印/审核通过）
 * 工具栏的高度是**变量**：窗口变窄会换行、标题长短不同、页面之间也不一样
 * （v0.1.309 曾写死 top:60px，在工具栏更高的页面/更窄的窗口上又压住工具栏）。
 * 所以这里量一次顶栏的**底边**（不是高度：顶栏不一定贴着视口最顶）写进 CSS 变量 --flash-top，
 *   styles.css  .flash-stack { top: var(--flash-top, 16px) }          （桌面：fixed）
 *   同文件 ≤1100px 断点   { position: sticky; top: var(--flash-top) }  （窄屏：页面内横幅，
 *                          滚动时贴在工具栏下方而不是被它盖住）
 *   erp-ui.css  body:has(.erp-app) .flash-stack { top: var(--flash-top, 60px) }
 *                          —— 只作为「脚本还没跑」时的兜底，避免首帧跳到 16px
 */
(function () {
  'use strict';

  var GAP = 8;          // 提示与工具栏之间留的空隙
  var FALLBACK = 16;    // 页面上没有顶栏时的默认位置
  // 只扫「可能是顶部常驻栏」的节点，避免整页 getComputedStyle 拖慢首屏
  var CANDIDATES = 'header, nav, [class*="toolbar"], [class*="Toolbar"], .topbar, .erp-status, .erp-summary';

  function measure() {
    if (!document.body) return;
    var tallest = 0;
    var nodes = document.body.querySelectorAll(CANDIDATES);
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest('.flash-stack, dialog')) continue;      // 提示自己、以及顶层对话框不算
      var style = window.getComputedStyle(el);
      if (style.position !== 'sticky' && style.position !== 'fixed') continue;
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      var top = parseFloat(style.top);
      // top:auto 的（底部固定条、抽屉）parseFloat 得 NaN，直接排除；只认贴着顶部的
      if (!isFinite(top) || top < 0 || top > 24) continue;
      var rect = el.getBoundingClientRect();
      if (rect.width < window.innerWidth * 0.6) continue;    // 宽度不过半的不是通栏顶栏
      if (rect.height < 8 || rect.height > 240) continue;    // 全屏遮罩/抽屉（inset:0）高度过百
      // 取「底边」而不是「高度」：顶栏不一定贴着视口最顶（<main> 有内边距，ERP 页里
      // 实测顶栏底边在 51px 处、高度只有 31px），只让开高度的话提示会正好压在按钮上。
      // 未滚动时 rect.bottom 就是真实底边；滚动后 sticky 顶栏贴到 0，底边回到高度值，
      // 两个取大即可（页面刷新回来时总是 scrollTop=0，此时量到的就是需要的值）。
      var clearance = Math.max(rect.height, rect.bottom);
      if (clearance > tallest) tallest = clearance;
    }
    document.documentElement.style.setProperty(
      '--flash-top', (tallest ? Math.round(tallest) + GAP : FALLBACK) + 'px');
  }

  function schedule() {
    if (window.requestAnimationFrame) window.requestAnimationFrame(measure);
    else measure();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', schedule);
  } else {
    schedule();
  }
  window.addEventListener('load', schedule);
  window.addEventListener('resize', schedule);
  window.addEventListener('orientationchange', schedule);
  // 字体/图标是异步落地的，工具栏可能因此从一行变两行 —— 布局稳定后再量一次
  window.setTimeout(schedule, 400);
  window.setTimeout(schedule, 1200);
})();
