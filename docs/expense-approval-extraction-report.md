# 员工报销审批与显式付款适配器拆分验收

日期：2026-10-17；阶段：5k；基线：d571d83。

- 审批路由迁移至 invoice_tool/employee_expenses/expense_approval_routes.py；expense_approval_services.py 用五项显式依赖调用原员工付款逻辑。
- 依赖只包括 db、now、log_action、lock_number_allocation、worker_tax_status_lookup，按请求解析。不把模块 globals() 传入新模块，不反向导入 app。
- app.py 从 14,282 行降至 14,254 行，净减少 28 行。
- 路由仅将 ensure_expense_payment_order(globals(), expense_id) 改为显式回调；AST 检查只归一化这一处已说明的调用差异，其余审批主体一致，521 个其他顶层函数未变。
- URL、POST 方法、endpoint、登录和权限、审批状态、参与人通知、审计、结算合计刷新及付款生成失败回滚保持不变。未修改 employee_finance 的业务实现。
- 迁移前：102 passed、5 subtests passed、3 deselected；迁移后：106 passed、5 subtests passed、3 deselected；另有财务领域及架构测试 20 passed。
- 四项新增测试：递归检查付款函数依赖键；五项依赖的运行时身份、重复审批不再生成付款；付款快照失败整体回滚；原路由注册及新模块无 globals()。
- 新纳入 13 项现有报销税务组件测试，覆盖实际付款单生成、明细合计、凭证及用途缺失、税务身份快照、重复 ensure、不重复组件、重开退役旧快照、取消保留快照和金额不一致回滚。
- 财务架构测试同步定位迁移后的审批入口，继续验证审批是报销付款单入口；单独补充执行 20 passed。
- 继续沿用三个已复现的 series_id 旧夹具排除项和两条 Pillow 弃用警告；未运行全仓库测试。
- 数据库仅名称验证后的 PostgreSQL invoice_test；git diff --check 通过。Codex 实施验证，独立提交，未合并、未部署、未访问生产数据库。
- 作废/删除仍在 app.py，下一阶段分别界定其付款取消接口。
