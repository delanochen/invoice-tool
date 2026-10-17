# 员工报销附件存储拆分验收

日期：2026-10-17；阶段：5b；基线：2c718e4。

- 七个附件目录、路径、指纹、上传、读取和分组辅助函数迁移至 invoice_tool/employee_expenses/storage.py。
- app.py 从 15,002 行降至 14,937 行，净减少 65 行；保留原函数签名和运行时依赖解析。
- AST 核对：七个迁移函数主体一致，527 个其他顶层函数未变。
- 不修改文件类型限制、随机文件名、SHA-256/图片指纹、明细行归属、历史通用附件分组、文件异常处理、查重、结算同步、权限或付款规则。
- 迁移前回归：44 passed、5 subtests passed、3 deselected；迁移后：50 passed、5 subtests passed、3 deselected。
- 六项新增测试覆盖旧签名、普通文件及图片指纹对照、跨 1MB 分块读取、历史无明细附件分组、明细上传转发、通用附件拒绝、非法扩展名和磁盘写入失败，以及实际文件与数据库元数据往返。
- 数据库往返验证上传人、明细键、文件哈希、读取入口及文件原始内容；使用合成 PDF 和临时目录。
- 图片指纹测试产生两条同源 Pillow getdata 弃用警告，迁移前实现与新模块均存在；结构迁移没有改动该算法。
- 回归包含代报销、业务用途、稳健保存、结算链接显示、上一阶段表单模块、附件解读和删除依赖清理。
- 三项单独排除均已在迁移前复现：test_expense_return_admin_and_robust_save.py::RobustExpenseSaveTest 的 test_edit_save_preserves_item_referenced_by_settlement、test_sync_copy_swallows_copyfile_oserror、test_sync_copy_swallows_unique_index_race。旧结算夹具缺少当前必填 series_id；没有变更业务代码或约束掩盖失败。
- 测试数据库只使用名称校验后的 PostgreSQL invoice_test。未运行全仓库测试或附件转结算全套集成测试。
- git diff --check 通过。由 Codex 实施和验证；独立提交，未合并、未部署、未访问生产数据库。
