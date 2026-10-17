# 发票辅助模块连续十轮拆分与验收

基线 eca0599；结构迁移，不改业务规则。

迁移前对照测试结果：22 passed, 9 subtests passed in 14.03s。


## 第 1/10 轮：发票表单与关联单据解析

- 迁移 3 个函数至 invoice_tool/invoices/forms.py，原 app 签名和动态依赖保留。
- app.py：13954 → 13925 行，减少 29 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：1 passed in 2.28s。


## 第 2/10 轮：发票保存令牌

- 迁移 2 个函数至 invoice_tool/invoices/tokens.py，原 app 签名和动态依赖保留。
- app.py：13925 → 13913 行，减少 12 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：1 passed in 2.01s。


## 第 3/10 轮：发票编号与重复号查询

- 迁移 2 个函数至 invoice_tool/invoices/numbering.py，原 app 签名和动态依赖保留。
- app.py：13913 → 13892 行，减少 21 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：1 passed in 2.12s。


## 第 4/10 轮：发票附件存储

- 迁移 5 个函数至 invoice_tool/invoices/storage.py，原 app 签名和动态依赖保留。
- app.py：13892 → 13867 行，减少 25 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：12 passed, 9 subtests passed in 9.29s。


## 第 5/10 轮：发票附件重名与上传校验

- 迁移 5 个函数至 invoice_tool/invoices/attachment_names.py，原 app 签名和动态依赖保留。
- app.py：13867 → 13836 行，减少 31 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：12 passed, 9 subtests passed in 9.46s。


## 第 6/10 轮：结算及里程附件复制

- 迁移 3 个函数至 invoice_tool/invoices/copying.py，原 app 签名和动态依赖保留。
- app.py：13836 → 13781 行，减少 55 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：13 passed, 9 subtests passed in 9.34s。


## 第 7/10 轮：发票 HTML 与 ZIP 组装

- 迁移 2 个函数至 invoice_tool/invoices/exports.py，原 app 签名和动态依赖保留。
- app.py：13781 → 13761 行，减少 20 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：2 passed in 2.07s。


## 第 8/10 轮：发票邮件附件组装

- 迁移 3 个函数至 invoice_tool/invoices/email_attachments.py，原 app 签名和动态依赖保留。
- app.py：13761 → 13719 行，减少 42 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：1 passed in 2.21s。


## 第 9/10 轮：发票 PDF 生成

- 迁移 1 个函数至 invoice_tool/invoices/pdf.py，原 app 签名和动态依赖保留。
- app.py：13719 → 13567 行，减少 152 行。
- 累积 AST 对照基线 eca0599，迁移函数主体精确一致，其他函数未变；无路由/业务规则修改。
- 专项结果：1 passed in 2.07s。
