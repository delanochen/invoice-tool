"""Legacy-compatible residual_sql helpers; explicit runtime dependencies."""

def sql_statement_kind(sql):
    stripped = (sql or "").lstrip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].lower()


def is_read_sql(sql, *, sql_statement_kind):
    return sql_statement_kind(sql) in {"select", "with", "explain"}
