"""Legacy-compatible contract_rates helpers; explicit runtime dependencies."""

def resolve_contract_rates(order_id, work_date, *, CUSTOMER_REIMBURSEMENT_RATE_FIELDS, contract_rate, db, lodging_reimbursement_limit):
    """按工单+日期从合同费率版本解析客户费率。

    返回 (rates, missing)：rates 为行字段->单价；missing 为缺失配置的展示名清单
    （合同不存在 / 日期无生效版本 / 版本缺条目）。缺什么由调用方醒目提示或拦截。
    """
    resolved = {}
    missing = []
    for field, rate_type, label in CUSTOMER_REIMBURSEMENT_RATE_FIELDS:
        result = contract_rate(db(), order_id, work_date, rate_type, lodging_fallback=lodging_reimbursement_limit())
        resolved[field] = result["rate"]
        if result["source"] == "missing":
            missing.append(label)
    return resolved, missing


def contract_rate_missing_summary(order_id, dates, *, resolve_contract_rates):
    """汇总一组日期上缺失的客户费率：{日期: [展示名, ...]}，仅返回有缺失的日期。"""
    summary = {}
    for work_date in sorted({str(value)[:10] for value in dates if value}):
        _, missing = resolve_contract_rates(order_id, work_date)
        if missing:
            summary[work_date] = missing
    return summary
