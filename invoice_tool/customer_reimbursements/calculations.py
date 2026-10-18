"""Legacy-compatible row_field helpers; explicit runtime dependencies."""

def _row_field(row, key, default=0):
    """读取结算明细行的字段，兼容 dict 与数据库 Row（后者没有 .get）。"""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value
