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


def expense_settlement_invoice_field(project_name, *, expense_settlement_invoice_mapping, project_name_key):
    """按员工报销项目名查映射（全名精确优先，英文基名前缀匹配兜底）。

    映射表存全名（如 "MRO Supplies配件及耗材费"）：先全名精确匹配；再取每行的
    英文基名（首个非 ASCII 字符前的部分）做前缀匹配，基名长短倒序避免同前缀
    项目混淆（如 Express Delivery Fees快递费 vs Express Delivery Fees）。
    查不到返回 (None, None)：该项目不进入客户实报实销，也不拆发票行。
    """
    mapping = expense_settlement_invoice_mapping()
    key = project_name_key(project_name)
    if key in mapping:
        return mapping[key]
    bases = []
    for row_key, value in mapping.items():
        base = row_key
        for index, char in enumerate(row_key):
            if not char.isascii():
                base = row_key[:index]
                break
        base = base.rstrip()
        if base:
            bases.append((base, value))
    bases.sort(key=lambda item: len(item[0]), reverse=True)
    for base, value in bases:
        if key == base:
            return value
        if key.startswith(base):
            suffix = key[len(base):].lstrip()
            if suffix and (not suffix[0].isascii() or not suffix[0].isalnum()):
                return value
    return (None, None)


def canonical_project_mapping_key(value, canonical_keys, *, project_name_key):
    key = project_name_key(value)
    if key in canonical_keys:
        return key
    for canonical_key in canonical_keys:
        if not key.startswith(canonical_key):
            continue
        suffix = key[len(canonical_key):].lstrip()
        if suffix and (not suffix[0].isascii() or not suffix[0].isalnum()):
            return canonical_key
    return key
