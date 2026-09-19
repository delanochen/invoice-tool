# 2026-09-19 SQLite 修复

此工具针对已核查的历史记录，不是通用的自动删除孤立记录工具。未知异常必须重新分析；不要扩充白名单后直接执行。工具不随应用启动运行。

默认只读源库、在内存副本演练：

```sh
python3 scripts/repair_sqlite_integrity_20260919.py --database /srv/invoice-tool/data/invoices.db
```

确认演练结果后，显式应用（先取得写锁、做一致性备份并验证，再修复）：

```sh
python3 scripts/repair_sqlite_integrity_20260919.py --database /srv/invoice-tool/data/invoices.db --apply --backup-dir /srv/invoice-tool/backups
```

备份子目录使用独立时间戳且权限为 0700；包含完整原数据库与结果清单。原始变更行保存在业务库的 `data_repair_archive`，包括原值、替换字段、原因和修复时间。不要向普通用户暴露此维护表，备份和维护表应与业务数据同等级保护。

依据：

- 31 条历史外键异常全部有原 schema 的 CASCADE 或 SET NULL 规则；30 条孤立子记录归档后移出业务表，1 条审计仅清空失效 user_id，姓名、内容、日期全部保留。不重新创建已删除发票或猜测关联人。
- 37 条 selected_at 来自 settlement_review 的历史迁移 `datetime('now')`，可按 UTC 补齐。
- 国家 CA 的 created_at 有 audit_logs #178 原始 SQL 和带时区审计时间相互印证，可按 UTC 补齐。
- 照片 #1–16 为 2026-09-04 旧版相机照片；ea79320 之前 static/field-watermark.js 使用 captured_at 绘制水印，后加入 watermark_at 时旧记录仅获空默认值。补齐字段不重写图片、不移动文件、不改 capture_date。
- invoices.paid_at 的 7 个 YYYY-MM-DD 是用户输入的付款日期，不是缺失时区的时间戳，不修改；迁移 PostgreSQL 时应映射 DATE。

当前应用 db() 已启用每连接外键约束，新增测试验证拒绝孤立写入和级联删除。管理员 SQL 控制台或外部脚本仍需遵守约束，不要关闭 foreign_keys。

恢复：事务提交前失败自动回滚。提交后需要依据归档进行选择性恢复并处理原外键问题；只有在停写并确认不存在需要保留的新写入时才能整体恢复备份。不能把旧快照直接覆盖到仍在使用的数据库。
