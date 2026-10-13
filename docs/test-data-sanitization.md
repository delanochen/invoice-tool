# 测试库脱敏（invoice_production → invoice_test）

测试环境的数据必须"结构像生产、身份不像生产"。这份文档说明
`scripts/sanitize_test_database.py` 的边界、做法与回退方式。

## 一句话流程

```
只读导出 invoice_production
   ↓
恢复到 scratch 库 invoice_test_incoming
   ↓
执行脱敏规则 → 全库探针自检（有残留就按确定性映射原地替换，仍不过则中止）
   ↓
DROP invoice_test + RENAME scratch → invoice_test   ← 只有自检全通过才会走到这里
```

任何一步失败都不会动到现有 `invoice_test`，也不会留下半脱敏的数据。

## 保留什么（用于验证业务计算）

- 所有金额、工时、里程、税率、费率快照、折扣、预支抵扣
- 状态机字段、日期、周期标签、付款批次与事件
- 全部 id 关联（工单 ↔ 日报 ↔ 报销 ↔ 发票 ↔ 工资 ↔ 凭证）
- 工资周期纠偏的 correction group / allocation 结构、税务分类与 review 结论
- 科目表、凭证分录、利润台账等会计结构

## 脱掉什么

| 类别 | 处理方式 |
| --- | --- |
| 员工姓名 / 英文名 | `员工NNN` / `Staff NNN`（按 id 确定性映射） |
| 登录邮箱 | `userNNN@example.test`；admin 角色为 `adminNNN@example.test` |
| 电话 / 地址 | 占位串；`phone_verified` 归零 |
| 登录口令哈希 | 全部替换为测试管理员密码的哈希（可全员登录，便于权限测试） |
| 客户 / 业主 / 厂商 / 项目名 | `客户NNN` / `业主NNN` / `厂商NNN` / `项目NNN` |
| 站点、工单现场、出发地、里程佐证地址 | 占位地址 + 确定性测试坐标 |
| 经纬度 / geocode 结果 | 测试坐标；geocode 缓存字段清空 |
| 银行账户 / 分行 / 尾号 / 外部账户 id | 占位；流水参考号与指纹重写 |
| 银行与税务信息（settings） | 相关键整行删除或清空 |
| 发票备注、报销说明、凭证摘要等自由文本 | 显式改写 + 探针命中后按映射替换 |
| 附件 / 照片 / 知识库正文 / AI 日报与解读产物 | 整表清空（不复制任何文件） |
| 一次性令牌（save tokens） | 整表清空 |
| 外部接口凭据（DeepSeek / Google 系列密钥、LLM base_url） | 删行 |
| 邮件投递记录 | 整表清空 |
| `data_repair_archive`（历史导出归档） | 整表清空 |

同时 `settings` 里 `deepseek_enabled` / `ai_review_enabled` 置 0，
测试环境不接生产外部接口。

## 自检（探针）

换名之前，脚本从生产库只读取"身份关键词"（姓名、英文名、邮箱、电话、地址、
公司名等），然后在 scratch 库的**每一张有数据的表的每一个文本/json 列**上做子串搜索：

1. 先搜一遍，命中就按确定性映射把这些值原地替换掉；
2. 再搜一遍，仍有残留 → 写报告到 `/srv/invoice-tool-test/sanitize/probe-hits-*.txt`
   并**中止**，不换库。

也就是说：脱敏效果的判据不是"规则跑完了"，而是"探针查不到了"。

## 执行

```sh
cd /opt/invoice-tool-test

python3 scripts/sanitize_test_database.py --dry-run    # 只看计划，不连数据库、不写文件
python3 scripts/sanitize_test_database.py              # 正式执行
python3 scripts/sanitize_test_database.py --keep-raw   # 保留源库导出供排查（含未脱敏数据，谨慎）
```

脚本会自己处理测试容器的停机与拉起（换库要求无连接）。执行前请确认
`/srv/invoice-tool-test/admin-password.txt` 存在——它是新的统一测试登录口令来源。

## 已知的短暂暴露窗口

`pg_dump` 的产物在恢复完成前临时落在
`/srv/invoice-tool-test/sanitize/`（目录 0700、文件 0600），原样含生产数据；
成功结束后脚本会删除它。scratch 库在同一个 PostgreSQL 实例里短暂持有原始数据，
换名后即为脱敏结果。若要求"原始数据零落盘"，需要改造成流式管道，
这里明确记录现状而非默认它不存在。

## 回退

1. 脱敏前会自动备份现有测试库到
   `/srv/invoice-tool-test/backups/invoice-test-before-sanitize-<时间戳>.dump`；
2. 需要回退时：停测试容器 → `DROP DATABASE invoice_test` →
   `CREATE DATABASE invoice_test OWNER invoice_test` → `pg_restore` 该 dump → 起容器；
3. 脱敏失败（探针未过）时什么都不用做，现有测试库没被动过。

## 与部署流程的关系

脱敏是**按需**操作，不挂在每次部署上。日常部署（`staging` → 测试环境）只跑迁移与健康检查，
不会重灌数据；只有需要刷新测试数据集时才手动执行本脚本。
