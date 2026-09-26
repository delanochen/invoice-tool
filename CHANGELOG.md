# 更新日志 / Changelog

本文件记录每次版本发布的用户可见变更与重要内部修改，供人与后续自动化流程（发布、审计、AI 助手）了解各版本改动内容，避免重复排查已完成的工作。版本号与根目录 `VERSION` 文件保持一致，发布时以 `release: vX.Y.Z` 提交更新。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.1.301] - 2026-09-26

### 新增
- **「员工等级」页改成 ERP 风格工作台（左侧等级树 + 右侧三页签）**：右板块按等级分
  「员工分配 / 费率版本 / 等级资料」三页，等级切换在页面内完成，不再逐个等级导航。
- **等级下的员工分配可编辑**：新增 `POST /employee-grades/<id>/members`（`action=add|remove`），
  面板 1 用下拉把员工加入本等级（从别的等级选过来会自动改挂，并在审计日志里记录原等级），
  表格每行可把员工移出本等级。权限沿用 `employee_grades.edit`，同时补进
  `required_action_for_request()` 的 endpoint 映射。
- **「当前生效费率」直读块**：按 7 个结算项目显示工资计算当天实际取到的费率与来源
  （`版本 vN` / `等级静态费率` / `未设置`），没有版本时不再是一片空白；既无版本费率也无静态值
  的项目给红色提示，避免工资按 0 计算却看不出来。

### 修复
- **点员工等级会新开一个标签**：等级列表原来是 `<a href="/employee-grades?grade_id=N">`，
  而工作区外壳（`static/workspace.js`）会把 iframe 内所有 `<a href>` 点击拦成「打开新标签」，
  于是每看一个等级就多一个标签。现在等级条目是 `<button data-grade-switch>`，只做面板显隐，
  选中项用 `history.replaceState` 写回地址栏（不导航）；页面里已不存在指向本页的 `<a href>`。
- **「员工等级」页的费率与版本看起来是空的**：等级费率由「有效期版本」和「等级静态费率列」
  两级构成，而生产库里 `employee_rate_versions` 一条都没有 —— 实际参与工资计算的是静态列
  （`standard_hourly_rate` 等），但页面只渲染版本历史，静态费率只藏在「编辑等级」弹窗里，
  用户看到的就是「费率和版本都没显示」。现在两级费率都在页面上直读并标注来源。
- `rate_engine.py`：`employee_rate()` 里的静态费率回退映射抽成 `EMPLOYEE_STATIC_RATE_COLUMNS`，
  新增 `employee_grade_rate_snapshot()` 供页面复用同一口径，避免「展示的费率」与
  「计算用的费率」出现两套。

### 测试
- `test_employee_grades.py`：改写页面契约用例（ERP 外壳、等级切换必须是按钮、页面内不得有
  本页链接、`data-grade-panel` 数量），新增「加入/移出等级成员」「无 edit 权限 403」
  「静态费率回退 → 版本费率（含缺项回退）」共 5 项断言。

## [0.1.300] - 2026-09-26

### 变更
- **工单列表页移除关键词筛选栏**：工具栏下方的「关键词」输入框与说明文字整块去掉。
  本页只列「进行中」工单，条件查询（含已关闭）统一走「工单查询」页；工具栏的「查询」按钮
  一并移除（它唯一的动作就是提交这个表单），「刷新 / 导出 Excel / 打印」保留。
  后端仍接受 `?q=` 手工构造的旧书签，状态栏会显示「关键词 xxx」提示。
- 修复 `test_payment_terms.py` 一条过期断言（`data-selected-summary` 已随 v0.1.291 的
  详情页 ERP 化删除），该用例自那时起长期红。

## [0.1.299] - 2026-09-26

### 修复
- **项目利润页「按日汇总」的「完整性」列在冒充客户确认状态**：该列原先写成
  `status_pill(0, ['client_confirmed'])`，只要该日期分组里没有缺费率的明细，就无条件渲染
  绿色「客户已确认」。于是工单还在进行中（甲方尚未确认金额、工单没置 `closed`）也会被标成
  甲方已确认，与列名「完整性」以及 0.1.249 确立的业务口径（客户已确认 = 工单 `status` 为
  `closed`）都不符。现新增 `completeness_pill(incomplete)` 宏，该列只回答「数据齐不齐」：
  无缺费率 → 「完整」，否则 → 「缺 N 条费率」。利润明细、工单汇总、手机卡片继续用
  `status_pill` 显示真实业务状态，口径不变（方案 A，2026-09-26 用户选定）。
- 修掉 `erp_ui_profitability_test.py` 里一处过期断言：项目利润页早已改为页签导航、不再渲染
  SidebarTree，但测试仍要求 `.erp-nav` 存在，导致该用例长期 FAIL。

### 测试
- `test_profitability_status.py` 新增模板契约 1 项：断言 `completeness_pill` 宏存在且输出
  「完整」、汇总表已改为调用它，并锁住 `status_pill(0, ['client_confirmed'])` 只允许出现在
  利润明细面板（即恰好 1 处）——防止有人把硬编码状态再写回汇总列。
- `erp_ui_profitability_test.py` 新增 5 项行为断言：按日汇总面板已渲染、「完整」可见、
  面板内不再出现「客户已确认 / 结算中 / 预计」、缺费率时显示「缺 2 条费率」（且不显示「完整」）。

## [0.1.298] - 2026-09-26

### 修复
- **ERP 页面底部汇总栏被挤出屏幕**：`erp-report.js` 的 `fitViewport()` 用 `Math.max(available, 360)`
  给 `.erp-app` 设高度，而 `erp-ui.css` 里 `.erp-app` 又有 `min-height: 360px`。可用高度一旦小于
  360px（小屏笔记本、缩小窗口、浏览器 125%/150% 缩放、工作区 iframe 里嵌套时很容易发生），
  app 被硬撑到 360px，多出来的部分正好把底部汇总栏与状态栏顶到屏幕外。
  现改为精确贴合可用高度并清零 `min-height`（内联样式覆盖 CSS），让 body 行自己压缩、内容区内滚；
  同时把 `window.innerHeight` 换成 `document.documentElement.clientHeight`，避免横向滚动条
  那十几像素被算进可用高度。实测：窗口高 303/243/183 时修复前汇总栏超出视口 55/115/175px，
  修复后各档 `.erp-app` 底 − 视口底 = −20.0px、汇总栏可见高度 37.7/37.7px（完全可见）。
- **ERP 表格列间竖线是双线**：Tabulator 会在表头与每行 cell 里各插一个 6px 宽的
  `.tabulator-col-resize-handle`（列宽拖拽条），ERP 之前给它加了一条**常显**的
  `border-right: 1px dotted #9aa7bb`，而它落在相邻 cell 的 `border-right` 右侧约 4px 处 →
  每个列边界都画成两条竖线。改成平时 `transparent`、`:hover` 才显形：既不画假线，
  又保留「这里能拖」的可发现性。实测：截图取样每个列边界确有两条相距 4px 的线
  （`#eef1f5` 与 `#9aa7bb`），修复后 `#9aa7bb` 像素数 141 → 6（残留为悬停态）。

### 变更
- **服务人员清单列序与列宽调整**：列序改为
  人员 / 出行方式 / 行程类型 / **员工出发地** / 里程 / 交通时长 / 公共交通时长——
  「员工出发地」从第二列挪到三个短枚举之后，宽度 200 → 440px（地址文本最长，原来会截断）；
  「里程」「交通时长」200 → 95px、「公共交通时长」383 → 150px（都是数字，不需要那么宽）。
  实现上两处坑：① 镜像表格（Tabulator）的列宽与源表 CSS 的 `nth-child` 规则无关，
  宽度必须由 `<th data-grid-width>` 直传（`system-grid.js` 新增读取）；
  ② `fitDataStretch` **完全不看** `widthGrow`——它把剩余空间整块丢给最后一列，
  所以给源表加 `data-grid-filler` 造一条末尾空列来吸收剩余空间，否则宽屏下
  「员工出发地」会被撑到 1000px 以上。实测（容器 1793px）：
  130 / 115 / 105 / 440 / 95 / 95 / 150 + 661 填充。
  另同步修正 `styles.css` 里按列序写的 `nth-child` 宽度（原来已与新列序错位）。
- **站点地图筛选条 ERP 化**：控件高度 38~42px、圆角 6~8px、字号 16px 的 Web 风
  换成 26px 控件 / 2px 圆角 / 12px 字号的 ERP 紧凑风；折叠箭头改用实心小三角 `▾`
  （原来是旋转的 `>`）；选中态用 `--erp-accent` 描边 + 浅蓝底；顶部合计区 96 → 76px，
  地图可用高度 500 → 526px；窄屏（≤640px）控件放大到 32px 便于触屏。
  顺带修掉 `.erp-toolbar` 残留 `grid-area: toolbar` 在地图页把工具栏甩到页面底部的问题
  （该页 `.main` 本身也是 grid，会生成隐式行列）。

## [0.1.297] - 2026-09-26

### 修复
- **ERP 报表表格行间出现双横线**：`system-grid.css` 里 `.tabulator-cell` 自带 `border-bottom`，
  ERP 改造时又给 `.tabulator-row` 加了 `border-bottom`，而 cell 是 `inline-block` + `vertical-align: middle`、
  高度由内容决定（实测 23px < 行高 26px），于是 cell 的底线居中浮在行底线上方 3px ——
  两条线同时可见，且上面那条被列间缝隙切成一段段。
  现改为横线只由 `.tabulator-row` 画一条；同时让 cell 与行内容盒等高
  （`min-height: calc(var(--erp-row-height) - 1px)` + `box-sizing: border-box`），
  使列竖线贯穿整行、不再在行底断开 3px。实测：cell 底线由 `1px` 变 `0px`、
  行内横向线由 2 种变 1 种、行高保持 26px 不变。
- 预留保护：`.grid-parent-calc`（分组小计行）的 cell 维持 32px 行高，不被上述贴合规则压扁。

## [0.1.296] - 2026-09-26

### Fixed（工单日历等页面最大化时状态栏仍被挤出屏幕）
- 上一版修的是 iframe **内部**的 sticky（`--flow` 改 `display: block`），但没查外壳：`workspace.css` 里
  `.workspace-shell > .main { height: 100% }` 让 `.main` 取满 `100dvh`，却忽略了它上面还有个顶栏 →
  顶栏 + 100dvh 比视口高出一个顶栏高度，**iframe 底部（ERP 汇总栏 / 状态栏所在）被推出屏幕外**。
  实测：顶栏 55 + main 548 = 603 vs 视口 548 → 溢出 55px；iframe 底 − 视口底 = +55px。
- 修复：`.workspace-shell` 改 `display: flex; flex-direction: column`，顶栏 `flex: 0 0 auto`，
  `.main` 改 `flex: 1 1 auto; min-height: 0`（去掉 `height: 100%`），高度由 flex 分配。
  实测修复后：顶栏 + main = 548 vs 视口 548 → 溢出 0.0px；iframe 底 − 视口底 = 0.0px；iframe 高度 519 → 464。
- 起因说明：顶栏从单行改成「30px 标题栏 + 24px 菜单栏」后高 55px，把这个一直存在的溢出放大成肉眼可见的
  「状态栏消失」，所以看起来像是菜单改动的副作用。

### Fixed（汇总栏与状态栏重叠 2.2px）
- `.erp-summary { position: sticky; bottom: 22px }` 的 22px 是写死的，而 `.erp-status` 实际高 19.8px
  （padding 2px + 11px 字号），未滚动时两条栏重叠 2.2px、滚动到底又贴合，观感不一致。
- 修复：`erp-report.js` 的 `fitViewport()` 里把状态栏实测高度写入 `--erp-status-h`，CSS 改用
  `bottom: var(--erp-status-h, 22px)`；`.erp-status` 加 `min-height: 22px` 与兜底值对齐（无 JS 时也不重叠）。
  实测：汇总栏底 − 状态栏顶 = 0.0px，状态栏高 22px 与兜底值匹配。

### Notes
- 验证方式：从真实请求 dump 出 `/workspace` 外壳页 + `/service-orders/calendar` 页面（临时脚本 + 测试数据库），
  外壳里塞入指向日历页的 iframe，用 Chrome headless 渲染后由页面脚本把测量结果 POST 回本地服务器：
  外壳层断言 `iframe 底 − 视口底`、`顶栏 + main vs 视口`；iframe 内断言汇总栏 / 状态栏位置。
  增量 `test_workspace.py`、`test_formal_report_link.py`、菜单与 ERP 相关用例 103 passed。

## [0.1.295] - 2026-09-26

### Fixed（菜单栏有一条线横穿文字）
- 根因：`styles.css` 里的 `.nav-group` / `.nav-submenu` / `.nav-subgroup` 是给旧「左侧深色 sidebar」写的，`erp-topnav.css` 上一轮只覆盖了 `margin`，没覆盖 `padding`。残留的 `padding-top: 8px` 把菜单内容整体下推，24px 的菜单栏装不下 → `#topnav` 的底边框正好落在文字中下部，看起来像「一条线挡住字」。
- 修复：`#topnav .nav-group` 显式 `padding: 0; margin: 0; border: 0; border-radius: 0`，一级 `summary` 同样清零 `border-radius` 并 `justify-content: flex-start`（清掉 `space-between`，窄屏压缩时不会把图标与文字拉开）。
- 顺带清掉同一批旧规则的三处残留：一级菜单 `summary::after` 的 `">"` 折叠箭头（`content: none`）、`.nav-submenu` 的 `margin-top: 6px`（面板与菜单栏之间多一条缝，鼠标划过会「掉出」菜单）、`.nav-subgroup[open] summary::after` 的 `rotate(90deg)`（级联 ▶ 展开后被转成 ▼，现恒指右）。

### Fixed（鼠标移开菜单不收起）
- 新增 C/S 收合行为：鼠标离开整个菜单区域（含下拉面板，面板是 `#topnav` 后代故不会误触发）后 260ms 自动收起全部菜单；短延时用于兜住「菜单栏 → 面板」的移动过程。`matchMedia('(hover: hover)')` 保护，触屏不受影响。
- `closeAll()` 现在连二级 `.nav-subgroup[open]` 一起复位，下次打开不残留上次展开的层级；点菜单栏空白处也会收起；键盘 Tab 走出菜单范围同样收起。
- 移动端抽屉里一级菜单补 ▼/▲ 展开指示（桌面菜单条不显示箭头）。

### Notes
- 验证方式：用系统 Chrome headless 渲染真实 `styles.css` + `erp-topnav.css` + 照抄 base.html 的 DOM，由页面内脚本测量 `getBoundingClientRect` 与 `getComputedStyle` 并输出断言（旧规则残留是否为 0、菜单项是否溢出菜单栏 24px、面板与菜单栏是否零缝隙、mouseleave 后 `open` 计数），900px 与 500px 两种宽度均通过。

## [0.1.294] - 2026-09-26

### Fixed（ERP 页面状态栏被挤出屏幕）
- 根因：`--flow` 页面（工单日历等）里 `.erp-summary` / `.erp-status` 是 **grid item**，它们的包含块就是自己所在的网格行（行高 == 自身高度），`position: sticky; bottom: 0` 没有可移动空间 → 内容一高（6 周的月历、最大化后仍超高的窗口）汇总栏与状态栏就被顶到屏幕外，只能滚到底才看得到。
- 修复：`erp-app--flow` 改回 `display: block`，工具栏 / 汇总栏 / 状态栏成为长文档里的普通块，sticky 才真正贴住视口常驻（工具栏粘顶、汇总栏与状态栏粘底）。
- 另外 `erp-report.js` 新增 `fitViewport()`：不再依赖写死的 `--erp-offset`（菜单高度 / main 内边距 / 工作区 iframe 高度任一变化都会让它失真，偏差为正时底部溢出视口），改为按实际可用高度精确赋值，并监听 resize / ResizeObserver 重算。
- 右下角浮动版本号移入 ERP 状态栏最右（C/S 客户端惯例），不再压住状态栏右端。

### Changed（顶部菜单改 C/S 客户端观感）
- 顶栏拆成两行：上排标题栏（品牌 / 语言 / 用户 / 退出，30px），下排菜单栏（24px 细条，浅灰渐变 + 1px 分隔线）。
- 菜单项 12px、直角无圆角；悬浮浅蓝高亮 + 描边，打开时深蓝底白字呈「按下」态；下拉面板直角、1px 边框、硬阴影（3px 3px 6px），整行深蓝高亮。
- 一级菜单带下划线访问键：`主菜单(M) 报表(R) 薪酬管理(P) 实用工具(T) 基础数据(D) 系统配置(S)`（Alt+字母），子菜单内 `薪酬参数` 改为向右级联弹出并带 ▶ 箭头，菜单内支持分隔线，当前页菜单项左侧带选中圆点。
- 交互向客户端靠拢：已打开某菜单时鼠标划过相邻菜单直接切换；支持 ↓ / ← / → / Enter / Esc 键盘操作；移动端抽屉与 `#topnav a` 钩子保持原样（workspace.js 依赖）。

### Changed（A 组报表页 ERP 风格改造）
- 改为 `.erp-app` 体系（工具栏 / 筛选区 / DataGrid / SummaryBar / StatusBar / 手机端卡片）：`/service-orders` 工单主列表、`/reports/payroll` 薪酬统计、`/reports/payroll-details` 薪酬明细、`/reports/labor-hours` 工时统计、`/reports/field-photos` 工单照片台账、`/reports/field-repairs` 设备维修清单、`/messages` 消息。
- `/expenses/<id>` 报销详情与结算前审核改为 `erp-app--flow`：内容高度不定，整页滚动但工具栏 / 汇总栏 / 状态栏常驻。
- 以上页面均为**展示层改造**，筛选字段名、批量下载/删除所需的 `data-*` 与 ID、`messages.js` / `photo-ledger.js` / `repair-delete.js` 依赖全部保持原样，后端逻辑未改动。
- 附带修正：移动端隐藏桌面网格的规则改为 `.erp-app:has(.erp-cards)`——没有卡片承载数据的页面（照片台账等）不再整张表凭空消失。

### Changed（录入型页面紧凑化）
- `erp-ui.css` 新增 `.erp-compact` 层：26px 控件、2px 圆角、12px 字号、5px 行距、面板 9px 内边距，把 styles.css 的大间距版式压成客户端密度；规则限定在 `.erp-compact` 内，不影响其它页面。
- 应用到工单 / 发票 / 报销 / 工单结算 / 用户 / 客户 / 站点 / 项目 / 工单类型 / 补贴参数等录入页：统一 ERP 工具栏（返回 / 保存 / 提交 / 删除）+ 底部状态栏，字段名与 `name` 全部保持原样。

### Fixed
- 薪酬明细报表手机端卡片「补贴类」金额 `a + b + c|money` 过滤器优先级错误（TypeError: float + str），改为先求和再格式化。

## [0.1.280] - 2026-09-24

### Changed（日报「读取」维修清单改进）
- 「读取」按钮从每个员工行移到**现场服务描述**字段旁；点击读取该工单**报告日期当天**的设备维修清单（位置号 + 铭牌号 + 备注，一台设备一行追加，已有内容不覆盖、重复行去重）。
- `/api/field/repairs/order/<id>` 新增可选 `?date=YYYY-MM-DD` 过滤，只统计当天拍摄的维修照片；日期格式非法返回 400。
- 员工清单表格删除「工作内容」列（维修清单统一写现场服务描述）。后端保存逻辑按列名索引取值、字段缺失自动回退空串，无需改动；数据库历史数据保留。

### Fixed（生成里程佐证不再「网络错误」）
- 根因：每台设备最坏 ~96 秒（路线/静态地图各 3 次×15s 重试），多台设备超过 gunicorn 180s 超时被切断连接，前端只能显示「请检查网络后重试」。
- 该接口的 Google 客户端改为 fail-fast（timeout=8、max_retries=1，单设备最坏 ~34s）；端点整体 try/except，异常返回 JSON 中文提示（数据无改动）；gunicorn 超时 180→300；前端防御性解析响应，区分网关超时 / 权限 / 服务器错误 / 断网并显示真实原因。

### Changed（工单日历月历布局）
- 月历高度随周数变化，锁死视口会把格子压扁：新增 `erp-app--flow` 例外布局回到文档流，排版保持 ERP 紧凑观感，一页放不下时整页滚动；并修正 flow 规则对基础 `overflow:auto` 规则的 CSS 特异性覆盖。

## [0.1.279] - 2026-09-24

### Changed（合同管理页 ERP 风格改造）
- `/contracts`（`templates/contracts.html`）改为 `.erp-app` 体系：工具栏（新建合同 / 查询 / 重置 / 筛选 / 刷新 / 导出 Excel / 打印）、筛选区、DataGrid、SummaryBar（合同数 / 生效中 / 关联工单）、StatusBar、手机端卡片。单视图 → 不设页签也不设侧栏，走无导航单列布局。
- **补上后端已支持但旧模板漏掉的关键词筛选**：`q` 同时匹配合同编号、合同名称、客户名称与项目名称（后端 SQL 本来就查，只是没有输入框）。
- 金额列带 `data-grid-currency`，表尾合计由表格按币种分别汇总；纯展示层改造，后端 SQL 未改。

### Changed（合同详情页 ERP 风格改造）
- `/contracts/<id>`（`templates/contract_detail.html`）改为 ERP 详情：顶部工具栏放状态与操作（返回列表 / 费率版本 / 编辑 / 删除），正文用**四个页签**分组——合同信息 / 附件（N）/ 关联工单（N）/ 关联发票（N），页签上直接带条数。
- 各页签内容是表格时用 `erp-grid` 内部滚动；合同信息字段区用新增的 `.erp-detail-scroll` 自己滚动，**不顶出整页滚动条**。
- SummaryBar：附件 / 工单 / 发票数量 + 合同金额 + 期限。后端数据与接口未改。

### Changed（工单日历 ERP 风格改造）
- `/service-orders/calendar`（`templates/service_order_calendar.html`）改为 ERP 布局：工具栏（上个月 / 本月 / 下个月 / 工单列表）+ 月历内容区 + SummaryBar（工单数 / 排期天数 / 排期条数）+ StatusBar。
- 月历高度随周数变化，超出可视高度时在新增的 `.erp-calendar-scroll` 内滚动。
- **修整页滚动条**：该页 `.main` 上下各 18px（`styles.css`，比默认 12px 多 12px），ERP 高度按 24px 算会多出 12px 而冒出页面级滚动条；现在按页面修正（`--erp-offset: 140px` / 嵌入时 `calc(100vh - 36px)`），整页溢出 0。

### 测试
- 相关回归 83 项 + 9 subtests 通过；三个页面均实测：单列布局、页签切换、金额两位、整页溢出 0（日历页改为内容区内部滚动）。

## [0.1.278] - 2026-09-24

### Fixed（删除报销报 Internal Server Error）
- **现象**（用户报告）：用 A 账号删除 B 提交的报销，直接显示英文 Internal Server Error 页。
- **根因**：删除时只清了 `expense_attachments` / `expense_items`，但 `expense_save_tokens` 和 `expense_duplicate_checks` 也外键引用 `expenses`——其中 `expense_duplicate_checks.matched_expense_id` 会被**别人的**重复报销检查记录指向这条报销。SQLite 的外键带 `on delete cascade`（本地根本复现不出来），PostgreSQL 生产库不一定有，删到引用行就抛外键错误 → 500。
- **修复**：删除报销前显式清理这两张表的相关行（`expense_id` 或 `matched_expense_id`）；删除报销附件时同样先清引用该附件的检查记录（`attachment_id` / `matched_attachment_id`）。
- **失败不再 500**：删除失败时回滚事务后抛 `RuntimeError`（项目惯例），由全局处理器转成页面上的中文提示：「删除报销失败，数据没有改动：这条报销还被其它记录引用（例如重复报销检查记录、发票或工单结算）。请先解除这些引用，或联系管理员处理。」

### Added（全局 500 兜底）
- 新增 `errorhandler(500)`：任何未捕获异常都渲染中文提示页（`error.html`，说明数据未被改动、可返回重试/联系管理员），真实堆栈只写日志。**不再出现 Werkzeug 的英文 500 页。**

### 测试
- 新增 `test_expense_delete.py`（4 项）：清理 `expense_save_tokens` / `expense_duplicate_checks`、清理「被别人 `matched_expense_id` 指向」的检查记录且不动对方报销、删除失败给友好提示且数据不丢、删附件时清理引用该附件的检查记录。
- 报销相关回归 37 项 + 5 subtests 通过。

## [0.1.277] - 2026-09-24

### Added（发票明细查询页 ERP 风格改造）
- `/reports/invoices`（`templates/reports.html`）改为 `.erp-app` 体系：顶部工具栏（查询 / 重置 / 筛选 / 刷新 / 导出 Excel / 打印）、筛选区、DataGrid、SummaryBar（明细数 / 发票数 / 金额小计 / 税额合计 / 价税合计）、StatusBar、手机端卡片列表。
- **单视图不设页签也不设侧栏**：只有「发票明细」一个视图，因此自动走 `erp-ui.css` 的无导航单列布局（与日报查询页同款），不白留 208px 导航列。
- 筛选改为通用多选组件（发票状态 / 核销状态 / 项目），字段名与后端完全一致；金额 / 税额 / 合计列带 `data-grid-currency`，**表尾合计由表格按币种分别汇总**（多币种不再混加）。
- 纯展示层改造：SQL、税额计算（`金额 × 税率`）与导出接口均未改动。

### Changed（时间数量统一保留一位小数）
- 新增 Jinja 过滤器 `hours`：`float(value or 0)` 保留一位小数，用于所有**只读展示**的工时 / 时长 / 小时数。
- 覆盖：日报查询（清单 / 按工单汇总 / 按站点汇总 / 汇总栏 / 手机卡片）、工时统计（指标卡 + 表格）、工资单与薪酬明细、日报查看页、工单详情的日报表、客户结算表头列合计、行程工具路段时长、工资日历工资条、辅助填写预览。
- **表单 input 值一律不改**：可编辑（含 readonly）的工时输入框必须保留原始精度（如 0.25 小时），否则保存时会把用户填的值改掉。
- 金额与单价仍走 `|money` 两位小数，里程仍保留两位——只有时间数量改为一位。

### Fixed
- `test_payroll_details.py` 的结算 Excel 用例把快照 mock 成空 `dict`，而合计行末列要读 `total_amount`（v0.1.274 加合计行后遗留），抛 `KeyError`。mock 补上该字段。

### 测试
- 相关回归 155 项 + 8 subtests 通过（日报 / 工资 / 薪酬 / 报销 / 行程 / 盈利 / 辅助填写）。

## [0.1.276] - 2026-09-24

### Fixed（ERP 报表：页签栏 / 筛选栏被压缩成一条线）
- **现象**：日报查询、项目利润两页的页签行（「工单汇总 / 利润明细 / …」）在数据多或窗口高度不足时被压成几像素高的细线，看起来像被筛选栏「遮挡」。
- **根因**：页签栏与筛选栏都是 `.erp-body` 的 flex 子项，默认 `flex-shrink: 1`；可用高度不足时浏览器为腾空间压缩它们。实测页签行 31px → 10px，**并非被其它元素盖住**（页签中心点命中的仍是页签自身）。
- **修复**：`.erp-body > .erp-tabs` 与 `.erp-body > .erp-filter` 设为 `flex: 0 0 auto`——页签栏与筛选栏是常驻框架，只占自身高度；高度不足时由面板内部滚动解决，不靠压缩框架。
- **验证**：向面板注入超高内容（模拟 241 条明细 / 可用高度不足），修复前页签 10px、修复后稳定 31px，筛选栏 87px，底部汇总栏不漂移，整页仍无纵向滚动。

### Changed（ERP 报表：去掉与页签重复的左侧「报表视图」菜单）
- 日报查询页、项目利润页的左侧 SidebarTree 与顶部页签是同一组视图入口，删除侧栏。信息搬进页签，不丢：
  - 日报查询页：三个页签补条数（日报数 / 工单数 / 站点数）
  - 项目利润页：四个页签补条数与「缺费率」警示点（`tab_button` 宏新增 `count` / `warn` 参数，口径与 `nav_item` 一致）
- `erp-ui.css`：`.erp-app:not(:has(> .erp-nav))` 自动改为单列布局，无侧栏的页面不再白留 208px 导航列；有侧栏的页面（员工证书：左侧是筛选入口，没有对应页签，不属于重复）布局不变。

### 测试
- 合并远端 v0.1.275（PostgreSQL 增量 schema 升级）后重跑：237 项通过、6 项跳过、9 subtests（含 `test_postgresql_schema_upgrade.py` 14 项与 `test_postgresql_deploy.py` 6 项）。
- 页签切换（含点页签内文字）、手机视口 400px（页签可见、卡片列表生效）均已核对。

## [0.1.275] - 2026-09-24

### Fixed（紧急：PostgreSQL 生产库编辑/复制工作日报 500）
- **现象**（用户报告）：v0.1.274 自动部署后，工单日报的「编辑」与「复制日报」直接显示 Internal Server Error；保存日报、里程佐证等路径同样受影响。
- **根因**：v0.1.274 为 SQLite 在 `init_db` 中新增了 `service_report_workers.origin_address` / `trip_type` 两列和 `service_report_mileage_evidence` 表，但 PostgreSQL 模式从不执行 init_db 的 DDL（schema 变更必须走迁移工具，运行角色无权建对象），而 0252 冻结基线之后没有任何增量机制——生产 PG 库缺列，`service_report_workers()` 等查询直接报错。所有加载「已有日报服务人员」的页面（编辑/复制/查看/保存）全部命中，纯新建页面不受影响。
- **修复**（不 bump `invoice_schema_version`，纯增量、幂等、随部署自动生效）：
  - 新增 `migrations/postgresql/0274-report-workers-trip.sql`：`ADD COLUMN IF NOT EXISTS` 两列 + `CREATE TABLE IF NOT EXISTS` 里程佐证表（identity 主键、`UNIQUE(report_id, worker_user_id)`、级联外键）+ 报表索引 + `invoice_sqlite_columns` 记账（PRAGMA table_info 在 PG 的翻译源），与 SQLite 定义逐字段对齐。
  - 新增 `scripts/upgrade_postgresql.py`（唯一增量入口，fail-closed）：校验版本标记与实际 catalog 形态（0252 基线或已升级，其余拒绝）→ 在 postgres 容器内以迁移角色 `psql --single-transaction + ON_ERROR_STOP` 应用（失败自动回滚）→ 复核形态后才报告成功；已升级则幂等跳过。
  - `scripts/debian-auto-deploy.sh` 在每次部署尝试（备份之后、`already current` 短路之前）对 PostgreSQL 后端执行该升级，保证「schema 先于其消费代码就位」；升级失败中止部署。无需人工登录服务器，推送后由 5 分钟定时器自动完成修复。
  - 测试装置修复：`test_postgresql_deploy.py` 的组合脚本改用临时文件执行（`sh -c` 在 Windows 原生 Python + MSYS2 下会损坏内嵌引号；文件模式与生产执行方式一致，Linux 行为不变）。
- **后续建议**（未包含在本版）：未来的破坏性/语义 schema 变更需要新版本标记并配套迁移演练；流程已写入 `docs/postgresql-cutover-runbook.md`「0252 基线之后的增量 schema 变更」。

### 测试
- 新增 `test_postgresql_schema_upgrade.py`（14 项）：升级器状态机（基线→应用、已升级→跳过、版本标记不符/形态异常→拒绝）、单事务应用与 stdin 内容契约、迁移 SQL 只增不改 + 幂等语句 + 记账行齐全、部署脚本接线位置（备份后、fetch 前）与失败中止。
- 回归：`test_postgresql_deploy.py` 6 项全部通过；`sh -n` 部署脚本、`py_compile` 升级器、sqlglot（postgres 方言）解析迁移 SQL 均通过。

## [0.1.274] - 2026-09-24

### Added（工作日报「辅助填写」：照片、进出场时间、出发地与里程一次填好）
- **入口**：日报表单工具栏新增「辅助填写」按钮，按「工单 + 报告日期」产出建议，**全程只读、不写库**——前端把勾选的建议值填进表单，用户点保存才落库。新增页没有 `report_id` 也可用（`can_edit_report=True`）。
- **接口** `POST /api/service-reports/assist-plan`：入参 `order_id` / `report_date` / `workers[]` / `site_address` / `departure_address`；人员必须在工单可选范围内，越权 id 一律忽略（避免取到别人地址）。
- **照片**：按 `field_photos.photo_type` 归类为到达 / 离开 / 自检 / 现场服务四类；到达时间取该类最早、离开取最晚；现场服务照片按拍摄时间取前 10 张（超出时预览提示「共 N 张，已取前 10 张」）；取不到水印时间的**不猜**，单独提示。
- **出发地与里程**：出发地取值链「表单已填 > 员工主数据地址 > 日报级出发地址」；里程与交通时长走 Google Routes + `trip_policy`（往返 ×2，时长沿用 1.15 冗余 + 0.25h 向上取整）。同一份 plan 内同起终点只请求一次 Google（有缓存）。
- **降级逐条说明**：随行 / 飞机不算驾车里程、缺出发地、缺目的地、未配置 Google Routes 都写明原因，能填的部分照常填。
- **前端策略**：里程 / 交通时长 / 出发地**只填空值**（手工填过的不覆盖）；照片沿用 `service-report.js` 的 NAS 通道（`nasSelections`），落库路径与「服务器选择」完全一致，不新增上传通道。
- **薄适配层** `service_report_assist.py`：只做「表单结构 ↔ 已有服务入参」的翻译，照片发现、拍摄时间、里程换算、行程口径分别复用 `PhotoDiscoveryService` / `PhotoMetadataService` / `MileageService` / `trip_policy`，**不重新实现任何口径**。

### Added（里程佐证：生成 / 复用 / 地址变更自动重做）
- 日报表单新增「生成里程佐证」，用 `MileageEvidenceService` 合成静态地图佐证并挂到附件分类 `mileage_proof`；接口 `POST /service-reports/<id>/mileage-evidence/generate`（`force=1` 强制重做）。
- **按线路指纹判断是否过期**：出发地 / 目的地 / 行程类型变化 → 指纹变化 → 自动重新生成；指纹一致且附件还在则复用。同一员工重新生成**复用同一附件行**（`save_generated_report_attachment` 换文件并清掉旧文件），不会每点一次多一行。
- 新增表 `service_report_mileage_evidence`（`report_id + worker_user_id` 唯一）记指纹 / 里程 / 附件 id；`service_report_workers` 补 `origin_address`、`trip_type` 两列（`trip_type` 默认 `round_trip`，与其他入口同口径）。

### Added（设备维修清单「读取」进工作内容）
- `field_work.py` 把设备维修清单的聚合逻辑抽成模块级 `group_repair_photos()`（与 `/reports/field-repairs` 表格同口径，两处不再各写一遍），并新增 `repair_work_text()` 生成「位置号 + 铭牌号 + 备注」文本。
- 新增 `GET /api/field/repairs/order/<order_id>`：按工单返回设备行与可直接写入工作内容的文本（多台设备换行）。
- **服务报告表单**：工作内容由单行 `input` 改为 `textarea`，每行加「读取」按钮，一次把该工单当天的设备维修清单添进工作内容。
- **AI 智能日报草稿详情**：人员行新增「工作内容」，同样带「读取设备维修清单」按钮；`WorkerTravel` 增加 `work_description`（与里程计算无关）。
- `POST /api/ai/daily-report/draft/<id>/update-worker` 支持 `work_description`，并且**只有路程相关字段（出发地 / 行程类型 / 出行方式）真的变了才作废里程**——单独改工作内容不再逼用户重算里程。

### Changed（「日报查询」「员工证书」两页 ERP 风格改造）
- 两页统一改为 `erp_ui.html` 宏 + `erp-ui.css` 体系：顶部工具栏 / 筛选 / SummaryBar 常驻，桌面端高密度 DataGrid、移动端卡片列表；均为**只读展示层分组合计**，后端 SQL 与接口未改动。
- `static/erp-report.js` 适配这类面板：清空多选时派发 `change` 让「全部」文案同步；面板计数取当前活动面板，避免隐藏面板量不到高度。

### Fixed
- **租车驾驶拿不到里程佐证**：`MileageEvidenceService` 原先只认 `self_drive`，工作日报侧租车（`rental_drive → rental_car`）员工无法生成佐证。放宽为 `self_drive` / `rental_car`（两者都是自己开车）；佐证正文不使用该字段，AI 智能日报既有行为不变。
- **`test_ai_daily_report_phase7` 在 Windows 上必然失败**：测试以默认编码（GBK）读 UTF-8 源码导致 `UnicodeDecodeError`，改为显式 `encoding="utf-8"`。

### 测试
- 新增 `test_service_report_assist.py`（22 项）：照片归类与到达 / 离开时间取最早最晚、现场照片上限与排序、时间不明不猜、出发地取值链、往返翻倍、路线缓存去重、佐证指纹复用 / 重生成 / `force`、接口入参校验。
- 本版相关回归 263 + 111 项通过；「辅助填写」另做浏览器端到端核对（真实点击 + 桩数据验证「应用到表单」的填值与只填空值策略）。

## [0.1.273] - 2026-09-24

### Changed（行程类型取代「是否住宿」：全系统共用一套口径）
- **背景**：里程是否翻倍原先由 AI 智能日报的「当天是否住宿（`overnight_stay`）」反推（不住 → 里程 ×2），隐含且易误解，LLM 还需额外追问一次住宿；工作日报（服务报告）侧则完全没有行程口径，两边迟早分叉。
- **新增单一口径源** `trip_policy.py`：取值只有 `round_trip`（往返，默认）与 `one_way`（单程），提供 `normalize_trip_type` / `trip_multiplier` / `trip_label`。AI 智能日报与后续工作日报「辅助填写」都从这里取口径，**禁止各自推导或再用布尔反推往返**。
- **数据模型**：`WorkerTravel`、`MileageEvidence`、`AIAction` 的 `overnight_stay` 全部替换为 `trip_type`；`model_validator(mode="before")` 自动迁移存量草稿与佐证（住宿 `true` → 单程，不住宿 `false` → 往返），历史草稿金额口径不变，模型层不再保留旧字段。
- **统一计算口径**：`reported_miles = 单程英里 × trip_multiplier(trip_type)`（往返=2）；交通时长同按倍数放大，再沿用 v0.1.243 的 1.15 冗余系数并向上取整到 0.25 小时。
- **路线不再被阻塞**：旧逻辑要求 `overnight_stay` 非空才允许调用 Google Routes；改为默认往返后行程类型不再是「缺失字段」，起终点确定即可算里程。
- **校验与提示**：删除 `OVRN-001`（未确认住宿）；`MILE-005` 改为按行程类型判定倍数；LLM 提示词不再追问住宿。
- **前端 / 接口**：员工出行区下拉改为「往返 / 单程」（默认往返）；`POST /api/ai/daily-report/draft/<id>/update-worker` 改收 `trip_type`（仍兼容旧 `overnight_stay` 传参），行程类型变化即触发路线作废重算。
- **测试**：11 个 AI 日报测试文件的住宿用例改写为行程类型用例，补充往返/单程倍数、默认值、旧数据迁移、接口兼容四类断言。

### Fixed
- **知识库上传异常路径崩溃（回归）**：`app.py` 中为静态检查补的 `saved = None` 初始化与残留的 `"saved" in locals()` 判断冲突——`saved` 永远"已定义"，异常分支会对 `None` 取下标抛 `TypeError`，掩盖真实错误（如 PDF 校验失败）并泄漏临时文件。改回 `if saved is not None:` 后 `test_knowledge_base` 恢复通过。
- **新建发票变量未定义**：`new_invoice()` 用 `source_reimbursement` 做判断却读 `source_order`，非报销来源时会 `UnboundLocalError` / 选错客户。统一按实际赋值的 `source_order` 判定。
- **Tabulator 在隐藏面板初始化**：ERP 面板 `display:none` 时量不到高度，切过去表头被压扁、明细看似为空。面板切换后在 `requestAnimationFrame` 内对当前面板的 grid 强制 `redraw(true)`。
- **出行工具宾馆卡片缺路段明细**：宾馆结果补齐 `leg_miles` / `leg_hours` / `total_miles` / `total_hours_text` / `destination`，前端新增与「路线地图」同款的路段表（宾馆→终点），并在摘要里注明图片为 A→B 折线、可直接作为行程佐证。
- **盈利报表打磨**：移除冗余的查询/重置按钮（筛选即时生效），利润来源卡片改为与 SummaryBar 一致的左对齐紧凑指标（正负分色），明细与汇总表通过 `data-grid-calcs="bottom"` 只保留表尾合计行（`system-grid.js` 新增该 opt-in 开关）。
- **类型噪音**：`database.py` / `scripts/migrate_postgresql.py` 的可选依赖 `import psycopg` 补 `# type: ignore[import-not-found]`；删除 `app.py` 中的冗余局部 `import json`。

### 补记
- 提交 `4b6d79d`（工单报表 ERP 风格 UI 改造：桌面高密度 DataGrid + 手机卡片列表）当时未升版本号也未记 CHANGELOG，本条「盈利报表打磨」即该改造的后续调整，一并纳入本版。

## [0.1.272] - 2026-09-21

### Fixed（工单结算金额翻倍修复 · 方案 A）
- **背景**：工单结算「结算合计」会出现整行金额翻倍。经排查为三根因叠加：①前端 `reimbursementInputValue` 把单元格 input 值再叠加 `data-auto-amount`，显示层翻倍；②后端合并 `merge_approved_expenses_into_customer_reimbursement` 用「人名 + 报销填单日」精确匹配「人名 + 日报工作日」，异日则新建一行，同一来源被计两次；③PostgreSQL 经 psycopg 回传 `datetime`，`str()` 得 `'2026-09-18 00:00:00'`，与 SQLite 文本 `'2026-09-18'` 不等，匹配键失效——生产（PG）翻倍比本地（SQLite）更严重。
- **根因①前端**：`static/customer-reimbursement.js` 的 `reimbursementInputValue` 改为只取 `input.value`（生效金额本就是 input 已填值），不再叠加 `data-auto-amount`。口径与 `system-grid.js` 完全一致。
- **根因②③后端（双栈红线）**：新增 `_reimbursement_date_key()` 把日期统一归一成 `'YYYY-MM-DD'`（兼容 `date`/`datetime`/含 `T` 或空格的 ISO 字符串）；合并时 row 匹配键与新建行 `project_date` 均走该归一；新增 `_fallback_row_for_worker()`——精确匹配落空时回落到同一员工的已有明细行（单行直接复用，多行取最近日期），避免「同人异日拆行」导致重复计入；仅在回落也为空时才新建行。
- **手改跟随来源**：`save_customer_reimbursement_items()` 入库前把「人工值 == 来源值」的单元格归一为 0，避免被误判成手改值而不再跟随来源（翻倍语义清理）。与 0.1.258「手改优先、人工+auto 绝不相加」口径一致。
- **方案 A 快照为准**：结算详情页/PDF/Excel/结算详情页四处统一读落库快照，GET 期不再调 `update_customer_reimbursement_totals()` 覆盖。新增只读比对 `customer_reimbursement_pending_sources()`（三档 `includable`/`pending`/`returned`，零写库）+ 结算页横幅「计入并重算」→ 新端点 `POST /service-orders/<id>/customer-reimbursement/sync-sources`（仅 draft/returned 可用，显式重算快照）。
- **三出口 gate**：`customer_reimbursement_gate_error()`——已审核未计入硬拦（无论是否确认）、待审核默认拦（带 `confirm_pending=1` 可放行）、已退回不拦；接入 submit / generate_invoice / send_email / approve。前端 submit 按钮待审核时带 `data-confirm-pending` 二次确认。
- **MRO 口径铁律（勿再把 mro 加进 total）**：`customer_reimbursement_totals()` 的 `total_amount = Σ item["total"] + rental_fuel_total`；MRO Supplies 经 `CUSTOMER_REIMBURSEMENT_EXPENSE_FIELDS` 映射到 `other` 字段已通过 `auto_other` 计入合计，`mro_supplies_total` 参数只用于快照列/PDF/发票展示，绝不能加进合计（自洽：`total_amount - (labor+lodging+other+mileage) == rental_fuel_total`）。迁移 SQL 里 `+ mro_supplies_total` 是老数据一次性修补，非现行口径。
- **Excel 导出**：`download_customer_reimbursement_excel()` 明细后追加合计行（逐列求和，末列=快照 `reimbursement["total_amount"]`）+ 口径说明行（租车油费不在明细行、MRO 已计入其他列）。
- **测试**：`test_reimbursement_expense_display.py` 新增 `ReimbursementPlanARegressionTest`（22 项），覆盖日期双栈归一、禁用 fallback 复现翻倍（2 行）、fallback 后不拆行（1 行）、manual==auto 归一为 0、MRO 参数不改 total 且自洽、gate 四分支、Excel 合计行，以及路由级集成（submit 被 gate 拦 → sync 计入 → 再 submit 成功、横幅渲染）。

## [0.1.271] - 2026-09-21

### Changed（侧边栏：「知识库」与「智能助手」移入「实用工具」分组）
- **背景**：0.1.269 把 AI 日报审查、出行工具、数据库工具收进「实用工具」折叠组，但「知识库」与「智能助手」仍挂在「主菜单」下，与它们同类的工具型功能分散在两处。本版把这两项也并入「实用工具」，让主菜单只保留日常业务入口（看板、合同、工单、现场工作、站点地图、工单日历、报销处理、新建发票、消息）。
- **改法**：`MENU_PERMISSION_GROUPS` 中把 `knowledge_base`、`ai_assistant` 两个条目从「主菜单」组移除，插入「实用工具」组的 `travel_tools` 之后、`database_console` 之前。组内顺序为：AI 日报 → 出行工具 → 知识库 → 智能助手 → 数据库工具。
- **权限零变更（重要）**：两个条目的 `roles` 原样保留（`{"admin", "manager", "finance", "employee"}`），`DEFAULT_MENU_ROLES` 与 `ROLE_ACTION_PERMISSION_GROUPS` 均未改动，因此本版是**纯重组**——谁能看到「知识库」「智能助手」与之前完全一致，不涉及任何授权变化，也不需要数据迁移。
- **侧边栏模板**：`templates/base.html` 中把两个顶层 `nav_link` 移入「实用工具」`<details>` 块（紧接出行工具之后、数据库工具之前）；`utility_endpoints` 扩充 `knowledge_base` 页面及其 11 个子端点（预览/下载/编辑/上传版本/预览版本/下载版本/删除）与 `ai_assistant`、`ai_assistant_chat`，使访问这些页面时「实用工具」组自动展开并高亮；`show_utility_menu` 同步纳入两个菜单权限与 `view` 动作权限判定。
- **多语言**：`static/ui-i18n.js` 为 `nl` / `de` / `es` 三份词典补上「知识库」「智能助手」的译名（Kennisbank / AI-assistent、Wissensdatenbank / KI-Assistent、Base de conocimiento / Asistente de IA），英文词典原本已有。新增项合并进各自已有的 `"实用工具"` 行，避免在同一对象里出现重复键造成遮蔽。
- **测试**：`test_utility_menu.py` 由 8 项扩到 13 项，覆盖分组归属与顺序、从旧分组移除、每个菜单键恰好定义一次、默认角色未变、组内五个链接齐全、组后不再出现顶层重复、`utility_endpoints` 含新端点、以及三份词典各自的定义次数。该文件 13 项全过；连同知识库与 AI 日报相关用例共 26 项通过。

## [0.1.270] - 2026-09-21

### Changed（站点地图：按访问国家自动选择地图模式，中国用户首屏即静态图）
- **背景**：0.1.268 的自动回退能兜住"加载不到 Google"的情况，但首次访问仍要先等 10 秒超时才切换，中国用户每次打开新浏览器都会看到一段空白等待。
- **改法**：服务端按访问来源国家决定默认模式，不再等到浏览器超时。Cloudflare（含 Tunnel）会为每个请求带上来源国家头 `CF-IPCountry`，服务端读取后：
  - 来源为中国（`CN`）→ 页面直接以**静态模式**渲染，首屏零等待、不发任何第三方请求；
  - 其他国家/地区（含美国）→ 仍是**交互模式**（Google Maps 可拖拽地图）；
  - 取不到国家头（内网直连、未知来源）→ 保守按交互模式，由 0.1.268 的超时守卫兜底。
- **手动选择仍然优先**：用户在工具栏上拨动「静态模式」开关，或 0.1.268 的自动回退，都会把选择写入 `localStorage`；本地选择存在时以本地为准，服务端国家只作为默认值。也就是说美国用户想用静态图、或中国用户临时想试交互地图，都可以自己切。
- 实现：`app.py` 新增 `STATIC_MAP_COUNTRIES = {"CN"}` 与 `request_country_code()`（读 `CF-IPCountry`，缺失返回 `None`）；`service_order_map()` 计算 `preferred_map_mode` 传入模板；模板把 `preferredMapMode` / `mapCountryCode` 放进 `window.serviceOrderMapConfig`，模式判定脚本改为「本地偏好 → 服务端默认值」。
- 常量集中在 `STATIC_MAP_COUNTRIES`，后续要把更多网络受限国家（或其它场景）纳入只需加一个元素。
- 测试：`test_service_order_map_static.py` 新增 5 项（国家头解析、`CN` 直接进静态模式、`US` 保持交互默认、未知国家回落交互、本地偏好覆盖服务端默认值），该文件共 32 项全过。

## [0.1.269] - 2026-09-21

### Changed（侧边栏新增「实用工具」菜单，收纳三个工具入口）
- 原先分散在三处的工具入口合并到一个可折叠的「实用工具」菜单下，主菜单更短、工具更好找：
  - **AI 日报审查**（原「智能助手」下方的顶层链接）
  - **出行工具**（原「智能助手」下方的顶层链接）
  - **数据库工具**（原「系统配置」子菜单内）
- 位置：侧边栏「智能助手」之后、「基础数据」之前；访问其中任一页面时菜单自动展开（高亮/展开判定使用新增的 `utility_endpoints`）。
- 权限配置同步调整：`MENU_PERMISSION_GROUPS` 新增「实用工具」分组并把这三个 key 移入（从「主菜单」「系统配置」移出）。**key 与各角色的默认可见性完全不变**（AI 日报/出行工具 = admin、manager、finance、employee；数据库工具 = 仅 admin），已保存的菜单权限配置无需改动。
- 显示条件不变：AI 日报审查与出行工具仍限内部用户（`is_internal_user()`），数据库工具仍需 `database_console` 查看权限；三项都不可见时整个菜单不渲染，外部管理员/外部员工不会看到空菜单。
- 多语言：`ui-i18n.js` 为「实用工具」「AI 日报审查」「数据库工具」补齐英/荷/德/西四种译文。
- 测试：新增 `test_utility_menu.py` 9 项（分组归属、旧分组已移除、菜单 key 全局唯一、默认角色未变、三个链接都落在「实用工具」块内、系统配置不再含数据库工具、端点集合、四语言译文）。

## [0.1.268] - 2026-09-21

### Fixed（站点地图：交互地图加载不到时自动回退静态模式，不再显示 0 个站点）
- **现象**：在中国网络下打开「站点地图」，顶部计数显示 `0 / 28`、地图一片空白，看起来像"数据没了"。服务端数据其实是完好的（28 个站点坐标齐全、页面 200、静态图接口 200）。
- **根因**：默认「交互模式」在浏览器端加载 `maps.googleapis.com/maps/api/js`（Google Maps JS）。中国网络访问不到该域名，脚本既不成功也不报错（长时间挂起），于是 `initServiceOrderGoogleMap` 回调永远不执行 → 站点标记一个都没画、计数停在 0。此前只能靠手动打开工具栏「静态模式」开关规避，换浏览器或清缓存后会复发。
- **修复**：页面加载器新增三重守卫，任一触发即自动切换为服务端出图的静态模式，并记住偏好（写 `localStorage`）：
  1. 第三方脚本 `error` 事件（网络直接拒绝/解析失败）；
  2. **超时守卫**：10 秒（Leaflet 12 秒）内交互渲染器仍未注册即判定失败——中国网络是挂起而非报错，超时是主要手段；
  3. `window.gm_authFailure` 钩子（Google 地图密钥无效/被域名限制时由 Google 主动调用）。
- 自动回退时：勾选「静态模式」开关、立即渲染静态图，并在状态栏最前面说明原因（如"交互地图脚本未能加载（网络不可达），已自动切换为静态模式"）。
- **兼容性**：脚本正常加载时零变化（渲染器注册后守卫不再触发）；美国用户不受影响。手动关闭「静态模式」开关后若交互地图能成功加载，则会回到交互模式并记住，不会被强制拉回。
- 测试：`test_service_order_map_static.py` 27 项通过（新增 5 项：加载器含 fallback/超时/`gm_authFailure`/写偏好、common.js 注册标记、static.js 展示回退原因、静态分支注释）；生成的页面 JS 经 `node --check` 语法校验通过。

## [0.1.267] - 2026-09-21

### Changed
- 「站点地图」静态模式：
  - **视野固定为美国本土 48 州**（`center=39.8283,-98.5795`、`zoom=4`），不再按筛选出的工单数量自动缩放——筛选条件变化时地图范围保持不变，方便对照。按需求**不拉伸到阿拉斯加/夏威夷**（那样会把本土压得很小）；当前无本土外站点，若将来出现需注意会被裁出画面。
  - **布局改为左右分布**：地图在左、站点列表在右（列表 300px 宽、与地图等高、内部滚动），地图可用高度显著变大（此前上下分布时列表占掉约 180px 且地图被压扁）；窄屏（≤1100px 列表收窄到 240px，≤720px）自动回落为上下排列。
  - 状态栏新增「固定美国本土视图」提示，编号说明改为「图上编号与右侧列表一致」。

### 测试
- `test_service_order_map_static.py` 增至 22 项：`get_map()` 的 center/zoom 转发与默认不带 center/zoom；静态出图端点在不同站点数（1/3/40）下始终使用固定视野参数；模板中地图与列表同处 `.static-map-body` 行容器且顺序为容器→地图→列表；静态渲染脚本含「固定美国本土视图」文案。

## [0.1.266] - 2026-09-21

### Fixed
- 「出行工具」路线地图：**跨长路线（如休斯顿→亚利桑那，1200+ 英里）生成时 Static Maps 报「地图参数无效」**的问题。根因：Google Routes 返回的高精度路线 polyline 编码后可达 ~17k 字符，拼进 Static Maps 的 GET URL 后超过 Google 8192 字符上限，被直接 400 拒绝（实测 `polylineQuality=OVERVIEW` 无效，Google 返回同样长度的 polyline）；短路线不受影响。
  - 修复：新增 `travel_tools/polyline.py`（Google polyline 编解码 + Douglas-Peucker 抽稀）；`FlexStaticMapsService.get_map()` 在 URL 将超限时按 50m→10km 容差逐级抽稀 polyline 后重拼 URL（640x400 证据图分辨率下视觉无差异，起终点锚点不变），仅在极少数仍超限时返回新错误码 `static_maps_url_too_long`（「路线跨度过大，地图无法渲染；请减少停靠点或分段生成」），且在发起 HTTP 请求前拦截。
  - 原误导文案「地图参数无效（地址或路线过旧）」修正为「地图无法渲染该请求（请检查地址写法或稍后重试）」。
  - 生产环境实测：1234.2 英里路线 polyline 17093 字符 → 50m 容差抽稀至 3687 字符（4600 点 → 704 点），最终 URL 5313 字符，Static Maps 返回 HTTP 200 有效 PNG。
- 补提交 v0.1.265 geocoding 报错改进对应的 3 个测试用例（此前仅提交了实现代码）。

### 测试
- 新增 `PolylineCodecTest`（3 项：Google 官方样例编解码逐字节回环、长路线编解码回环、Douglas-Peucker 保端点）与 `FlexStaticMapsUrlBudgetTest`（4 项：长 polyline 自动抽稀至 URL ≤ 8192 且端点保持、短 polyline 原样透传、预算不可满足返回 None、无法满足预算时不发 HTTP 直接报错）+ 静态错误文案映射 1 项；`test_travel_tools.py` 共 39 项全过。

## [0.1.265] - 2026-09-21

### Changed
- 「出行工具」随机找宾馆：终点地址解析失败的报错改为显示**具体原因**（此前一律显示笼统的"终点地址无法解析，请更具体"），便于自助排查：
  - **Google 查无此地址**（ZERO_RESULTS）：提示门牌号可能不存在，建议只写到「路名, 城市, 州」或换相邻门牌号再试；
  - **未配置 Geocoding Key**：明确提示去系统设置配置 `google_geocoding_api_key`；
  - **调用被拒/限流**：透传 Google 原始状态码（如 `REQUEST_DENIED`、`OVER_QUERY_LIMIT`），提示检查密钥是否启用 Geocoding API、配额是否用尽；
  - 网络/响应异常等其余失败各有对应文案。

## [0.1.264] - 2026-09-21

### Added
- 工作日报「编辑」页新增「← 上一条 / 下一条 →」导航（用户需求，与 v0.1.261 查看页同款交互）：**只在同一工单内**切换相邻日报（排序与日报列表默认序一致：实际工作日期降序、同日按创建顺序），已是工单内最新/最早一条时对应按钮置灰（悬停提示「已是同工单最新/最早一条日报」）。导航按钮位于页头操作栏最左，样式复用全局按钮外观。
  - 与查看页导航（跨工单全局范围）刻意区分：编辑页聚焦当前工单，其他工单的日报不会出现在导航里。
- 外部账号边界与编辑页守卫保持一致：外部员工只能在自己创建的日报间导航（不出现点了就 403 的链接，也不泄露他人在同工单创建日报的存在性）；外部经理查看不受影响。

### 测试
- 新增 `test_service_report_edit_navigation.py`（7 项）：三态置灰、链接指向相邻日报编辑页、同日多条按 id 衔接、跨工单日报不出现、外部员工仅限自己日报且直接访问他人日报仍 403、相邻编辑页可达；与查看页导航回归（6 项）同跑 13 项全过，相关日报表单套件（payroll / formal_link / image_preview）22 项回归通过。

## [0.1.263] - 2026-09-21

### Added
- 「站点地图」页改为**双模式**（用户需求：国内员工也需要站点地图，且不能影响美国员工）：
  - **交互模式（默认，零变化）**：美国员工打开页面所见与之前完全一致——配了 Google Maps Browser Key 就继续直连 Google Maps JS（拖拽/缩放/点选弹窗），未配则 Leaflet + OSM 兜底。
  - **静态模式（新增开关）**：工具栏新增「静态模式」开关（偏好存 localStorage，会记住）。开启后页面**不加载任何第三方脚本/样式**（Google JS / Leaflet / OSM / unpkg 全不引入），改为把当前筛选出的站点坐标 POST 到服务端，由服务端用 Google Static Maps（复用 `google_static_maps_api_key`，按已存经纬度画点，无 geocoding 费用）出图并以 base64 回传——中国网络可直接查看。
  - 静态模式能力保留：搜索与全部筛选条件继续生效（前端防抖后自动重新出图）、编号/颜色图例（颜色与交互地图一致：超期红/预警橙/正常绿/无工单灰，紫色=公司总部）、未定位站点面板与「重新定位」、统计计数；≤35 个站点时图中编号 1-9/A-Z 与下方站点列表一一对应（点击直达工单详情），站点较多（36-200）时退化为纯颜色标注并在状态栏说明，超过 200 个自动截断并提示缩小筛选范围。
  - 站点定位（geocode-next 循环）在静态模式下依然运行，定位完成后再出图，避免反复请求。

### Changed
- `travel_tools/static_maps.py` `FlexStaticMapsService.get_map()`：支持无 label 标注（label 缺省时不再输出空 `label:` 参数）与可选 `scale` 参数（站点地图用 640x640@2x），原 travel_tools 调用行为不变。
- 「站点地图」前端拆出共享层 `static/service-order-map-common.js`（页面元素引用、筛选/图例/未定位面板、geocode 循环、统一事件接线、模式开关），原 `service-order-map-google.js`（Google Maps JS）与 `service-order-map.js`（Leaflet）只保留各自地图专属逻辑；新增 `service-order-map-static.js`（静态渲染器：防抖请求、编号列表、错误文案）。渲染器通过 `registerServiceOrderMapRenderer()` 注册，共享控制层统一驱动，三种模式共用同一份筛选逻辑。

### 测试
- 新增 `test_service_order_map_static.py`（18 项，全部 mock 网络）：无 label/scale 的 Static Maps URL 构造；端点登录/权限守卫（未登录 302、外部员工 403、外部经理放行）、参数校验 400、未配 Key 503、成功出图（编号对齐/颜色映射/总部紫标）、>35 退化无编号、>200 截断；模板接线（开关/静态面板/动态加载器，静态模式不出现静态 `<script src>` 的 Google 标签）。
- `test_service_order_map_email.py` 契约断言从两个 provider 文件改为共享层 `service-order-map-common.js`（buyerDetails 随重构迁移）。
- 回归：`test_travel_tools.py`、`test_permission_menu_config.py`、`test_basic_data_permissions.py` 全部通过。

## [0.1.262] - 2026-09-21

### Added
- 新增「出行工具」页面（主菜单，admin/manager/finance/employee，外部账号不可见），复用智能日报的 Google Routes / Static Maps 服务端调用模式，API Key 不出后端、佐证图即用即生成不落盘：
  - **路线地图**：起点 + 终点 + 可选中间停靠点（最多 9 个，按顺序途经）生成路线地图佐证 PNG（A/1-9/B 标注 + 路线折线 + 路段距离/时长信息面板），可下载。
  - **随机找宾馆**：输入终点、起点到终点总距离与大概方位（8 向罗盘或自定义角度），球面几何反推起点位置，Google Places（New）searchNearby 按半径（默认 25 英里）搜索 lodging，随机返回一家并用 Google Routes 验证「宾馆→终点」实际驾车距离/时长（与目标差值）。佐证 PNG 按「起点→终点路线」版式呈现（标题与面板格式与路线地图/里程佐证一致，不出现宾馆检索痕迹，仅展示起点=宾馆、终点、单程距离、时长）；页面上仍显示评分/推算位置/差值等辅助挑选信息；支持「换一家」（排除已展示宾馆）。
- 新增 `travel_tools/` 模块：`geo.py`（球面几何/方位换算）、`routes_service.py`（Routes API 多点路线，TRAFFIC_UNAWARE，重试与错误码沿用 ai_daily_report 约定）、`places_service.py`（Places searchNearby + Geocoding + 随机选取）、`static_maps.py`（任意标注的 Static Maps 客户端）、`evidence.py`（地图+信息面板合成）、`service.py`（流程编排）、`web.py`（路由注册，沿用 profitability 的 register(app, globals()) 模式）。
- 系统设置新增 `google_places_api_key`（Google Places API 密钥）：可在「系统设置」页配置或用环境变量 `GOOGLE_PLACES_API_KEY`；留空时回退使用 Geocoding 密钥（同一 Google 项目）。菜单权限「出行工具」默认内部四角色可见，外部账号默认关闭。

### 测试
- 新增 `test_travel_tools.py`（29 项，全部 mock 网络请求）：球面几何往返、Routes intermediates(via=true) 请求体与分段解析、Static Maps 标注构造、Places 解析/随机排除/地理编码、两条流程编排（成功合成图片、错误映射）、Flask 接线冒烟（菜单种子、未登录 302、外部账号 403、内部账号渲染）。

## [0.1.261] - 2026-09-21

### Added
- 工作日报「查看」页新增「← 上一个 / 下一个 →」导航（用户需求）：按日报列表的默认顺序（实际工作日期降序、同日按创建顺序）在相邻日报之间切换；已是第一条时「上一个」置灰、已是最后一条时「下一个」置灰（带悬停提示）。导航栏与导出/打印同行、左右分栏，打印时自动隐藏。
- 导航范围遵守权限边界：外部账号（external_manager 按客户、external_employee 按 `user_service_orders` 授权工单）只能在有权访问的日报间导航，不会通过「上一个/下一个」看到无权日报的链接或页面。

### 测试
- 新增 `test_service_report_navigation.py`（6 项）：首/中/尾三态按钮启用与置灰、链接指向相邻日报、同日多条日报按 id 衔接、外部账号导航不越权（可见范围外日报不出现链接且按钮置灰）。
- 回归：`test_service_report_external_view.py`、`test_report_query_filters.py`、`test_field_work.py`、`test_image_preview_unified.py` 全部通过（67 passed + 14 subtests）。
- Chrome headless 渲染验证：中间态两按钮均可点、尾态「下一个」置灰（截图核对）。

## [0.1.260] - 2026-09-20

### Fixed
- 修复 AI 日报 Draft 详情页点击照片后弹出的「附件预览」布局错乱的问题（用户报告：标题被挤成竖排、缩放按钮换行堆在中间、照片偏在右侧，且弹窗无法正常关闭）。根因：`static/ai-daily-report-review.js` 的 `openImagePreview` 用 `dialog.style.display = "flex"` 直接显示共享弹窗，没有走 `showModal()`——`<dialog>` 没有 `open` 属性时 `close()` 会抛 `InvalidStateError`，关闭按钮失效；内联 `flex` 还让弹窗头部与图片舞台并排成一行。修复：`openImagePreview` 改走共享的标准开启入口 `window.openAttachmentImagePreview(src, name)`（`showModal()` + 居中模态 + 打开期间锁定页面滚动、关闭后恢复滚动位置），共享弹窗关闭逻辑对遗留内联显示方式容错。
- 补漏（0.1.259 遗漏）：`new_expense`（新建报销保存）与 `edit_expense` 同样对辅助步骤兜底——查重、附件同步失败不再中断保存，提交后通知失败不阻塞，并增加 `except Exception` 路由级兜底（回滚 + 友好提示）。`test_expense_return_admin_and_robust_save.py` 增至 10 项（新建路径：查重崩溃/同步崩溃下仍 302 且数据落库）。

### Changed
- 图片弹出预览全站统一为一套实现（用户要求「与日报中的图片预览统一」）：
  - 共享弹窗（base.html `#imageAttachmentPreviewDialog` + `static/attachment-preview.js/.css`）外观与交互对齐原工单日报照片预览：1180px 居中模态、顶部标题 + 缩放工具栏（缩小 / 放大 / 自适应 / 原图 / 缩放百分比 / 关闭，顺序一致）、下方深色图片舞台；新增缩放百分比标签与「点击遮罩空白处关闭」。
  - 工单日报表单的 NAS 照片预览（`#nasPhotoPreviewDialog` 及其私有缩放逻辑约 90 行）删除，`openNasPhotoPreview` 改为委托共享弹窗；工单日报表单里的日报照片缩略图本就通过 `data-image-preview` 走同一弹窗。AI 日报 Draft 详情、AI 日报审查中心、工单日报、报销等页面的图片预览自此为同一外观、同一交互、同一份代码。

### 测试
- 新增 `test_image_preview_unified.py`（16 项静态契约）：AI 日报 JS 必须经 `openAttachmentImagePreview` 开启预览且不再出现内联 `display` 打开方式；共享弹窗公开入口/容错关闭/缩放标签/遮罩关闭；base.html 工具栏顺序与缩放标签；CSS 统一外观（1180px + 深色舞台）；service-report.js 委托调用且重复的对话框标记、私有缩放函数与 CSS 全部移除、两个调用点保留。
- 相关套件回归：test_review_js_contract、test_field_work_js_contract、test_report_copy、test_service_report_external_view、test_service_report_zero_mileage、test_customer_report_payroll 全部通过（53 passed + 5 subtests）。
- Chrome headless 渲染验证：统一后的弹窗为居中模态，标题/工具栏在顶部、竖版照片在深色舞台内自适应居中。

## [0.1.259] - 2026-09-20

### Fixed
- 修复报销单详情页明细表底部黄色「小计 / 合计」行持续闪烁的问题（用户报告，EX2609032）。`static/system-grid.js` 存在两类可自我维持的重渲染循环：① `ResizeObserver` 观察网格容器，任何高度抖动（如合计行重建）都会再次触发 `redraw()`，`redraw()` 又改变高度，形成反馈循环；② 源表格被外部（如浏览器翻译插件、脚本）以每秒数次频率改写时，`sync()` 会跟着无限 `replaceData`，每次重建都会让合计行肉眼可见地闪一下。修复：ResizeObserver 只在宽度真正变化时才 `redraw`（列布局只取决于宽度）；4 秒内 redraw 超过 8 次、replaceData 超过 12 次进入熔断窗口期，直接跳过直至振荡源停止；`renderComplete` 中的分组小计重算合并到下一帧只跑一次。`performance.now()` 熔断窗口滚动恢复，正常缩放窗口、真实数据变化不受影响。
- 修复管理员在报销详情页点「退回」/「审核通过」提示「没有访问权限」（403）的问题（用户报告）。根因：集中路由闸门把 `return_expense`/`approve_expense` 映射到 `expenses.approve` 权限，而默认角色组 `{manager, finance}` 漏了 admin；详情页模板却按 `normalized_role() in ["admin","manager"]` 硬编码显示按钮，管理员"看得到点不动"。修复：默认组补入 admin；`seed_role_permissions` 用 `insert or ignore` 只能补缺失行、不会刷新老库中已固化的 `is_enabled=0` 行，故新增一次性数据迁移（settings 哨兵 `expense_approve_admin_v1`，管理员事后手动关闭不会被重启覆盖）；模板按钮改用 `has_action_permission("expenses", "approve")` 门控，与权限体系约定一致。

### Changed
- 报销编辑/提交保存链路对环境性异常兜底，不再因辅助步骤崩溃返回 500（用户报告"经理修改报销单点提交""提交人编辑保存"均出现 Internal Server Error）：
  - `run_expense_duplicate_checks`（查重）与 `sync_expense_attachments_to_settlement`（报销附件同步到结算）改为尽力而为：失败记录 `app.logger.exception` 后继续保存主流程；`edit_expense` 增加 `except Exception` 兜底（回滚 + 友好提示），提交后的站内通知失败也不再阻塞（报销数据已保存）。
  - `copy_file_to_customer_reimbursement_attachment`：`shutil.copyfile` 遇文件占用/磁盘错误（OSError）返回跳过而非抛错；命中唯一索引 `idx_customer_reimbursement_attachment_expense_source` 的并发竞争（两个请求同时同步同一附件）按"已处理"处理而非 IntegrityError 500。
  - `expense_attachment_fingerprints` 的图片指纹部分捕获所有异常（Pillow 对超大图抛的 `DecompressionBombError` 不是 OSError/ValueError 子类，此前会逃逸成 500）；`save_expense_attachment` 的 `uploaded.save` 失败转为 ValueError 提示而非 500。

### 测试
- 新增 `test_expense_return_admin_and_robust_save.py`（8 项）：init 后 admin 的 expenses.approve 为启用、管理员 POST 退回报销返回 302 且状态变为 returned、迁移哨兵只跑一次且尊重管理员事后手动关闭、附件拷贝吞掉 PermissionError 与唯一索引并发冲突、指纹函数在 Pillow DecompressionBomb 风格异常下仍返回 SHA、编辑保存分别在查重崩溃与同步崩溃下仍返回 302 且数据落库。
- 相关套件回归：test_reimbursement_expense_display（9）、test_customer_reimbursement_mro、test_expense_attachment_transfer、test_expense_on_behalf、test_grid_grouping_labels、test_basic_data_permissions 全部通过。
- 闪烁熔断另以 Chrome headless + jsdom 探针验证：仅高度振荡 20 次触发 0 次 redraw；宽度振荡风暴被截断在 8 次；每 100ms 一次的持续源表格改写（30 次）仅产生 12 次 replaceData（窗口上限），合计行 DOM 数保持 1。

## [0.1.258] - 2026-09-19

### Fixed
- 修复工单结算单「从报销传递过来的数字在单元格里全部显示 0、后面还挂着『已调整』」的问题（用户报告）。根因是同一个金额存了两列却三处口径不一致：明细行的 `lodging/fuel/...` 是**人工调整值**（未调整时为 0），`auto_lodging/auto_fuel/...` 才是**从员工报销自动转入的来源合计**。而页面单元格 `input` 的 value 直接取人工列（必然为 0），徽章判定又是 `auto > 0 and 人工 != auto`（0 ≠ 来源金额，必然判定为「已调整」），于是未调整过的行也会显示 0 + 已调整。
- 修复已调整过的金额在点击保存后被刷掉的问题。`merge_approved_expenses_into_customer_reimbursement` 每次保存都会把 `auto_*` 清零并按报销来源重新累加，但表单不回传 `auto_*`、也不保留人工列语义，用户手改的数字在重算后无处落地即被覆盖。

### Changed
- 金额口径统一为**「手改优先」**：单元格展示与合计取值均为「有人工调整值则用人工值，否则用报销来源金额」。
  - `customer_reimbursement_item_expense_amount()` 由 `实际 + auto` 改为手改优先，避免来源金额在合计/Excel/PDF 中被重复计入。
  - `merge_approved_expenses_into_customer_reimbursement()` 在重建 `auto_*` 前先捕获人工调整值，重算后回填，保存不再冲掉手改数字；单元格值等于来源合计时视为「未曾调整」，继续保持跟随来源。
  - `customer_reimbursement_items_from_form()` 增读并回传 `auto_*`；模板补隐藏域。
  - 模板 `is_adjusted` 判定改为 `人工值非 0 且不等于来源金额`，另有来源的行不再误显示「已调整」。
  - `static/system-grid.js` 去掉 `input.value + data-auto-amount` 的重复累加（value 已是生效金额）。
  - 新增 `_row_field()` 兼容 `sqlite3.Row`（无 `.get`）。
- 不改动任何里程、工时、随行计费逻辑。

### 测试
- 新增 `test_reimbursement_expense_display.py`（9 项）：未调整时生效金额等于来源金额而非 0、未调整不渲染「已调整」徽章、手改后徽章出现、手改值经保存与 totals 重算后保持不变、表单回传 `auto_*` 后 round-trip 仍保留手改值、合计不重复累加来源、页面渲染的 input 值与隐藏域断言、manual_review 选择来源路径同样保留手改值。已用变异测试确认用例能捕获旧缺陷。
- 更新 `test_customer_reimbursement_mro.py` 的 Excel 导出断言以匹配「手改优先」口径（住宿 10 与来源 20 不再相加）。
- 结算/报销相关套件（mro、settlement_excel、profitability_status、expense_attachment_transfer、expense_on_behalf、customer_report_payroll、grid_grouping_labels）共 69 项通过。

## [0.1.257] - 2026-09-19

### Fixed
- 修复 AI 智能日报传递到工单日报后，「现场到达时间照片」和「离开现场时间照片」两张照片没有同步过去的问题（用户报告）。根因是字段级不一致：正式保存只把到达/离开照片写进了工单日报的取证列（`service_reports.arrival_photo_relative_path` 等），而工单日报页面（编辑表单与只读查看页）都是从附件表 `service_report_attachments` 按 `category IN ('arrival','departure')` 读图的——写的地方和读的地方不是同一个，所以那两个照片区必然为空。该行为源自早期「到达/离开仅作取证、不生成正式附件」的设计边界，但该边界从未与页面实际渲染方式对齐。

### Changed
- 到达/离开照片改为**双重写入**：既保留取证列（仍指向 AI 日报原始照片，供审计），又真正物化为工单日报附件（分类 `arrival` / `departure`），与手动填写的工单日报完全一致。附件清单（manifest）中这两个角色由 `materialization_required=0` 改为 `1`，`FORMAL_CATEGORIES` 与 `ROLE_TO_CATEGORY` 补入 arrival/departure。存储路径沿用 `工单号/日期/现场到达时间照片`、`离开现场时间照片` 目录命名，与手动填写的日报一致；工单附件打包下载（attachments.zip）自动一并包含这两类照片。

### 测试
- 新增 `test_arrival_departure_sync.py`（8 项）：从工单日报页面自身的读取函数 `get_report_attachments` 验证 arrival/departure 非空、文件可通过 `report_attachment_path` 解析且哈希命中 prepared asset、目录命名与手动日报一致、取证列仍写入、无 ref 时不虚构附件行、仅 departure 不产生 arrival、无里程佐证草稿仍同步两张照片、编辑页与只读查看页均渲染出照片 `<img>` 且图片端点可访问。
- 更新旧契约用例：`test_ai_daily_report_phase9.py` 的 test_10/21/22/63/64/65 与 `test_ai_daily_report_phase8.py` 的 test_52 由「到达/离开不产生附件」改为「物化为真实附件且保留取证」，并按新分类数调整断言。phase9（51）/phase8（59）/phase7（110）/phase6（44）/incomplete_pass（11）回归通过。

## [0.1.256] - 2026-09-19

### Fixed
- 修复 AI 一句话解析找不到人员的问题（用户报告："我和高阳和antonio自驾从家出发……还是没有找到Antonio"，高阳能解析、Antonio 不能）。根因是解析服务的精确匹配用 SQLite `name = ?`，而 `=` **区分大小写**——输入小写 "antonio" 无法命中库中存储为 "Antonio" 的账号；SQLite 的 `=` 和 `LIKE` 也都不做 Unicode 变音折叠，"António" 同样漏配；此外角色资格判断用的是数据库原始存储值，早期录入的旧角色名（`user`、`external`）一律被拒。`resolve()` 重写为一次全表加载 + Python 侧折叠匹配（NFKD 去变音 + casefold），大小写、变音差异均容错，旧角色名经别名映射（`user→employee`、`external→external_manager`）后参与资格判断；精确折叠命中直接自动解析，多个折叠同名才转人工确认候选。

### 测试
- 新增 `test_ai_daily_report_employee_resolution.py`（10 项）：小写命中大写存储名（用户原始场景）、大小写折叠、双向变音折叠、partial 候选（停用排除）、旧角色名 user/external 可解析、停用不可解析、批量 resolve_workers、"我"自引用对旧角色名用户生效。phase2（44）/home_origin（7）/workers（17）/staff_search（11）/employee_resolution（10）共 89 项回归通过。

## [0.1.255] - 2026-09-19

### Fixed
- 修复智能日报详情页「添加工作人员」搜索不到外部员工的问题（用户报告：搜"Antonio"提示未找到）。搜索接口与手动添加接口此前把角色硬编码为内部三类（管理员/经理/员工），而 AI 一句话解析自 2026-09-16 起已允许外部人员作为日报工作人员，两条入口不一致。现两处白名单统一对齐 `WORKER_ELIGIBLE_ROLES`（含财务、外部经理、外部员工），对话框说明文案同步去掉"内部"限定。

### Changed
- 员工搜索匹配增强：匹配时忽略大小写与变音符号差异（如"Antonio"可搜到录入为"António"的人员，此前 SQLite LIKE 对非 ASCII 字符不做折叠导致漏配）。搜索无结果时给出区分性提示：存在相近但已停用/角色不符的账号时提示账号不可用，否则提示先到用户管理创建账号。前端角色标签补充「财务」「外部经理」「外部员工」。

### 测试
- 新增 `test_ai_daily_report_staff_search.py`（11 项）：外部经理/外部员工/财务可被搜到、变音与大小写折叠、邮箱匹配、停用账号排除并提示、无账号提示、空关键词、外部员工可经 add-worker 加入草稿、停用用户被拒、20 条上限、外部用户调用搜索仍 403。`test_ai_daily_report_workers.py` 两个旧契约用例按新决策更新（原断言财务/外部被排除，现断言可搜索可加入）。workers/home_origin/phase2/staff_search 共 79 项、auto_prepare 5 项、auto_prepare_preview 4 项回归通过。

## [0.1.254] - 2026-09-19

### Fixed
- 修复智能日报详情页三个操作按钮（生成日报/取消/删除）「一闪而过」的问题：`POST /draft/<id>/auto-prepare` 此前返回的是聊天流程用的扁平 preview（不含 `status` 与 `draft_version`，字段结构也与详情页期望完全不同）。详情页在自动准备完成后用该响应整体替换页面数据并二次渲染，`status` 变为 undefined 导致按钮区被清空，同时 `draft_version` 丢失会影响后续确认/取消等操作的版本校验。该端点现改用与 `GET /draft/<id>/preview` 相同的聚合版 preview（新增共享构造函数 `_build_review_center_preview`），并顺带清理了 preview 端点中的重复构造块。凡 0 照片或时间线待确认的草稿打开详情页即触发自动准备，因此必现。

### 测试
- 新增 `test_ai_daily_report_auto_prepare_preview.py`（4 项）：auto-prepare 响应携带 status/draft_version 与全部聚合字段、顶层 draft_version 与 preview 一致、与 GET preview 形状完全一致、draft/confirmed/saved 三种状态各自正确保留（saved 时按钮为空属预期）。另以 HTTP 级 E2E（`verify_flicker_fix.py`，15 项断言）模拟详情页两次渲染输入，确认两次均为 `status='draft'`、按钮均渲染。`test_ai_daily_report_auto_prepare.py` 5 项、`test_ai_daily_report_phase6.py` 44 项、`test_ai_daily_report_phase9.py` 51 项、`test_ai_daily_report_incomplete_pass.py` 11 项回归通过。

## [0.1.253] - 2026-09-18

### Added
- 智能日报允许不完整传递到工单（产品决策 2026-09-18）：此前新建智能日报若没有可解析内容（如仅选了工单和日期），确认后自动生成被 Phase 7 校验 ERROR 挡住，正式保存又因没有附件清单而不可用，用户既无法传递到工单也无从编辑。现在存在未解决的 ERROR 时，界面提供「不完整也传到工单」按钮（确认被挡后的提示弹窗、操作区与正式保存区各一个入口），把不完整的日报直接生成工单日报（不生成附件），用户随后在工单日报页面继续编辑完善。

### Changed
- `FormalSaveService` 新增 `run_incomplete` 旁路：保留 exactly-once、状态闸门与版本锁，跳过 Phase 7 校验闸门与附件清单全链路；commit 记录使用哨兵 `manifest_id='force_incomplete'`（快照 `{"incomplete": true}`）保证可审计；`_build_formal_mapping` 新增 `allow_empty_workers` 参数，允许 0 名服务人员。confirm 与 formal-save 端点均支持请求体 `force_incomplete: true`（仅在校验未通过时生效，校验通过仍走完整附件链路）。

### 测试
- 新增 `test_ai_daily_report_incomplete_pass.py`（11 项）：空草稿校验阻断（DRFT-004/WRKR-001）、run_incomplete 生成工单日报（0 工人 0 附件、草稿转 saved、commit 哨兵值、审计含「不完整」）、exactly-once 幂等、状态/版本闸门拒绝、映射层空工人开关、端点级 422 不变性与 force 201、confirm 一键强制传递、被挡后重试路径。`test_ai_daily_report_phase9.py` 51 项、`test_ai_daily_report_phase7.py` 110 项回归通过。

> 注：本版本功能代码实际随 v0.1.252 提交（859145f）进入仓库；因与同日另一条 v0.1.252（数据库修复）版本号撞车，此修正提交恢复该条目并为本功能分配独立版本号 0.1.253。

## [0.1.252] - 2026-09-19

### Fixed
- 历史报销关联迁移明确写入 UTC 时区，避免产生无时区的结算选择时间；正常付款日期保持日期语义。

### Maintenance
- 增加一次性 SQLite 数据修复工具，默认仅在内存副本演练；显式执行前备份，归档原记录，按已核查的外键级联/置空规则清理历史孤立关系，补齐有来源证据的 UTC 和旧照片水印时间。
- 修复前后逐行核对，未知异常自动中止；不改金额、照片归档日期或附件文件。新增 6 项测试覆盖外键约束、审计保留、金额/日期不变、幂等性和未知来源拒绝处理。

## [0.1.251] - 2026-09-19

### Changed
- 语言选择改版：注册页去掉「首选交流语言」下拉，改为直接勾选交流语言（勾选的第一项自动作为首选）；勾选框与文字并排、横向排列，修复此前勾选框错位。用户管理的「新增用户」「编辑用户」弹窗同步提供「交流语言」勾选（编辑时按用户主数据预勾选）。勾选结果统一保存进用户主数据 `users.preferred_communication_language` / `users.communication_languages`，新增/编辑/注册三条链路均落库。

### Added
- 概览页新增「待审核报销」指标卡片（已提交待审核 + 被退回的报销合计）。原「待报销金额」仅统计已审核通过且未发放的报销，两者不重复计算。指标行由 7 个增至 8 个：概览页改用紧凑样式（缩小字号与间距），常规电脑端一行显示全部指标，发票数量保留。

### 测试
- 新增 `test_language_selection_dashboard.py`（10 项）：AST 提取 `communication_languages_from_form` 真函数驱动 4 种表单场景（首个勾选为首选、全不勾回退、非法值过滤、旧首选字段兼容）+ 注册页/用户弹窗/概览页/样式/i18n 契约。`test_registration_address.py` 旧表单兼容 4 项 + 8 子测试通过。

## [0.1.250] - 2026-09-19

### Added
- 项目利润页筛选条件新增「人员」下拉（全部人员/各员工），可单独使用或与工单筛选组合。工时行按日报参与人过滤，费用行按报销受益人过滤；选中员工的工时分摊仍按日报全组人数计算，与不筛选时完全一致。筛选对汇总、利润明细、状态列同步生效。

### 测试
- `test_profitability_status.py` 新增员工筛选 2 项（分摊不变性、费用受益人过滤、与工单组合）+ 模板契约 1 项。

## [0.1.249] - 2026-09-19

### Changed
- 项目利润页「客户已确认 / 结算中 / 预计」三状态判定规则重做（业务规则确认：甲方确认金额后我们才把工单改为已完成）：
  - **客户已确认** = 工单状态为「已完成」（closed）；
  - **结算中** = 工单进行中，但已生成工单结算单或发票（作废发票不算）；
  - **预计** = 工单未生成结算单/发票。
- 旧逻辑按结算单审核状态判定（approved → 客户已确认），与实际业务不符：结算单审批通过不代表甲方确认金额，甲方可能要求删除结算单重新生成。工时行与费用行的判定来源统一改为「工单状态 + 是否已生成结算/发票」。

### 测试
- 新增 `test_profitability_status.py`（5 项）：纯函数判定、六工单场景（含 approved 结算但工单未完成必须为「结算中」、void 发票不算、closed 无结算也是「客户已确认」）、结算单删除后回退「预计」、费用行同规则、结算链接收入快照不受影响。

## [0.1.248] - 2026-09-19

### Fixed
- 项目利润页筛选区布局松散：表单使用了不存在的 `filter-form filter-grid` 类（无任何 CSS 规则），字段全部竖向堆叠。改用全站统一的 `filter-bar service-filter`——桌面端筛选条件横向单行排列（开始日期/结束日期/工单/汇总方式/查询），窄屏自动换行。

## [0.1.247] - 2026-09-19

### Fixed
- 弹窗误关闭：新增/编辑用户等所有 `.modal-dialog` 弹窗，点击内部留白/间隙会被误判为「点击遮罩」而自动关闭。现在只有真正点到弹窗内容框之外的遮罩层才关闭（按坐标与内容矩形比较）。
- 项目利润页「展开利润明细」表格没有分类汇总：`system-grid.js` 的货币列正则不含 收入/成本/利润/客户单价/员工成本单价，维度正则不含「类别」，导致该表既无分组工具也无小计。已补充正则——现在可按 工单/员工/类别/项目（一级+二级）分组并显示小计与合计。
- 附带：将 8 个基础数据页面里硬编码的 `modal-forms.js` 缓存版本号统一改为随发布的 `app_version`，避免 JS 更新后浏览器仍用旧缓存。

### 测试
- 新增 `test_grid_grouping_labels.py`（货币/维度正则契约 + 弹窗修复契约）；上游合入的 `test_ai_daily_report_delete_sync.py`、`test_customer_reimbursement_mro.py`、权限菜单契约等 23 项通过。

## [0.1.246] - 2026-09-18

### Fixed
- AI 日报草稿详情页「查看正式日报」按钮 404：链接硬编码为不存在的 `/edit_service_report/<id>`，改为真实路由 `/service-reports/<id>/edit`；同时去掉 `target="_blank"`，在工作区内以标签页打开而非新开浏览器窗口。

## [0.1.245] - 2026-09-18

### Fixed
- PWA 独立模式下页面顶栏被手机状态栏（时间/电量/刘海）遮挡：移动端侧边栏顶栏新增 `env(safe-area-inset-top)` 安全区内边距，深色背景自然延伸到状态栏后方；普通浏览器模式下该值为 0，外观不变。

## [0.1.244] - 2026-09-18

### 变更（主站 PWA：手机图标以独立 App 打开，不再堆积浏览器标签页）
- **问题**：手机主屏图标（添加到主屏幕的网页快捷方式）每次点开都是普通网页，越积越多；多个页面里找不到未完成的工作。
- **修复**：新增主站 PWA 支持——
  - `GET /manifest.webmanifest`：`display=standalone`、`start_url=/`、使用现有品牌图标（192/512）。
  - `GET /sw.js`（`Service-Worker-Allowed: /`）：极简透传 Service Worker，**不做任何缓存**（工具是动态页面，保持与普通网页一致的真实时性），仅用于满足可安装性。
  - `base.html`：manifest 链接、`apple-mobile-web-app-capable` / `mobile-web-app-capable`、状态栏样式、App 标题、apple-touch-icon、SW 注册。
- **效果**：用户重新"添加到主屏幕/安装应用"后，图标以独立 App 窗口打开（无浏览器地址栏），再次点击恢复上次浏览位置（配合既有的 workspace 重定向逻辑），不再产生新标签页。旧的网页快捷方式请删除后重新添加。
- 测试：新增 `test_pwa_manifest.py`（manifest 字段、SW 头、模板接线、无缓存断言）；phase6/phase9/auto_confirm 回归 99 项通过。

## [0.1.243] - 2026-09-17

### 修复（AI 日报传递到工单日报的时间/交通时长未自动填充）
- **现场到达/离开时间显示为空**：AI 日报照片时间线写入的是 ISO 时间戳（如 `2026-09-17T08:50:00`），而工单日报表单按 `HH:MM` 拆分小时/分钟下拉框，导致小时选不上（只显示了分钟）。正式保存（`formal_save`）现在统一规范化为 `HH:MM` 再保存与计算；启动时对存量 AI 生成日报做一次性数据回填，编辑页渲染也做了兼容。
- **交通时长未自动计算**：Google 路线计算拿到了单程时长（`route_duration_seconds`）但从未换算成人员交通时长。现在路线计算成功后自动为每位自驾人员填充 `travel_hours`：单程时长 × 1.15 上浮（Google 估算通常偏少，用户决策）后向上取整到 15 分钟，当日往返（不住宿）×2，与里程的往返口径一致；手动填写过交通时长的人员永不覆盖，路线重算时自动值同步更新。
- **总计服务工时**：（离开-到达）× 人员数 的既有逻辑此前因时间解析失败而得 0，时间规范化后自动生效。
- **交通时长/合计用时**：工单日报的 交通时长 = 每位人员交通时长之和、合计用时 = 交通时长 + 公共交通时长，人员 `travel_hours` 填充后自动生效。
- 测试：新增 `test_ai_daily_report_v01243.py`（15 项：时间规范化、时长换算、手动值保护、确认链路端到端、存量表单渲染兼容）；相关回归 106 项全部通过。

## [0.1.242] - 2026-09-17

### 变更（照片台账筛选条件紧凑化 + 新增筛选字段）
- **筛选字段调整为 9 项**：工单、**站点（新增）**、铭牌号、位置号、集装箱号、**照片类型（新增）**、**施工员（新增）**、开始日期、结束日期。
  - 后端 `field_work.py photo_rows()` 新增 `site`（匹配工单客户名/站点地址，模糊）与 `photo_type`（精确：设备/进场/离场/自检/非设备/历史）筛选；施工员筛选后端原本已支持（`technician` 模糊匹配实际拍摄/技术员名）。
  - 站点/照片类型筛选同时作用于台账页面、JSON 接口、Excel 导出与设备维修清单（共用 `photo_rows()`），导出链接自动携带当前筛选参数。
- **布局紧凑化**：`.photo-filter-bar` 改为每行 5 列的两行网格（9 个字段 + 按钮组），间距 8px→6px、下边距 12px→8px，长字段不再被挤到单独一行。
- 测试：`test_field_work.py` 新增 `test_ledger_filters_by_site_and_photo_type`；台账相关回归通过（`test_field_work` 中 4 个失败为存量问题，已在未改动的 HEAD 上复现确认与本改动无关）。

## [0.1.241] - 2026-09-17

### 修复（AI 日报 · 「以下字段仍需要确认」误报） + 变更（Draft 取消/删除分离）
- **误报修复**：确认 Draft 时反复弹出「以下字段仍需要确认：Antonio.origin / worker_4_origin_unconfirmed / …」，即使出发地早已通过界面修改确认（来源 user_input ✓）。
  - **原因**：`verification_fields` 在草稿生命周期内只增不减（LLM 的 `名字.origin` 与 TravelService 的 `worker_N_origin_unconfirmed` 两种拼法并存），人员出发地/住宿后续被确认后，存量标记不被清理，导致每次确认都要「确认并覆盖」。
  - **修复**：新增 `reconcile_travel_verification_fields()`（`ai_daily_report/travel_service.py`），按当前工作人员状态同步：出发地已确认 → 删除其 origin 类标记；仍缺/未确认/住宿未定 → 保留对应标记；非自驾人员与已移除人员的标记删除；时间线等其他字段原样保留。接入三处：确认端点（确认前同步并持久化，不再弹覆盖框）、预览聚合（弹窗/徽标显示）、草稿列表接口（「需确认」徽标）。
- **Draft 取消/删除分离**（用户 2026-09-17 决策）：原「删除 Draft」按钮实际是取消语义，且 `/cancel` 端点曾被改成物理删除。现在：
  - **取消**（`POST /draft/<id>/cancel`）：软取消，状态 → `cancelled`，记录 `cancelled_by`/`cancelled_at`，草稿保留可在列表筛选「已取消」查看（恢复 Phase 1 原始契约）。
  - **删除**（`POST /draft/<id>/delete`，新增）：真删除——草稿行及其关联 actions / manifests（含 sources/assets/roles）/ formal commits 物理删除，先清理 manifest 暂存目录；已正式保存（saved）的日报不可删除。
  - 详情页：`draft`/`confirmed` 状态显示「取消」+「删除」；`cancelled` 状态保留「删除」。列表页同样「取消」「删除」双按钮，cancelled 草稿保留删除入口，均有确认提示。
- 测试：新增 `test_ai_daily_report_v01241.py` 13 项（reconcile 单元 6 + 确认不再误弹覆盖 2 + 取消/删除行为 5）全部通过；auto_confirm/auto_prepare/phase9/phase6 回归 104 项通过（phase1 的 `test_cancel_draft` 为已知存量 CSRF 环境失败，403 与本改动无关）。

## [0.1.240] - 2026-09-17

### 修复（发票详情页打印/预览布局不紧凑）
- **问题**：点「发票预览」（打印视图）时，Notes/Terms/Payment Instructions 与 Subtotal/Amount Due 整块被挤到第二页，第一页表格下方留下大片空白。
- **原因**：打印 CSS 中 `.invoice-paper` 强制 `min-height: 297mm`（恰好等于整页高度，易溢出且强制撑满第一页），且各区块间距 12mm、内边距过大，底部区块总高度略超剩余空间。
- **修复**（`static/styles.css` @media print）：去掉 `min-height: 297mm`；纸内边距 12mm→10mm、区块间距 12mm→7mm、左右栏 gap 16mm→12mm；表格单元格、标题、段落间距同步收紧。典型发票（几张行项目）现在可完整排入单页 A4。
- 仅影响打印/导出预览样式，屏幕上的详情页外观不变。

## [0.1.239] - 2026-09-17

### 变更（权限改为菜单配置驱动：发票删除 · 工单结算修改状态/删除）
- **按用户要求：这些权限不再在代码里按角色写死，全部由「权限管理」页面的菜单权限配置决定，默认财务/经理/管理员都有权限。**
- **发票删除**：
  - 详情页删除按钮改为 `has_action_permission("invoices", "delete")`（此前写死 `normalized_role() == "admin"`，导致财务即使有权限也看不到按钮）。
  - 后端 `delete_invoice` 去掉「按状态/发起人绕过」的硬编码分支，改为纯权限判断——此前该兜底实际被集中式路由闸门拦死（按钮可见但请求 403），现在口径一致。
- **工单结算**：
  - 新增专用动作 **「修改状态」(action key `reset`)**，权限目录默认 `{admin, manager, finance}`；路由映射 `reset_customer_reimbursement` 从 `edit` 改为 `reset`。
  - `approve_customer_reimbursement` / `return_customer_reimbursement` 补上服务端 `approve` 权限校验（此前端点无校验，仅靠界面隐藏），模板中的「审核通过/退回」按钮由写死的 `normalized_role() in ["admin","manager"]` 改为 `has_action_permission("customer_reimbursements","approve")`。
  - 「重置为可编辑」（修改状态）按钮改用 `reset` 权限，「删除工单结算」按钮改用 `delete` 权限（不再挂在 `can_manage_customer_reimbursement()` 大杂烩上）。
- **AI 智能日报确认后进入工单日报**：确认成功后若已自动生成工单日报，前端直接跳转到该工单日报页面（此前只提示并停留在草稿页）；被关卡挡住时仍保持在草稿页并提示原因。
- 存量数据库无需迁移：启动时 `insert or ignore` 自动为新动作 `reset` 写入默认授权行。
- 测试：新增 `test_permission_menu_config.py` 9 个用例（默认权限、发票删除随菜单开关、工单结算审核/修改状态/删除随菜单开关、模板不再按角色写死）；权限/结算/发票相关 6 个套件回归 68 项通过（`test_payment_terms` 2 个失败为存量问题，已在未改动的 HEAD 沙盒中复现确认与本改动无关）。

## [0.1.238] - 2026-09-17

### 新增（AI 智能日报 · 进场照片 / 离场照片 独立展示模块）
- **草稿详情页新增「进场照片」「离场照片」两个模块**（`templates/ai_daily_report_draft_detail.html` + `static/ai-daily-report-review.js`），与安全自检照片、施工照片并列展示。
- **自动添加**：自动链路（v0.1.234 的 `_auto_prepare_draft_media` → 时间线自动确认）本来就会把时间线认定的到达/离场候选照片自动写入 `arrival_photo_ref` / `departure_photo_ref` 并应用到到达/离场时间，此前只是没有独立模块展示。现在详情页直接显示：照片缩略图（点击放大）、应用的到达/离场时间、来源（自动（时间线确认）/ 手动标记 / 手动）。
- **手动操作保留**：可编辑状态下提供「手动选择/更换」按钮（复用照片管理对话框的进场/离场标记）；手动标记的照片（`photo_marked` 来源）不会被自动流程覆盖，与既有保护规则一致。
- **下游不受影响**：进场/离场佐证照片本来就会进入附件清单（attachment_manifest `arrival_reference` / `departure_reference` 角色）并随正式保存归档；校验规则（时间来源为照片类但缺对应照片时的 WARNING）保持不变。
- 验证：`node --check` JS 语法通过；Phase 4/8/9 + auto_prepare + auto_confirm 回归 159 项全部通过。

## [0.1.237] - 2026-09-17

### 修复（AI 智能日报 · 里程佐证路径导致自动生成工单日报被挡）
- **现象**：确认 Draft 后提示「已确认，但自动生成工单日报未完成：佐证路径不在 Draft 佐证目录中。」
- **根因**：`MileageEvidenceService._save_evidence_image` 实际把**纯文件名**（如 `ev_xxx.png`）存进 `evidence_records[].file_relative_path`，而附件清单校验 `attachment_manifest._resolve_evidence_path` 死板要求完整前缀 `ai-daily-report-drafts/{draft_id}/mileage/`，真实记录 100% 被拒。佐证图片下载路由（app.py）早已兼容两种格式并归一化，但清单校验漏掉了同样处理；Phase 9 测试夹具自己用完整路径拼佐证文件，掩盖了该 bug。
- **修复**：`_resolve_evidence_path` 与下载路由对齐——完整路径需匹配正确前缀；纯文件名（不含 `/`、`\`、`..`）自动补前缀后解析，路径围栏与哈希校验不变。
- **测试**：新增回归用例 `test_confirm_with_bare_filename_evidence_path`（把夹具佐证路径改写为真实生成器的纯文件名形式，确认后必须成功生成工单日报）；auto_confirm 4/4，Phase 6/7/9 + auto_prepare + auto_confirm 回归 214 项全部通过。

## [0.1.236] - 2026-09-17

### 修正（AI 智能日报 · 施工内容按纯文本处理）
- **背景**：用户明确施工内容就是一段描述文字，不是表格化字段。此前 AI（DeepSeek）会按 prompt 示例把施工内容拆出 `work_items.equipment`（设备编号）等结构化字段，并将其标入待确认清单，导致确认日报时弹出「work_items.equipment 仍需要确认」的人工确认对话框，不符合业务实际。
- **改动**：
  - `ai_daily_report/prompts.py`：新增规则 10——施工内容完整放入 `work_items[].description`（保留原文）；equipment/action/fuse_number 仅在用户明确提及时填写，不拆分/推断/编造设备号；**永远不要**把 `work_items.equipment` 加入 missing_fields。
  - `app.py` chat 端点：合并 missing_fields 时防御性过滤所有 `work_items.*` 标记（防止模型仍输出）。
  - `ai_daily_report/validation_engine.py`：`_check_ai_verification` 跳过 `work_items.*` 字段，存量草稿已有的标记也不再产生 AIVR-003 WARNING。
  - `ai_daily_report/preview_aggregation.py` 与草稿列表端点：展示口径同步过滤，确认弹窗不再出现 `work_items.equipment`。
- **不受影响**：`work_items` 数据结构保留（正式保存 `format_work_items` 仍兼容 equipment 为空时仅渲染 description）；`workers.transportation` 的待确认逻辑保持不变（出行方式影响里程计算，仍需人工把关）。
- 验证：AI 日报相关 7 个套件回归 303 项通过（`test_cancel_draft` 为已知存量 CSRF 环境问题）。

## [0.1.235] - 2026-09-17

### 改进（里程佐证 · 精简图面信息）
- **里程佐证图去掉「Report Date」与「Route Calculated」两行**（按 2026-09-17 产品决策）：图面保留 Employee / Origin / Destination / One-way Distance / Mileage Rule / Reported Mileage / Overnight Stay / Route Provider。此前 UTC 存储的 `Route Calculated: 2026-09-18T01:08:53Z` 显示在 09-17 的单据上看起来像"未来日期"，造成困惑，直接移除显示。
- 报表日期与路线计算时间**仍完整保留在证据记录（MileageEvidenceRecord）中**，参与指纹与审计，只是不再画在图上；面板高度同步收紧（280→244）。已生成的历史佐证图不回溯重绘，新生成的使用新版式（`ai_daily_report/mileage_evidence.py`）。
- 验证：离线渲染探针确认新版式正确；Phase 3A/3B/9 与 auto_confirm/auto_prepare 回归 128 项通过（2 个 phase3b 失败为已知 Windows 存量环境问题，与本改动无关）。

## [0.1.234] - 2026-09-17

### 改进（AI 智能日报 · 发现照片/时间线/施工照片/里程 全自动）
- **四个手动步骤自动化，无需人工干预**：此前生成日报草稿后，用户必须手动依次点击「发现照片」「确认时间线」「分类/筛选施工照片」「重新计算里程」。现在这些步骤全自动执行（`app.py` 新增 `_auto_prepare_draft_media` 自动链路），人工仍可随时修改，修改结果永远不会被自动流程覆盖。
- **两处触发点**：
  - 一句话生成/续改日报（`/api/ai/daily-report/chat`）：动作应用保存后自动执行整个链路，返回的预览即包含照片、时间线与里程。
  - 草稿详情页（`static/ai-daily-report-review.js`）：页面加载时若发现照片未扫描或时间线未确认，自动调用新端点 `POST /api/ai/daily-report/draft/<id>/auto-prepare` 一次，覆盖存量草稿；原有手动按钮全部保留。
- **幂等与保护规则**（每步已完成即跳过，失败优雅降级不中断）：
  - 发现照片：仅时间线未扫描/失败时执行；现场标记的照片类型（自检/进场/离场/设备）继续随候选带入。
  - 确认时间线：自动强制确认并应用到达/离场候选；但**用户手动设定过的时间**（来源 `user_input` / `manual`）绝不覆盖。
  - 筛选施工照片：Vision 分类（含 `ai_photo_analysis` 跨重启缓存）+ 自动选择安全自检照片与施工照片，仅未分类时执行；用户的手动选择/移除记录保留。
  - 重新计算里程：仅对没有成功路线的自驾人员计算（Google Routes）并生成 Static Maps 里程佐证；缺出行信息或未配置 API key 时跳过不报错。
- 新增回归测试 `test_ai_daily_report_auto_prepare.py`（5 个用例：未扫描草稿自动发现+确认时间线、重复调用幂等、手动时间保护、saved 草稿跳过、未登录 403）。相关 Phase 1/2/4/5/6/9 与 auto_confirm 套件回归 281 项通过（`test_cancel_draft` 为存量环境问题，与本改动无关）。

## [0.1.233] - 2026-09-17

### 改进（AI 智能日报 · 确认后自动生成工单日报）
- **确认草稿后自动生成工单日报**：此前确认 AI 日报草稿只把状态标记为 `confirmed`，还需要手动点「准备附件」和「正式保存」两步才能生成正式工单日报。现在 `/api/ai/daily-report/draft/<id>/confirm` 确认成功后自动串联执行：Phase 7 校验 → 准备附件清单 → **自动完成里程佐证合规审查**（按 2026-09-17 产品决策，自动视为已审查，写入字段与人工审查一致）→ Phase 9 正式保存，直接写入 `service_reports`，确认一步到位（`app.py`）。
- **优雅降级**：任何关卡未通过（校验存在 ERROR、附件清单完整性、出行方式无法映射、并发提交冲突等）时草稿保持 `confirmed` 状态不丢数据，响应中 `auto_formal_save.blocked_code` / `blocked_message` 携带原因，用户可修复后走原有「准备附件 / 正式保存」流程。
- **幂等重确认**：对已正式保存（`saved`）的草稿重复调用确认（AI Assistant 重试场景），返回 200 并携带已有正式报告 ID 与链接，不再报 400。
- **前端提示**（`static/ai-daily-report-review.js`）：确认成功后按结果显示「已确认并自动生成工单日报（Report #N）」或降级原因；草稿详情页正式保存区随之显示「查看正式日报」链接。
- 新增回归测试 `test_ai_daily_report_auto_confirm.py`（3 个用例：完整数据确认后自动生成报告且重复确认幂等、校验不通过保持 confirmed 并给出原因、已保存草稿重确认返回既有报告）。相关 Phase 6/9 及 AI 日报套件回归通过（未触及的 3 个存量失败与本改动无关：cancel_draft 的 CSRF 环境差异、phase3b 两个 Windows 特有路径/符号链接用例）。

## [0.1.232] - 2026-09-17

### 改进（AI 智能日报 · 安全自检照片自动选择）
- **现场标记的「自检照片」自动成为日报的安全自检照片**：此前现场拍照时工人选择的照片类型（自检/进场/离场/设备）只随候选照片带入草稿（`manual_classification`），但日报的「安全自检照片」区域仍显示"未选择"，需要用户在草稿页再手动标记一次。现在 `discover_photos_for_draft`（含幂等重扫分支）会把候选照片中带 `safety` 标记的自动追加进 `selected_safety_photos`（来源记为 `user_selected`，置信度 1.0），与施工照片的「自动筛选」机制对齐（`ai_daily_report/daily_report_service.py`）。
- 追加式同步，从不移除/重排已有选择：草稿内手动标记、视觉 AI 选取、用户更换的结果均不受影响；重复扫描不会产生重复条目。
- 新增回归测试：safety 标记照片自动入选、来源与主图字段正确、重扫幂等不重复（`test_field_photo_type_edit.py`，13 个用例全过）；相关回归 `test_ai_daily_report_safety_multi.py`、`test_photo_classification_revision.py`、`test_ai_daily_report_phase4.py` 共 85 项通过。

## [0.1.231] - 2026-09-17

### 修复（工作日报 · 内部用户看不到「查看」按钮）
- **工单详情页日报行对内部用户新增独立「查看」按钮**：此前该按钮槽位被「编辑」占用，「查看」标签只在 external_manager 上出现（`data-edit-url-label` 三元写死），导致内部员工/管理员看不到查看入口。现在每行新增 `data-view-url`（始终指向 Word 样式只读页 `/service-reports/<id>/view`），工具栏「查看」与「编辑」分成两个独立按钮：内部用户两个都有，「编辑」仍指向编辑表单；external_manager 的编辑按钮置灰、只保留查看（`templates/service_order_detail.html`）。
- 新增回归测试：内部管理员工单页同时可见「查看」（viewUrl → 只读页）与「编辑」（editUrl → 编辑表单）按钮（`test_service_report_external_view.py::test_06`，共 7 个用例）。

## [0.1.230] - 2026-09-17

### 改进（工作日报 · 外部用户查看权限）
- **外部账号的「工作日报-查看」权限真正生效**：此前权限矩阵默认给所有角色勾选了「工作日报 > 查看」，但日报查询页 `/reports/service-reports` 用 `is_internal_user()` 一刀切 403、导航也硬编码对外部管理员隐藏，导致勾选了查看仍然提示无权限。现在以权限矩阵为准：路由门槛改为 `has_action_permission("service_reports", "view")`，导航「报表 > 日报查询」对外部管理员按同一权限显示/隐藏（`app.py`、`templates/base.html`）。
- **新增 Word 导出样式的只读查看页** `GET /service-reports/<id>/view`（`templates/service_report_view.html`）：版式与导出的 Word 日报一致（现场服务日报标题、站点/业主/地址、服务人员、服务工时汇总、服务描述、交通与里程、保存/更换的配件、现场时间、四类照片、签字栏），无任何编辑控件，附「导出 Word」按钮（跟随导出权限显示）和打印按钮。外部管理员在工单详情页点日报行的「查看」也改为进入该只读页，不再进入内部编辑表单。
- **外部账号数据范围限定**：日报查询页对外部账号套用 `service_order_access_filters`（外部管理员限本客户工单的日报，外部员工限被授权工单），筛选下拉选项同步按可见范围过滤，不泄露其他客户数据（`app.py`、`templates/service_report_query.html`）。
- 新增回归测试 `test_service_report_external_view.py`（6 个用例：默认放行、显式取消勾选后 403、Word 样式只读页内容、客户范围隔离、导出按钮跟随导出权限、内部管理员不受影响）。

## [0.1.229] - 2026-09-17

### 改进（AI 智能日报 · 一句话生成与续改）
- **草稿详情页新增「一句话修改日报」对话框**（`templates/ai_daily_report_draft_detail.html`、`static/ai-daily-report-review.js`）：已生成草稿后可直接输入自然语言继续修改（如"把张三的出发地点改成 Hobbs, NM"、"张三改成乘车"、"今天不住宿"、"增加更换 A313 的 2 号保险丝"），走同一个 `/api/ai/daily-report/chat` 端点（带 `draft_id`），成功后整页刷新展示最新预览。支持 Ctrl/Cmd+Enter 提交。
- **修复无法用一句话把出行方式改回自驾的缺陷**：`WorkerInput.transportation` 由默认 `self_drive` 改为可空（`ai_daily_report/schemas.py`），`null` 语义为"用户未提及"——update 时保持原值、create 时落到默认 `self_drive`（`ai_daily_report/daily_report_service.py` 的 `_apply_update_worker`、`ai_daily_report/employee_resolution.py`）。旧逻辑把"用户明确说自驾"与默认值混为一谈，导致 `update_worker` 永远无法切回 `self_drive`；现在双向切换均可，且切换后会清除既有路线/里程缓存等待重算。
- **对话历史接入意图解析**（`app.py` chat 端点 + `ai_daily_report/intent_service.py` 既有参数）：每次解析时把本草稿最近几轮对话传给 DeepSeek，"改从家里出发"这类指代的理解不再只依赖草稿摘要。
- **提示词更新**（`ai_daily_report/prompts.py`）：transportation 仅在用户明确提及时输出、未提及为 null；`update_worker` 明确"只改用户提到的字段、其余保持 null 且不触发澄清"；origin 强制澄清规则限定于新建日报场景。
- 新增 4 个回归测试（`test_ai_daily_report_phase2.py`）：改回自驾、null 保持原值、transportation 校验、新人员默认自驾。新增根目录 `conftest.py`：测试会话自动注入随机的 `ADMIN_EMAIL`/`ADMIN_PASSWORD`（应用初始化要求），命令行不再需要传凭据。

## [0.1.228] - 2026-09-17

### 改进（现场拍照 · 水印时间）
- 「调整水印时间」改为两步式交互：点击按钮先弹出**密码验证窗口**，验证成功后该窗口关闭，再自动弹出**时间设定窗口**；设定后返回拍照流程。本组已验证过密码时再次调整会跳过密码步骤直接进入时间设定。
- 时间输入精确到**小时和分钟**（无秒）；确认时系统自动附加 **0–59 的随机秒数**，避免每张照片水印都显示 :00 秒，更接近真实拍摄。
- 明确失效时机：**本组照片全部上传完成后**，设定的时间自动失效，水印时间密码授权同步清除，下一组拍摄恢复使用手机系统时间（需再次调整须重新验证密码）。
- 界面新增英/西语翻译；密码窗口支持回车提交（`templates/field_work.html`、`static/field-work.js`、`static/field-i18n.js`）。

## [0.1.227] - 2026-09-17

### 改进（AI 智能日报）
- 自检照片改为全自动选取：照片分类后只要识别到自检照片（safety_person）候选，系统总是自动选取排名最高的一张（排序规则：正面站立人像 > 置信度 > 照片 ID），不再需要手动点击"自检照片"按钮。置信度低于自动选取阈值（默认 0.80）时照片仍会自动选中，但标记 `verification_required` 并在校验面板显示"需要人工确认"警告，用户可随时手动换图（`ai_daily_report/photo_classification.py` 的 `select_safety_photo`）。用户手动选择的照片仍然优先保留，不被自动选取覆盖。

## [0.1.226] - 2026-09-17

### 修复（安全）
- 发票导出页与发票详情页的客户/公司地址字段不再使用 `|safe` 直接输出未转义内容，改为新增的 `nl2br` 模板过滤器（先 HTML 转义、再保留换行），消除内部用户可注入的存储型 XSS（`templates/invoice_export.html`、`templates/invoice_detail.html`、`app.py`）。

### 修复（用户体验）
- 新增 403 友好错误页：外部账号访问无权限的工单/工作日报时，不再显示 Flask 默认英文 Forbidden 页，改为与站点风格一致的中文提示（外部账号提示仅能查看所属客户的工单并联系管理员开通权限）。`app.py` 新增 `@app.errorhandler(403)`，`templates/error.html` 的页面标题改为跟随传入标题。

## 更早版本

0.1.225 及之前的变更未记录在本文件中，可通过 `git log --oneline` 查看历史提交（功能提交 + 对应的 `release:` 提交）。
