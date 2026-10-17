# 发票路由连续十轮拆分与验收

基线 27f12df；只拆结构，不改业务规则。

迁移前验证：22 passed, 56 subtests passed in 16.12s。


## 第 1/10 轮：附件下载与预览

- 2 个路由迁入 `invoice_tool/invoices/attachment_read_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13567 → 13559 行。
- 专项验证：12 passed, 15 subtests passed in 9.63s。


## 第 2/10 轮：附件上传与删除

- 2 个路由迁入 `invoice_tool/invoices/attachment_write_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13559 → 13541 行。
- 专项验证：13 passed, 18 subtests passed in 9.57s。


## 第 3/10 轮：ZIP 与 PDF 导出

- 2 个路由迁入 `invoice_tool/invoices/export_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13541 → 13531 行。
- 专项验证：1 passed, 2 subtests passed in 2.22s。


## 第 4/10 轮：发票详情

- 1 个路由迁入 `invoice_tool/invoices/detail_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13531 → 13520 行。
- 专项验证：1 passed, 3 subtests passed in 2.35s。


## 第 5/10 轮：发票邮件发送入口

- 1 个路由迁入 `invoice_tool/invoices/send_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13520 → 13512 行。
- 专项验证：1 passed, 5 subtests passed in 2.29s。


## 第 6/10 轮：管理员状态调整

- 1 个路由迁入 `invoice_tool/invoices/admin_status_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13512 → 13495 行。
- 专项验证：1 passed, 5 subtests passed in 2.45s。


## 第 7/10 轮：收款标记与撤销

- 2 个路由迁入 `invoice_tool/invoices/payment_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13495 → 13474 行。
- 专项验证：2 passed, 5 subtests passed in 2.44s。


## 第 8/10 轮：发票作废

- 1 个路由迁入 `invoice_tool/invoices/void_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13474 → 13446 行。
- 专项验证：1 passed, 7 subtests passed in 2.42s。


## 第 9/10 轮：发票删除

- 1 个路由迁入 `invoice_tool/invoices/delete_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13446 → 13427 行。
- 专项验证：1 passed, 5 subtests passed in 2.30s。


## 第 10/10 轮：联合验收

- 391 passed, 4 deselected, 2 warnings, 126 subtests passed in 253.53s (0:04:13)。
- 九组共 13 个路由迁移；app.py 13567 → 13427 行。
- 仅名称校验后的 invoice_test，持有协调锁；本轮新增分支对照使用模拟依赖及合成文件，不发送真实邮件、不写真实发票。
- 联合范围沿用已评估的发票、报销、日报、工资、工单、合同和付款条款测试；四项 series_id 旧夹具缺口精确排除。此为受限联合回归，不是全仓库测试。
- 原 cancel_reason 缺口、文件删除与事务回滚风险等既有问题未借结构迁移修复；未合并、未部署。
