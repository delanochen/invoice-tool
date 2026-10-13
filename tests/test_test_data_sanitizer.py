"""脱敏脚本的安全不变量（纯静态 + 纯本地，不连任何数据库）。

这些断言的意义：脱敏脚本是唯一被允许读生产库的自动化工具，
所以它的白名单、只读约束与"绝不写源库"必须被测试钉住。
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sanitize_test_database.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sanitize_test_database", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sanitizer():
    return load_module()


def test_source_and_target_are_fixed_constants(sanitizer):
    assert sanitizer.SOURCE_DATABASE == "invoice_production"
    assert sanitizer.TARGET_DATABASE == "invoice_test"
    assert sanitizer.ALLOWED_CONTAINERS == {"invoice-tool-postgres"}


def test_only_test_databases_are_writable(sanitizer):
    for name in ("invoice_test", "invoice_test_incoming", "invoice_test_scratch_2"):
        sanitizer.assert_database_name(name, writable=True)
    for name in ("invoice_production", "invoice_production_restore_validation", "postgres"):
        with pytest.raises(ValueError):
            sanitizer.assert_database_name(name, writable=True)
    for name in ("production", "Invoice_Test", "invoice test", ""):
        with pytest.raises(ValueError):
            sanitizer.assert_database_name(name, writable=True)


def test_purge_and_rewrite_sets_do_not_overlap(sanitizer):
    purged = set(sanitizer.PURGE_TABLES)
    assert len(sanitizer.PURGE_TABLES) == len(purged), "PURGE_TABLES 有重复项"
    for table, column, _expression in sanitizer.build_column_rules():
        assert table not in purged, f"{table}.{column} 既被清空又被改写"
    assert "email_delivery_logs" in purged
    assert "field_photos" in purged
    assert "knowledge_documents" in purged


def test_column_rules_cover_the_contract(sanitizer):
    rules = {(table, column): expression for table, column, expression in sanitizer.build_column_rules()}
    # 身份与联系方式的必查项
    for key in [
        ("users", "name"),
        ("users", "email"),
        ("users", "phone"),
        ("users", "address"),
        ("users", "password_hash"),
        ("clients", "name"),
        ("buyers", "name"),
        ("buyers", "detailed_address"),
        ("service_orders", "site_address"),
        ("service_reports", "departure_address"),
        ("bank_accounts", "bank_name"),
        ("bank_accounts", "last_four"),
    ]:
        assert key in rules, f"缺少脱敏规则: {key}"
    # 密码哈希必须走运行期注入，不能写死
    assert rules[("users", "password_hash")] == sanitizer.PASSWORD_HASH_PLACEHOLDER


def test_business_columns_are_not_touched(sanitizer):
    """金额/工时/里程/状态等用于验证计算的列不得被改写或清空。"""
    protected = {
        ("service_reports", "total_service_hours"),
        ("service_reports", "driving_miles"),
        ("service_reports", "travel_hours"),
        ("service_reports", "report_date"),
        ("expenses", "amount"),
        ("invoices", "payment_amount"),
        ("employee_payment_components", "amount"),
        ("employee_payment_orders", "net_amount"),
        ("profit_ledger", "revenue"),
        ("service_orders", "status"),
    }
    touched = {(table, column) for table, column, _expression in sanitizer.build_column_rules()}
    assert not (protected & touched)


def test_settings_secrets_are_purged_or_blanked(sanitizer):
    for key in ("deepseek_api_key", "google_maps_browser_api_key", "payment_account_number"):
        assert key in sanitizer.PURGE_SETTING_KEYS
    assert sanitizer.REWRITE_SETTINGS["deepseek_enabled"] == "0"
    assert sanitizer.REWRITE_SETTINGS["ai_review_enabled"] == "0"
    assert "api_key" in sanitizer.SETTINGS_SECRET_KEY_PATTERN


def test_write_helpers_are_never_called_with_the_source_database(sanitizer):
    source = SCRIPT.read_text(encoding="utf-8")
    for helper in ("psql_script", "psql_ddl"):
        assert f"{helper}(container, SOURCE_DATABASE" not in source
        assert f'{helper}(container, args.source' not in source
    # DDL（DROP/ALTER DATABASE）不能进事务块
    assert "psql_ddl(" in source


def test_probe_terms_are_relation_preserving(sanitizer):
    """假名必须由 id 推导，不能是固定串，否则页面关联会失去区分度。"""
    pairs = sanitizer.term_queries()
    assert len(pairs) >= 25
    for label, query in pairs:
        assert "chr(31)" in query, f"{label} 未按 原值<US>假名 格式返回"
    users_query = dict(pairs)["users.name"]
    assert "lpad(id::text, 3, '0')" in users_query
