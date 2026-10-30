# ERP 操作按钮统一规范

所有继承 `base.html` 的管理页面自动加载 `static/erp-buttons.css`，位于页面自定义样式之后。此文件是操作按钮尺寸的唯一来源。

## 新页面写法

```html
<div class="actions">
  <a class="erp-btn" href="/previous">上一个</a>
  <button class="erp-btn" type="button" disabled>下一个</button>
  <form method="post">
    <button class="erp-btn primary" type="submit">保存</button>
  </form>
</div>
```

- 普通操作统一用 `erp-btn`；现有 `button` 样式类、照片选择标签和常用工具栏中的原生按钮自动兼容。
- 桌面：28px 高、12px 字号；手机（宽度不超过640px）：32px 高、13px 字号。共用18px行高、9px水平内边距、1px边框、2px圆角。
- 同一工具栏中的链接、普通按钮、提交按钮、文件选择按钮使用同一尺寸。禁用状态、主操作和危险操作只改变颜色/透明度，不改变尺寸。
- 不在页面样式、行内样式或媒体查询中单独设置按钮高度、最小/最大高度、内边距、行高、字号或圆角；需要调整密度时修改共享文件的令牌并运行回归检查。
- 页面专用样式可以定义布局、按钮宽度和语义颜色。长文本保持单行；窄屏通过工具栏换行安排，不把单个按钮拉高。
- 标签页、树节点、地图/相机操作、表格组件内部图标不属于普通操作按钮；保留专属组件尺寸，不添加 `erp-btn`。新业务页面必须继承 `base.html`，独立现场相机页面保持自己的触控布局。
- 保留 `hidden`、`display:none`、原生 `disabled` 及文件输入的可访问名称；不能为了统一外观破坏显示条件或上传行为。

## 验收

运行 `node --test tests/erp_buttons.test.cjs`（需要安装 Playwright 和 Chromium；使用本机 Edge 时设置 `ERP_BUTTON_BROWSER=msedge`）。测试覆盖日报查看/编辑、其他页面的内联样式以及桌面、手机、横屏尺寸；实际比较浏览器计算出的按钮高度、字体、边框与对齐，并检查隐藏状态、语义颜色和独立图标控件。

测试自动遍历所有模板的内联样式，新页面同样进入检查；禁止新增会与共享尺寸冲突的按钮 `!important` 规则。发布前必须运行该检查。
