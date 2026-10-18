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


## 第 10/10 轮：跨模块联合验收

- 联合结果：380 passed, 4 deselected, 2 warnings, 79 subtests passed in 248.49s (0:04:08)。
- 范围包括全部本轮发票对照测试、此前日报/工时/工资、打卡照片、合同、工单、付款条款及报销/财务回归。
- 四项精确排除沿用已复现的 series_id 旧夹具问题；既有 cancel_reason 字段缺口、真实转结算集成覆盖限制和事务/文件回滚风险仍单独记录，未改业务规则。
- 此为受限 PostgreSQL 联合回归，不是全仓库测试；含 SQLite 或未评估外部依赖的测试未执行。
- app.py 当前 13567 行；本轮九次结构迁移累计减少 387 行，迁移 26 个函数。
- 测试只连接名称校验后的 invoice_test，并持有协调锁；文件测试使用临时目录及合成内容。未发送邮件，未访问生产，未合并或部署。
