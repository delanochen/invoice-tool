# 员工报销审核重置动作拆分验收

日期：2026-10-17；阶段：5o；基线：6510dc8。

- process_expense_action 迁移至 invoice_tool/employee_expenses/expense_reset_routes.py，复用既有三项显式付款取消依赖适配器。
- app.py 从 13,978 行降至 13,954 行，净减少 24 行。
- 只归一化付款取消 globals() 调用的适配差异后，路由主体一致，516 个其他顶层函数未变。
- POST URL、endpoint、登录/内部员工/管理员权限、仅支持 reset_workflow、无效 ID 和不存在单据处理、已付款保护、重置字段、审计与提交均不变。
- 迁移前：139 passed、13 subtests passed、3 deselected；迁移后：143 passed、13 subtests passed、3 deselected。
- 四项新增测试验证路由注册，管理员两次重置真实 ER 只产生一次付款 cancel 事件、付款组件原样保留、报销重置到 submitted/pending 并清理审核及付款字段，paid/reconciled 不可重置，员工及经理不可重置，旧付款动作拒绝，无效/缺失单据不取消付款。
- 重复重置仍可能重复记录报销 reset 审计和更新报销时间，测试只确认付款取消事件不重复，不宣称整个请求没有重复写入。
- 财务架构测试同步定位新的处理动作文件，原来禁止旧直接付款动作的断言保留。
- 联合回归继续覆盖此前报销、财务、付款组件、附件、查询、复核、作废/删除边界。
- 沿用三个 series_id 旧夹具排除项和两条 Pillow 弃用警告；测试库 cancel_reason 字段缺口、真实转结算集成限制、作废异常提交及文件回滚风险仍单独记录，未修改业务逻辑。未运行全仓库测试。
- 仅操作名称验证后的 PostgreSQL invoice_test；git diff --check 通过。Codex 实施验证，独立提交，未合并、未部署、未访问生产数据库。
- 下一阶段核对员工报销模块边界和跨模块注册、梳理剩余 app.py 领域与验证范围。
