# 员工报销查重复核与退回路由拆分验收

日期：2026-10-17；阶段：5f；基线：2ba704d。

- 查重复核和退回两个路由迁移至 invoice_tool/employee_expenses/expense_review_routes.py，保留 POST URL、endpoint、登录装饰器和 app 函数别名。
- app.py 从 14,443 行降至 14,406 行，净减少 37 行。
- AST 核对两个业务主体一致，529 个其他顶层函数未变（此前三个已迁移路由仍由注册器导出）。
- 复核角色、复核状态、退回前置状态、原因必填、审核元数据、参与人通知与审计不变。
- 审批、作废及删除仍使用 app 全局上下文调用付款模块，暂留原处；未把旧 globals() 调用搬进新模块，也未改付款规则。
- 迁移前：64 passed、5 subtests passed、3 deselected；迁移后：67 passed、5 subtests passed、3 deselected。
- 三项新增测试：原 POST 路由注册与别名；退回原因必填、元数据及创建人/归属人保留、双方通知与重复退回不覆盖；员工退回被拒绝、草稿不可退回。
- 联合回归继续覆盖三种查重复核状态、角色、保存令牌重放/回滚、代报销、附件、查重、业务用途及删除依赖。
- 沿用三个已复现 series_id 旧夹具排除项；两条既有 Pillow 弃用警告不变。仅使用名称验证后的 PostgreSQL invoice_test，未运行全仓库测试。
- git diff --check 通过。Codex 实施与验证；独立工作树单独提交，未合并、未部署、未访问生产数据库。
