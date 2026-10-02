-- v0.1.xxx 用户英文名。
--
-- 注册界面与用户详情（编辑用户页 / 用户列表编辑弹窗）新增「英文名」字段，
-- 对应 users 表新增 english_name 列（可空字符串，默认 ''）。
--
-- 幂等：ADD COLUMN IF NOT EXISTS，重复执行安全。
-- 明确不改：既有表 / 既有行 / 任何其他列。

ALTER TABLE users ADD COLUMN IF NOT EXISTS english_name text NOT NULL DEFAULT '';

INSERT INTO settings(key,value)
VALUES ('postgresql_0296_users_english_name','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
