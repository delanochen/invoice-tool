# 员工报销整单 AI 审核接口拆分验收

日期：2026-10-17；阶段：5i；基线：0d3df49。

- 整单 AI 审核路由迁移至 invoice_tool/employee_expenses/expense_ai_review_routes.py，保留 URL、GET/POST 方法、endpoint、登录/操作权限及 app 别名。
- app.py 从 14,347 行降至 14,328 行，净减少 19 行。
- AST 核对迁移函数主体一致，523 个其他顶层函数未变。
- GET 只读取现有结果，POST 强制执行原任务；空结果 JSON、错误状态、200/502 映射、提交和动态附件目录保持不变。
- 不修改模型、提示词、审核结论、夜间任务、审批或付款逻辑。
- 迁移前：82 passed、5 subtests passed、3 deselected；迁移后：86 passed、5 subtests passed、3 deselected。
- 四项新增测试验证路由身份、无记录 GET 不调用模型、POST 强制及目录动态解析、成功失败完整 JSON、员工禁止操作和缺失单据不调用模型。
- 新纳入现有 AI 审核测试，覆盖结论解析、结果复用、失败重试、重复报销输入、疑似附件展示及夜间 worker 选择和配置。
- 联合回归继续覆盖此前报销表单、归属、权限、附件、查重、人工复核和删除范围。
- 沿用三个已复现 series_id 旧夹具排除项；两条既有 Pillow 弃用警告保持。未运行全仓库测试。
- AI 调用模拟，数据库仅名称验证后的 PostgreSQL invoice_test；git diff --check 通过。
- Codex 实施和验证；隔离工作树独立提交，未合并、未部署、未访问生产数据库。
