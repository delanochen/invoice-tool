# 员工报销表单与明细保存拆分验收

日期：2026-10-17；阶段：5a；基线：1ee793d。

- 四个辅助函数迁移至 invoice_tool/employee_expenses/forms.py，app.py 保留原签名入口和动态依赖。
- app.py 从 15,136 行降至 15,002 行，净减少 134 行。
- AST 检查：四个函数主体一致，530 个其他顶层函数未变。
- 保留明细 ID、结算引用删除保护、附件清理及原有表单校验；不变更金额、燃油分类、审批、付款或报销归属规则。
- 迁移前回归：30 passed、5 subtests passed、3 failed。三个失败均为 test_expense_return_admin_and_robust_save.py::RobustExpenseSaveTest 下 test_edit_save_preserves_item_referenced_by_settlement、test_sync_copy_swallows_copyfile_oserror、test_sync_copy_swallows_unique_index_race：旧结算夹具未提供当前必填 series_id。
- 迁移后回归：34 passed、5 subtests passed、3 deselected；仅单独排除上述三个已复现的夹具失败，未修改业务代码或数据库约束来隐藏失败。
- 新增独立基线函数对照、旧签名、金额及车辆类型组合校验、保留项目 UPDATE 原 ID、禁止移除结算引用明细测试。引用保护新增用例使用模拟数据库；受旧夹具阻碍的实际结算引用集成测试仍待夹具更新后复跑。
- 数据库操作只使用经名称校验的 PostgreSQL invoice_test；git diff --check 通过。未运行全仓库测试。
- Codex 完成实施与验证；独立工作树单独提交，未合并、未部署、未访问生产数据库。
