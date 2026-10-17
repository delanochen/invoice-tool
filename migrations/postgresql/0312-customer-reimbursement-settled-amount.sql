BEGIN;

-- 0312：工单结算支持「结算来源金额调整」（一条报销明细部分计入客户结算）。
--
-- 背景：客户审批工单结算时会整类拒付（工具费、服装费等），但员工报销往往
-- 已按一张单 / 一条明细提交并审核（例如一笔 9,800 里 8,000 可报、1,800 客户
-- 不承担）。原 links 表只有 amount_snapshot（报销全额），系统只能「整条计入」
-- 或「整条剔除」，无法表达部分计入；手工改结算单单元格又会让利润表仍按全额
-- 确认客户收入，毛利虚高。
--
-- settled_amount：本笔员工报销在这份客户结算里计入多少（客户结算金额）。
--                 NULL 表示未做调整，与 amount_snapshot 全额一致（全部历史行
--                 都落在这一档）。允许 0（明确不承担）；差额 = 报销金额 -
--                 客户结算金额，由计算得出，不重复存储。
-- adjustment_reason：调减原因。应用层强制：settled_amount < 全额必须填写。
--
-- 语义边界：员工报销金额、员工付款单、工资结算一概不动 —— 本列只调整
-- 「向甲方收取的金额」。甲方是否确认由结算单状态表达，不放进本列。

ALTER TABLE public.customer_reimbursement_expense_links
    ADD COLUMN IF NOT EXISTS settled_amount double precision;

ALTER TABLE public.customer_reimbursement_expense_links
    ADD COLUMN IF NOT EXISTS adjustment_reason text NOT NULL DEFAULT '';

-- 便于筛出「被客户调减过」的明细做对账，不影响全额计入的历史行。
CREATE INDEX IF NOT EXISTS idx_customer_reimbursement_expense_links_adjusted
    ON public.customer_reimbursement_expense_links (customer_reimbursement_id)
    WHERE settled_amount IS NOT NULL;

INSERT INTO settings(key,value)
VALUES ('postgresql_0312_customer_reimbursement_settled_amount','complete')
ON CONFLICT (key) DO UPDATE SET value=excluded.value;

COMMIT;
