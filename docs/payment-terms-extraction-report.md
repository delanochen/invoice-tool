# 付款账期结构拆分：第一阶段实施记录

## 范围

2026-10-17，在独立工作副本 app-payment-terms-extraction 上，基于 af4a797 抽离付款账期。没有带入主工作区随后新增的结算版本功能，没有修改其他业务逻辑、数据库结构、生产数据或部署配置。

app.py 从 18,410 行减少为 18,210 行，净减少 200 行；不是整个 app.py 拆分计划已完成。

## 模块

- invoice_tool/payment_terms/calculations.py：月份偏移、到期日、账期描述。
- invoice_tool/payment_terms/services.py：账期查询、默认账期、表单解析校验、可重算发票筛选。
- invoice_tool/payment_terms/routes.py：列出/新增、修改、启停、重算发票四个原有入口。
- test_payment_terms_module_contract.py：路由名称、URL、方法、旧 helper 兼容和动态替换依赖的测试。

app.py 保留原有八个 helper 名称与签名，并导出四个原 view 名称。新模块使用显式依赖注入，不反向导入 app，不把 globals() 当接口。db、now、log_action 和跨 helper 调用在调用时解析；calculate_payment_due_date 的 app 包装显式传入 shifted_month，保持测试或调用方替换旧函数的行为。

SQL 条件、日期算法、权限装饰器、事务、错误提示和模板上下文保持不变。嵌套函数移动造成的 SQL 字符串缩进差异在结构比对中作空白归一化；没有修改 SQL 业务条件。

## 实施与审查分工

CodeBuddy 首先生成纯计算候选模块。较大任务两次未能在五分钟内进入文件写入，随后由 Codex 生成明确的逐文件结构补丁，CodeBuddy 实际写入服务、路由、兼容测试及 app 接线。更新已有 calculations.py 时因先读后写保护而失败；Codex 核对后补齐月份 resolver 接口，并清理接线时重复插入的导入。

不能将一次简单消息测试或文件工具测试等同于复杂编码任务稳定。后续继续采用小范围补丁、明确文件白名单和有时限的执行，并由协调者独立验收。

## 验证证据

1. 源码 AST 对比：12 个抽离函数在规范化依赖名称、可选 resolver 参数及 SQL 缩进后与原逻辑一致；其余 559 个原有顶层函数未变。
2. 完整针对性运行：test_payment_terms.py、test_payment_terms_module_contract.py、test_customer_catalog_module_contract.py、test_basic_data_permissions.py，结果 34 通过、3 失败，另 6 个子测试通过。
3. 三项失败均为结算测试数据直接插入 customer_reimbursements 时未提供 series_id，碰到当前共享测试 schema 的 NOT NULL 约束。使用未重构的 af4a797 app.py 在临时副本复跑三项，全部复现相同失败。没有修改或跳过既有测试的断言，也没有回退数据库约束。
4. 最终代码复跑排除上述已确认的无关基线失败项后：34 passed, 3 deselected, 6 subtests passed（14.73秒）。
5. 每次测试连接先查询 current_database() 并严格要求 invoice_test。测试仅使用该库；没有访问正式数据库。没有运行完整仓库测试套件，不能宣称全量测试通过。
6. git diff --check 通过。Git 的 LF/CRLF 提示属于现有 Windows 换行转换提示。

三项既有失败：

- PaymentTermsTest.test_approved_expense_categories_transfer_to_matching_reimbursement_columns
- PaymentTermsTest.test_draft_customer_reimbursement_syncs_all_report_worker_days
- PaymentTermsTest.test_rental_vehicle_fuel_is_customer_billable_but_personal_fuel_is_not

## 集成与后续

本阶段单独提交并保留在独立副本，未合并主工作区、未部署。合并前需要把现有结算测试 fixture 与当前 schema 的差异另立任务处理，并结合主工作区的新提交再次验证。下一结构阶段建议为合同及合同附件，避免与结算功能并行改动混在同一提交。
