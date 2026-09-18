# 更新日志 / Changelog

本文件记录每次版本发布的用户可见变更与重要内部修改，供人与后续自动化流程（发布、审计、AI 助手）了解各版本改动内容，避免重复排查已完成的工作。版本号与根目录 `VERSION` 文件保持一致，发布时以 `release: vX.Y.Z` 提交更新。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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
