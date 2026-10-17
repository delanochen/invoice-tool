# 发票路由连续十轮拆分与验收

基线 27f12df；只拆结构，不改业务规则。

迁移前验证：22 passed, 56 subtests passed in 16.12s。


## 第 1/10 轮：附件下载与预览

- 2 个路由迁入 `invoice_tool/invoices/attachment_read_routes.py`；原 URL、方法、登录装饰器、端点及 app 函数别名保留。
- 原函数主体经解析树逐项对照一致，SQL 字符串保持原值，其余函数未变。动态依赖通过显式回调/解析器注入。
- app.py：13567 → 13559 行。
- 专项验证：12 passed, 15 subtests passed in 9.63s。
