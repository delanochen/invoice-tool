# CodeBuddy 执行、Codex 审核：连续二十轮

基线 ff94ef2；结构迁移，不改业务规则。

迁移前基线：30 passed, 59 subtests passed in 16.44s。


## 第 1/20 轮：settlement_access

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/access.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：require_customer_reimbursement。
- app.py：12683 → 12678 行。
- 审核验证：1 passed, 3 subtests passed in 1.83s。


## 第 2/20 轮：contract_rates

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/rates.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：resolve_contract_rates。
- app.py：12678 → 12667 行。
- 审核验证：1 passed, 2 subtests passed in 1.92s。


## 第 3/20 轮：missing_rates

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/rates.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：contract_rate_missing_summary。
- app.py：12667 → 12661 行。
- 审核验证：1 passed in 1.95s。


## 第 4/20 轮：used_rates

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/rates.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：used_customer_rate_fields。
- app.py：12661 → 12647 行。
- 审核验证：1 passed, 3 subtests passed in 1.86s。


## 第 5/20 轮：rate_guard

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/rates.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：assert_contract_rates_covered。
- app.py：12647 → 12633 行。
- 审核验证：1 passed, 3 subtests passed in 1.98s。


## 第 6/20 轮：holidays

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/holidays.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：observed_us_holidays。
- app.py：12633 → 12603 行。
- 审核验证：1 passed, 3 subtests passed in 1.93s。


## 第 7/20 轮：row_field

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/calculations.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：_row_field。
- app.py：12603 → 12599 行。
- 审核验证：1 passed, 5 subtests passed in 1.87s。


## 第 8/20 轮：expense_amount

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/calculations.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：customer_reimbursement_item_expense_amount。
- app.py：12599 → 12590 行。
- 审核验证：1 passed, 3 subtests passed in 1.92s。


## 第 9/20 轮：item_calculation

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/calculations.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：calculate_customer_reimbursement_item。
- app.py：12590 → 12563 行。
- 审核验证：1 passed, 3 subtests passed in 1.97s。


## 第 10/20 轮：project_mapping

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/project_mapping.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：expense_settlement_invoice_mapping。
- app.py：12563 → 12545 行。
- 审核验证：1 passed in 1.76s。


## 第 11/20 轮：project_field

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/project_mapping.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：expense_settlement_invoice_field。
- app.py：12545 → 12516 行。
- 审核验证：1 passed, 5 subtests passed in 1.92s。


## 第 12/20 轮：canonical_project

- CodeBuddy 实际写入 `invoice_tool/customer_reimbursements/project_mapping.py` 并修改 app 接线，Codex 核验文件内容与累计 AST，未接手业务文件修改。
- 迁移函数：canonical_project_mapping_key。
- app.py：12516 → 12507 行。
- 审核验证：1 passed, 5 subtests passed in 2.08s。
