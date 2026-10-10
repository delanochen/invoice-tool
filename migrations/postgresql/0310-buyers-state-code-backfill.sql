-- v0.1.394 站点州简写回填修复：处理地址中的 U+00A0 不间断空格（NBSP）。
--
-- 背景：0309 回填使用 PostgreSQL 正则的 \s（仅 ASCII 空白），而部分站点地址
-- 是复制粘贴带入的 NBSP（U+00A0，UTF-8 c2a0），导致 ",<NBSP>TX 77577" 匹配不上，
-- 这些站点州简写漏填。本迁移先归一化 NBSP 再按同一规则回填剩余空行。
--
-- 幂等：只处理 state_code='' 的行，重复执行安全。明确不改其他列/表。

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
VALUES ('postgresql_0310_buyers_state_code_backfill','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;
