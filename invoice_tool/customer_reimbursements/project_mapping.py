"""Legacy-compatible project_mapping helpers; explicit runtime dependencies."""

def expense_settlement_invoice_mapping(*, MRO_CANONICAL_PROJECT_NAME, db, is_mro_project_name, project_name_key):
    """员工报销项目 → (结算字段, 发票项目名)，读 expense_settlement_invoice_map 表。

    MRO 的发票项目在读取时归一为规范全名：迁移 0282 早期种子写的是裸名
    'MRO Supplies'，而升级脚本按「表已存在即跳过」执行，跑过旧版 0282 的库
    不会再补这一行（PostgreSQL 尤其如此，init_db 在 PG 上直接返回、不跑种子）。
    所以修正放在读取侧，不改写历史映射数据。
    """
    rows = db().execute(
        "select expense_project_name, settlement_field, invoice_project_name from expense_settlement_invoice_map"
    ).fetchall()
    mapping = {}
    for row in rows:
        invoice_project_name = row["invoice_project_name"]
        if is_mro_project_name(invoice_project_name):
            invoice_project_name = MRO_CANONICAL_PROJECT_NAME
        mapping[project_name_key(row["expense_project_name"])] = (
            row["settlement_field"],
            invoice_project_name,
        )
    return mapping
