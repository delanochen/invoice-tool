-- v0.1.341 员工等级费率：没维护 = NULL，维护成 0 = 0。
--
-- 以前这些列是 DEFAULT 0 NOT NULL，「建等级时没填费率」和「明确把费率填成 0」
-- 落在库里都是 0，调用方只能靠 rate <= 0 猜「是不是没维护」。利润表就用这条
-- 猜，于是把 W1 外籍员工明确维护成 0 的交通补贴 / 里程补贴 / 公共交通 /
-- 租车驾驶全标成了「缺费率」并弹红条。
--
-- 改成可空之后：NULL = 没维护（利润表标缺费率），0 = 维护成 0（正常按 0 计薪，
-- 不标缺费率）。应用侧口径见 rate_engine.employee_rate()。
--
-- 只放宽列属性，**不改写既有的 0**：历史行里的 0 仍然按 0 参与工资计算，
-- 不动任何历史工资口径；只有「以后新建 / 编辑时不填」才会落 NULL。

ALTER TABLE employee_grades ALTER COLUMN standard_hourly_rate DROP NOT NULL;
ALTER TABLE employee_grades ALTER COLUMN standard_hourly_rate DROP DEFAULT;
ALTER TABLE employee_grades ALTER COLUMN transport_hourly_rate DROP NOT NULL;
ALTER TABLE employee_grades ALTER COLUMN transport_hourly_rate DROP DEFAULT;
ALTER TABLE employee_grades ALTER COLUMN overtime_hourly_rate DROP NOT NULL;
ALTER TABLE employee_grades ALTER COLUMN overtime_hourly_rate DROP DEFAULT;
ALTER TABLE employee_grades ALTER COLUMN holiday_hourly_rate DROP NOT NULL;
ALTER TABLE employee_grades ALTER COLUMN holiday_hourly_rate DROP DEFAULT;
ALTER TABLE employee_grades ALTER COLUMN car_hourly_rate DROP NOT NULL;
ALTER TABLE employee_grades ALTER COLUMN car_hourly_rate DROP DEFAULT;
ALTER TABLE employee_grades ALTER COLUMN car_mileage_rate DROP NOT NULL;
ALTER TABLE employee_grades ALTER COLUMN car_mileage_rate DROP DEFAULT;
ALTER TABLE employee_grades ALTER COLUMN rental_driving_hourly_rate DROP NOT NULL;
ALTER TABLE employee_grades ALTER COLUMN rental_driving_hourly_rate DROP DEFAULT;

INSERT INTO settings(key,value)
VALUES ('postgresql_0288_employee_grade_rate_nullable','complete')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;
