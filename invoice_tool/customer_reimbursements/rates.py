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
