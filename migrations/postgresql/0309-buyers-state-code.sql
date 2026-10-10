-- v0.1.xxx 站点州简写。
--
-- 站点（buyers）新增 state_code 列，保存美国州简写（如 "Houston, TX 77001" → "TX"）。
-- 新值在站点创建/编辑保存时由应用层从 detailed_address 自动解析（也可手动修改）；
-- 本迁移对既有站点做一次性回填（地址尾部 "州简写 + 邮编" 规则）。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + 回填只在 state_code='' 的行上执行，重复执行安全。
-- 明确不改：既有表 / 既有行 / 任何其他列。

ALTER TABLE buyers ADD COLUMN IF NOT EXISTS state_code text NOT NULL DEFAULT '';

-- 存量回填：只认地址尾部规则且属于美国州/领地清单，避免把街道后缀（ST/RD/NW）误当州。
-- 先归一化 U+00A0 不间断空格（复制粘贴地址常见），因为 PostgreSQL 正则 \s 只匹配 ASCII 空白。
UPDATE buyers
SET state_code = upper(x.st)
FROM (
    SELECT id,
           (regexp_match(replace(detailed_address, chr(160), ' '),
                         ',\s*([A-Za-z]{2})\s*(?:[,.]?\s*\d{5}(?:-\d{4})?)?\s*$'))[1] AS st
    FROM buyers
    WHERE detailed_address IS NOT NULL
      AND trim(detailed_address) <> ''
      AND state_code = ''
) x
WHERE buyers.id = x.id
  AND x.st IS NOT NULL
  AND upper(x.st) IN (
      'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA',
      'HI','ID','IL','IN','IA','KS','KY','LA','ME','MD',
      'MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
      'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC',
      'SD','TN','TX','UT','VT','VA','WA','WV','WI','WY',
      'DC','PR','VI','GU','AS','MP'
  );

INSERT INTO settings(key,value)
VALUES ('postgresql_0309_buyers_state_code','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
