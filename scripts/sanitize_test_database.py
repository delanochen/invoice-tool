"""从 invoice_production 生成脱敏后的测试库 invoice_test。

设计原则
--------
1. **绝不在生产库上写任何东西**：源库只被 `pg_dump` 读取，读之前先落在
   `BEGIN READ ONLY` 事务里并校验 `transaction_read_only = on`。
2. **先落临时库，再原子换名**：dump 先恢复到 scratch 库（默认
   `invoice_test_incoming`），脱敏与自检全部通过后才
   `DROP DATABASE invoice_test` + `RENAME scratch -> invoice_test`。
   任一步失败都不会动到现有测试库，也不会留下半脱敏数据。
3. **保留业务结构、脱掉身份**：金额、工时、里程、状态、日期、费率、税率、
   以及所有 id 关联原样保留（用于验证业务计算）；员工姓名、客户/业主/厂商/
   公司名、站点与地址、电话、邮箱、银行与税务信息、附件、令牌全部替换或清除。
4. **确定性假名**：同一个 id 永远映射到同一个假名，因此报表、关联与引用
   在测试环境里仍然自洽。
5. **自证后才换名**：换名前把源库里出现过的姓名/邮箱/电话/地址在
   **全库文本列**再搜一遍；有命中就先按确定性映射原地替换，再复查一次，
   复查仍不过就中止（不换名）。

用法（在部署机 /opt/invoice-tool-test 下）
------------------------------------------
    python3 scripts/sanitize_test_database.py --dry-run    # 只打印计划，不碰任何东西
    python3 scripts/sanitize_test_database.py              # 执行
    python3 scripts/sanitize_test_database.py --keep-raw   # 保留源库导出供排查（含未脱敏数据）

脚本会自行停掉测试容器 `invoice-tool-test`（换库要求无连接），结束后再拉起。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# 硬白名单：越界的容器/库名一律拒绝
# --------------------------------------------------------------------------
ALLOWED_CONTAINERS = {"invoice-tool-postgres"}
SOURCE_DATABASE = "invoice_production"
TARGET_DATABASE = "invoice_test"
DEFAULT_SCRATCH_DATABASE = "invoice_test_incoming"
TEST_APP_CONTAINER = "invoice-tool-test"
DEFAULT_ADMIN_PASSWORD_FILE = "/srv/invoice-tool-test/admin-password.txt"
DEFAULT_STATE_DIR = "/srv/invoice-tool-test"

# 只有这些库名允许被写：测试库本体及其 scratch 版本
WRITABLE_DATABASE_PATTERN = re.compile(r"^invoice_test(_[a-z0-9_]+)?$")
FORBIDDEN_DATABASES = {
    "postgres",
    "template0",
    "template1",
    "invoice_production",
    "invoice_production_restore_validation",
}

PLACEHOLDER_ADDRESS = "1100 Test Ave, Testville, TX 77777"
PLACEHOLDER_SITE = "2200 Test Site Rd, Testville, TX 77777"
PLACEHOLDER_DEPOT = "3100 Test Depot Ave, Testville, TX 77777"
PLACEHOLDER_COMPANY = "测试公司（TEST）"
PASSWORD_HASH_PLACEHOLDER = ":TEST_PASSWORD_HASH"
SEPARATOR = "chr(31)"

# --------------------------------------------------------------------------
# 整表清除：附件 / 照片 / 令牌 / 原始导出 / 模型输出
# 测试环境不复制任何文件，留着行只会变成悬空引用
# --------------------------------------------------------------------------
PURGE_TABLES = [
    "service_report_attachments",
    "invoice_attachments",
    "expense_attachments",
    "customer_reimbursement_attachments",
    "contract_attachments",
    "company_attachments",
    "user_attachments",
    "asset_photos",
    "clock_in_photos",
    "field_photos",
    "voucher_attachment_links",
    "knowledge_documents",
    "knowledge_document_versions",
    "ai_daily_report_drafts",
    "ai_daily_report_manifest_sources",
    "ai_daily_report_manifest_roles",
    "ai_daily_report_prepared_assets",
    "ai_daily_report_actions",
    "ai_daily_report_attachment_manifests",
    "ai_daily_report_formal_commits",
    "expense_ai_reviews",
    "expense_attachment_interpretations",
    "expense_duplicate_checks",
    "ai_photo_analysis",
    "service_report_save_tokens",
    "expense_save_tokens",
    "invoice_save_tokens",
    "email_delivery_logs",
    "data_repair_archive",
]

# 凭据类 settings：整行删除
PURGE_SETTING_KEYS = [
    "deepseek_api_key",
    "ai_interpret_api_key",
    "google_maps_browser_api_key",
    "google_geocoding_api_key",
    "google_places_api_key",
    "google_routes_api_key",
    "google_static_maps_api_key",
    "field_watermark_time_password",
    "payment_account_number",
    "payment_routing_number",
    "payment_swift_bic",
    "payment_beneficiary",
    "payment_bank_name",
    "ai_interpret_base_url",
]

# 公司身份：改写而非删除，避免页面缺字段
REWRITE_SETTINGS = {
    "company_name": PLACEHOLDER_COMPANY,
    "company_email": "test-company@example.test",
    "company_phone": "555-0100",
    "company_address": PLACEHOLDER_ADDRESS,
    "company_ein": "",
    "company_registration_number": "",
    "company_tax_note": "",
    # 测试环境不得调用生产外部接口
    "deepseek_enabled": "0",
    "ai_review_enabled": "0",
}

# 兜底：键名带这些字样的 settings 一律清空值
SETTINGS_SECRET_KEY_PATTERN = "(api_key|password|secret|token|routing|swift|iban)"


def pad(expression: str) -> str:
    return f"lpad(({expression})::text, 3, '0')"


def test_coordinate(column: str, base: float, span: int) -> str:
    """确定性测试坐标：全部落在测试城市，且每行位置不重叠。"""
    return f"({base} + (({column}) % {span}) * 0.001)::double precision"


# --------------------------------------------------------------------------
# 字段级脱敏规则：(表, 列, SQL 表达式)
# --------------------------------------------------------------------------
def build_column_rules() -> list[tuple[str, str, str]]:
    rules: list[tuple[str, str, str]] = []

    def add(table: str, column: str, expression: str) -> None:
        rules.append((table, column, expression))

    # 员工（含登录凭据：统一改成测试密码的哈希）
    add("users", "name", f"'员工' || {pad('id')}")
    add("users", "english_name", f"'Staff ' || {pad('id')}")
    add(
        "users",
        "email",
        "(case when lower(role) in ('admin', 'administrator') then 'admin' else 'user' end)"
        f" || {pad('id')} || '@example.test'",
    )
    add("users", "phone", "''")
    add("users", "phone_verified", "0")
    add("users", "phone_verified_at", "NULL")
    add("users", "address", f"'{PLACEHOLDER_ADDRESS}'")
    add("users", "password_hash", PASSWORD_HASH_PLACEHOLDER)

    # 客户 / 业主 / 厂商 / 项目
    add("clients", "name", f"'客户' || {pad('id')}")
    add("clients", "short_name", f"'C' || {pad('id')}")
    add("clients", "contact_name", f"'客户联系人' || {pad('id')}")
    add("clients", "email", f"'client' || {pad('id')} || '@example.test'")
    add("clients", "address", f"'{PLACEHOLDER_ADDRESS}'")
    add("owners", "name", f"'业主' || {pad('id')}")
    add("manufacturers", "name", f"'厂商' || {pad('id')}")
    add("projects", "name", f"'项目' || {pad('id')}")
    add("projects", "name_key", f"'项目' || {pad('id')}")

    # 站点
    add("buyers", "name", f"'站点' || {pad('id')}")
    add("buyers", "contact_name", f"'站点联系人' || {pad('id')}")
    add("buyers", "contact_details", f"'555-0100 ext ' || {pad('id')}")
    add("buyers", "detailed_address", f"'{PLACEHOLDER_SITE}'")
    add("buyers", "geocode_address", "NULL")
    add("buyers", "email", f"'site' || {pad('id')} || '@example.test'")
    add("buyers", "owner", f"'业主' || {pad('coalesce(owner_id, id)')}")
    add("buyers", "latitude", test_coordinate("id", 32.7767, 100))
    add("buyers", "longitude", test_coordinate("id", -96.7970, 97))

    # 合同
    add("contracts", "title", f"'合同' || {pad('id')}")
    add("contracts", "project_name", f"'项目' || {pad('coalesce(client_id, id)')}")
    add("contracts", "notes", "NULL")

    # 工单
    add("service_orders", "client_name", f"'客户' || {pad('coalesce(client_id, id)')}")
    add(
        "service_orders",
        "site_address",
        f"'站点' || {pad('coalesce(buyer_id, id)')} || ' — {PLACEHOLDER_SITE}'",
    )
    add("service_orders", "client_order_number", f"'TEST-' || {pad('id')}")
    add("service_orders", "buyer_contact_name", f"'站点联系人' || {pad('coalesce(buyer_id, id)')}")
    add("service_orders", "buyer_contact_details", f"'555-0100 ext ' || {pad('coalesce(buyer_id, id)')}")
    add("service_orders", "geocode_address", "NULL")
    add("service_orders", "latitude", test_coordinate("id", 32.7767, 100))
    add("service_orders", "longitude", test_coordinate("id", -96.7970, 97))

    # 工作日报
    add("service_reports", "departure_address", f"'{PLACEHOLDER_DEPOT}'")
    add("service_reports", "site_address", f"'站点现场 — {PLACEHOLDER_SITE}'")
    add(
        "service_reports",
        "cabinet_number",
        "(case when cabinet_number is null then null else 'CAB-' || " + pad("id") + " end)",
    )
    add("service_reports", "arrival_photo_relative_path", "NULL")
    add("service_reports", "arrival_photo_hash", "NULL")
    add("service_reports", "departure_photo_relative_path", "NULL")
    add("service_reports", "departure_photo_hash", "NULL")

    # 日报随行人员与里程佐证
    add("service_report_workers", "origin_address", f"'{PLACEHOLDER_DEPOT}'")
    add("service_report_mileage_evidence", "origin_address", f"'{PLACEHOLDER_DEPOT}'")
    add("service_report_mileage_evidence", "destination_address", f"'{PLACEHOLDER_SITE}'")
    add("service_report_mileage_evidence", "route_fingerprint", f"'TEST-ROUTE-' || {pad('id')}")
    add("service_report_mileage_evidence", "attachment_id", "NULL")

    # 报销（project 是项目名文本）
    project_expression = "(case when project_id is null then '项目' else '项目' || " + pad("project_id") + " end)"
    add("expenses", "project", project_expression)
    add("expense_items", "project", project_expression)

    # 发票付款备注
    add("invoices", "payment_note", "NULL")

    # 银行
    add("bank_accounts", "account_name", f"'测试账户' || {pad('id')}")
    add("bank_accounts", "bank_name", "'Test Bank'")
    add("bank_accounts", "last_four", "'0000'")
    add("bank_accounts", "external_account_id", "NULL")
    add("bank_transactions", "reference_number", f"'TEST-REF-' || {pad('id')}")
    add("bank_transactions", "external_transaction_id", "NULL")
    add("bank_transactions", "import_fingerprint", f"'test-fingerprint-' || {pad('id')}")

    # 审计日志的写操作人
    add(
        "audit_logs",
        "user_name",
        "(case when user_id is null then '系统' else '员工' || " + pad("user_id") + " end)",
    )

    # 报价单
    add("quotations", "customer", f"'客户' || {pad('coalesce(client_id, id)')}")
    add("quotations", "project_name", f"'项目' || {pad('id')}")
    add("quotations", "project_location", "'Testville, TX'")
    add("quotations", "prepared_by", f"'员工' || {pad('coalesce(created_by, id)')}")

    # 客户对账单
    add("customer_reimbursements", "file_name", f"'reimbursement-' || {pad('id')} || '.xlsx'")
    add("customer_reimbursements", "stored_filename", f"'reimbursement-' || {pad('id')} || '.xlsx'")
    add(
        "customer_reimbursement_items",
        "worker_name",
        "(case when source_worker_user_id is null then '员工' else '员工' || "
        + pad("source_worker_user_id")
        + " end)",
    )

    # 员工付款 / 预支
    add("employee_payment_orders", "external_transaction_id", "NULL")
    add("employee_advances", "attachment_name", f"'advance-' || {pad('id')} || '.pdf'")
    add("employee_advances", "attachment_stored_filename", f"'advance-' || {pad('id')} || '.pdf'")
    add("employee_advances", "external_transaction_id", "NULL")

    # 资产
    add("assets", "name", f"'设备' || {pad('id')} || ' — ' || category")
    add("assets", "serial_number", f"'TEST-SN-' || lpad(id::text, 4, '0')")

    return rules


# --------------------------------------------------------------------------
# 自由文本探针：哪些源值必须从全库文本列里消失
# 每条查询返回 "<原值><US><假名>"，US = chr(31)
# --------------------------------------------------------------------------
def term_queries() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    # 员工
    pairs.append((
        "users.name",
        "select btrim(name) || chr(31) || ('员工' || lpad(id::text, 3, '0')) from users "
        "where length(btrim(name)) >= 3",
    ))
    pairs.append((
        "users.english_name",
        "select btrim(english_name) || chr(31) || ('Staff ' || lpad(id::text, 3, '0')) from users "
        "where length(btrim(english_name)) >= 3",
    ))
    pairs.append((
        "users.email",
        "select btrim(email) || chr(31) || ((case when lower(role) in ('admin','administrator') "
        "then 'admin' else 'user' end) || lpad(id::text, 3, '0') || '@example.test') from users "
        "where length(btrim(email)) >= 7",
    ))
    pairs.append((
        "users.phone",
        "select btrim(phone) || chr(31) || ('555-0100') from users where length(btrim(phone)) >= 7",
    ))
    pairs.append((
        "users.address",
        f"select btrim(address) || chr(31) || ('{PLACEHOLDER_ADDRESS}') from users "
        "where length(btrim(address)) >= 8",
    ))

    # 客户
    pairs.append((
        "clients.name",
        "select btrim(name) || chr(31) || ('客户' || lpad(id::text, 3, '0')) from clients "
        "where length(btrim(name)) >= 3",
    ))
    pairs.append((
        "clients.short_name",
        "select btrim(short_name) || chr(31) || ('C' || lpad(id::text, 3, '0')) from clients "
        "where length(btrim(short_name)) >= 3",
    ))
    pairs.append((
        "clients.contact_name",
        "select btrim(contact_name) || chr(31) || ('客户联系人' || lpad(id::text, 3, '0')) from clients "
        "where contact_name is not null and length(btrim(contact_name)) >= 3",
    ))
    pairs.append((
        "clients.email",
        "select btrim(email) || chr(31) || ('client' || lpad(id::text, 3, '0') || '@example.test') "
        "from clients where email is not null and length(btrim(email)) >= 7",
    ))
    pairs.append((
        "clients.address",
        f"select btrim(address) || chr(31) || ('{PLACEHOLDER_ADDRESS}') from clients "
        "where address is not null and length(btrim(address)) >= 8",
    ))

    # 站点
    pairs.append((
        "buyers.name",
        "select btrim(name) || chr(31) || ('站点' || lpad(id::text, 3, '0')) from buyers "
        "where length(btrim(name)) >= 3",
    ))
    pairs.append((
        "buyers.contact_name",
        "select btrim(contact_name) || chr(31) || ('站点联系人' || lpad(id::text, 3, '0')) from buyers "
        "where contact_name is not null and length(btrim(contact_name)) >= 3",
    ))
    pairs.append((
        "buyers.email",
        "select btrim(email) || chr(31) || ('site' || lpad(id::text, 3, '0') || '@example.test') "
        "from buyers where email is not null and length(btrim(email)) >= 7",
    ))
    pairs.append((
        "buyers.detailed_address",
        f"select btrim(detailed_address) || chr(31) || ('{PLACEHOLDER_SITE}') from buyers "
        "where detailed_address is not null and length(btrim(detailed_address)) >= 8",
    ))
    pairs.append((
        "buyers.geocode_address",
        f"select btrim(geocode_address) || chr(31) || ('{PLACEHOLDER_SITE}') from buyers "
        "where geocode_address is not null and length(btrim(geocode_address)) >= 8",
    ))

    # 业主 / 厂商 / 项目
    pairs.append((
        "owners.name",
        "select btrim(name) || chr(31) || ('业主' || lpad(id::text, 3, '0')) from owners "
        "where length(btrim(name)) >= 3",
    ))
    pairs.append((
        "manufacturers.name",
        "select btrim(name) || chr(31) || ('厂商' || lpad(id::text, 3, '0')) from manufacturers "
        "where length(btrim(name)) >= 3",
    ))
    pairs.append((
        "projects.name",
        "select btrim(name) || chr(31) || ('项目' || lpad(id::text, 3, '0')) from projects "
        "where length(btrim(name)) >= 3",
    ))

    # 工单 / 日报里的地址
    pairs.append((
        "service_orders.site_address",
        f"select btrim(site_address) || chr(31) || ('站点 — {PLACEHOLDER_SITE}') from service_orders "
        "where length(btrim(site_address)) >= 8",
    ))
    pairs.append((
        "service_orders.geocode_address",
        f"select btrim(geocode_address) || chr(31) || ('{PLACEHOLDER_SITE}') from service_orders "
        "where geocode_address is not null and length(btrim(geocode_address)) >= 8",
    ))
    pairs.append((
        "service_reports.site_address",
        f"select btrim(site_address) || chr(31) || ('站点现场 — {PLACEHOLDER_SITE}') from service_reports "
        "where site_address is not null and length(btrim(site_address)) >= 8",
    ))
    pairs.append((
        "service_reports.departure_address",
        f"select btrim(departure_address) || chr(31) || ('{PLACEHOLDER_DEPOT}') from service_reports "
        "where departure_address is not null and length(btrim(departure_address)) >= 8",
    ))
    pairs.append((
        "service_report_workers.origin_address",
        f"select btrim(origin_address) || chr(31) || ('{PLACEHOLDER_DEPOT}') from service_report_workers "
        "where length(btrim(origin_address)) >= 8",
    ))
    pairs.append((
        "service_report_mileage_evidence.origin_address",
        f"select btrim(origin_address) || chr(31) || ('{PLACEHOLDER_DEPOT}') "
        "from service_report_mileage_evidence where length(btrim(origin_address)) >= 8",
    ))
    pairs.append((
        "service_report_mileage_evidence.destination_address",
        f"select btrim(destination_address) || chr(31) || ('{PLACEHOLDER_SITE}') "
        "from service_report_mileage_evidence where length(btrim(destination_address)) >= 8",
    ))

    # 报价单客户名
    pairs.append((
        "quotations.customer",
        "select btrim(customer) || chr(31) || ('客户' || lpad(id::text, 3, '0')) from quotations "
        "where length(btrim(customer)) >= 3",
    ))

    # 公司身份
    pairs.append((
        "settings.company_name",
        f"select btrim(value) || chr(31) || ('{PLACEHOLDER_COMPANY}') from settings "
        "where key = 'company_name' and length(btrim(value)) >= 3",
    ))
    pairs.append((
        "settings.company_email",
        "select btrim(value) || chr(31) || ('test-company@example.test') from settings "
        "where key = 'company_email' and length(btrim(value)) >= 7",
    ))
    pairs.append((
        "settings.company_phone",
        "select btrim(value) || chr(31) || ('555-0100') from settings "
        "where key = 'company_phone' and length(btrim(value)) >= 7",
    ))
    pairs.append((
        "settings.company_address",
        f"select btrim(value) || chr(31) || ('{PLACEHOLDER_ADDRESS}') from settings "
        "where key = 'company_address' and length(btrim(value)) >= 8",
    ))

    return pairs


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------
def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}", flush=True)


def docker(*args: str, stdin: bytes | None = None, capture: bool = True) -> str:
    result = subprocess.run(
        ["docker", *args],
        input=stdin,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"docker {' '.join(args)} 失败: {detail}")
    return (result.stdout or b"").decode("utf-8", "replace")


def psql(container: str, database: str, sql: str) -> str:
    return docker(
        "exec", "-i", container, "psql", "-U", "invoice_owner", "-d", database,
        "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        stdin=sql.encode("utf-8"),
    )


def psql_script(container: str, database: str, script: str) -> None:
    """在单个事务里执行；任何错误整体回滚。"""
    docker(
        "exec", "-i", container, "psql", "-U", "invoice_owner", "-d", database,
        "-X", "-q", "-v", "ON_ERROR_STOP=1",
        stdin=("BEGIN;\n" + script + "\nCOMMIT;\n").encode("utf-8"),
    )


def psql_ddl(container: str, database: str, statement: str) -> None:
    """执行不能进事务块的语句（CREATE/DROP/ALTER DATABASE）。"""
    docker(
        "exec", "-i", container, "psql", "-U", "invoice_owner", "-d", database,
        "-X", "-q", "-v", "ON_ERROR_STOP=1",
        stdin=(statement.strip() + "\n").encode("utf-8"),
    )


def assert_database_name(name: str, *, writable: bool) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name or ""):
        raise ValueError(f"非法库名: {name!r}")
    if writable:
        if name in FORBIDDEN_DATABASES:
            raise ValueError(f"拒绝写入生产库/系统库: {name!r}")
        if not WRITABLE_DATABASE_PATTERN.fullmatch(name):
            raise ValueError(f"拒绝写入非测试库: {name!r}")
    elif not name:
        raise ValueError("库名不能为空")


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def assert_source_read_only(container: str) -> None:
    flag = psql(container, SOURCE_DATABASE, "BEGIN READ ONLY;\nSHOW transaction_read_only;\nCOMMIT;\n").strip()
    if flag != "on":
        raise RuntimeError("源库未进入只读事务，立即中止")
    identity = psql(container, SOURCE_DATABASE, "select current_database();\n").strip()
    if identity != SOURCE_DATABASE:
        raise RuntimeError(f"源库身份不符: {identity!r}")


def public_tables(container: str, database: str) -> dict[str, int]:
    """返回 {表: 行数}，行数为 -1 表示未知（未 ANALYZE）。"""
    output = psql(
        container,
        database,
        "select c.relname || chr(31) || c.reltuples::bigint from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r';\n",
    )
    tables: dict[str, int] = {}
    for line in output.splitlines():
        parts = line.split(chr(31))
        if len(parts) == 2:
            tables[parts[0]] = int(parts[1])
    return tables


def text_columns(container: str, database: str) -> list[tuple[str, str, str]]:
    output = psql(
        container,
        database,
        "select table_name || chr(31) || column_name || chr(31) || data_type "
        "from information_schema.columns where table_schema = 'public' and data_type in "
        "('text', 'character varying', 'character', 'jsonb', 'json') "
        "order by table_name, ordinal_position;\n",
    )
    columns = []
    for line in output.splitlines():
        parts = line.split(chr(31))
        if len(parts) == 3:
            columns.append((parts[0], parts[1], parts[2]))
    return columns


def inbound_foreign_keys(container: str, database: str) -> list[tuple[str, str, str, str]]:
    """返回 (子表, 子列, 父表, 是否可空) —— 仅覆盖 public schema。"""
    query = (
        "select tc.table_name || chr(31) || kcu.column_name || chr(31) || ccu.table_name || chr(31) "
        "|| coalesce((select is_nullable from information_schema.columns c "
        "  where c.table_schema = tc.table_schema and c.table_name = tc.table_name "
        "    and c.column_name = kcu.column_name), 'NO') "
        "from information_schema.table_constraints tc "
        "join information_schema.key_column_usage kcu "
        "  on kcu.constraint_name = tc.constraint_name and kcu.table_schema = tc.table_schema "
        "join information_schema.constraint_column_usage ccu "
        "  on ccu.constraint_name = tc.constraint_name and ccu.table_schema = tc.table_schema "
        "where tc.constraint_type = 'FOREIGN KEY' and tc.table_schema = 'public';\n"
    )
    rows = []
    for line in psql(container, database, query).splitlines():
        parts = line.split(chr(31))
        if len(parts) == 4:
            rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


# --------------------------------------------------------------------------
# 脱敏脚本生成
# --------------------------------------------------------------------------
def build_sanitize_script(
    container: str,
    database: str,
    password_hash: str,
) -> tuple[str, list[str]]:
    tables = public_tables(container, database)
    purge = [table for table in PURGE_TABLES if table in tables]
    the_missing = sorted(set(PURGE_TABLES) - set(purge))
    if the_missing:
        log(f"note: 库中不存在这些待清空表，跳过: {', '.join(the_missing)}")

    lines: list[str] = [
        "SET LOCAL client_min_messages = warning;",
        "SET LOCAL statement_timeout = '600s';",
    ]

    purge_set = set(purge)
    lines.append("-- 1) 先解开指向待清空表的外键，再删行")
    for child, column, parent, nullable in inbound_foreign_keys(container, database):
        if parent not in purge_set or child in purge_set:
            continue
        if nullable != "YES":
            raise RuntimeError(f"待清空表的引用列不可为空，需人工决策: {child}.{column} -> {parent}")
        lines.append(f"UPDATE {child} SET {column} = NULL WHERE {column} IS NOT NULL;")

    for table in purge:
        lines.append(f"DELETE FROM {table};")

    lines.append("-- 2) 字段级脱敏")
    for table, column, expression in build_column_rules():
        if table not in tables:
            continue
        if expression == PASSWORD_HASH_PLACEHOLDER:
            expression = sql_literal(password_hash)
        lines.append(f"UPDATE {table} SET {column} = {expression};")

    lines.append("-- 3) settings：删凭据、改公司身份、兜底清空疑似密钥")
    lines.append(
        "DELETE FROM settings WHERE key IN ("
        + ", ".join(sql_literal(key) for key in PURGE_SETTING_KEYS)
        + ");"
    )
    for key, value in REWRITE_SETTINGS.items():
        lines.append(f"UPDATE settings SET value = {sql_literal(value)} WHERE key = {sql_literal(key)};")
    lines.append(
        f"UPDATE settings SET value = '' WHERE key ~ {sql_literal(SETTINGS_SECRET_KEY_PATTERN)};"
    )

    if "llm_configs" in tables:
        lines.append("DELETE FROM llm_configs;")

    return "\n".join(lines) + "\n", purge


# --------------------------------------------------------------------------
# 探针与自由文本替换
# --------------------------------------------------------------------------
def fetch_terms(container: str) -> dict[str, str]:
    """返回 {原值: 假名}，按原值长度倒序排列（dict 保序）。"""
    mapping: dict[str, str] = {}
    for label, query in term_queries():
        script = f"BEGIN READ ONLY;\nSET LOCAL statement_timeout = '60s';\n{query};\nCOMMIT;\n"
        try:
            output = psql(container, SOURCE_DATABASE, script)
        except RuntimeError as error:
            raise RuntimeError(f"读取生产库失败（{label}）: {error}") from error
        for line in output.splitlines():
            if chr(31) not in line:
                continue
            term, pseudonym = line.split(chr(31), 1)
            term = term.strip()
            if term and term not in mapping:
                mapping[term] = pseudonym
    return dict(sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True))


def column_text_expression(column: str, data_type: str) -> str:
    """json/jsonb 需要先转文本才能做子串匹配。"""
    if data_type in ("jsonb", "json"):
        return f"{column}::text"
    return column


def probe(
    container: str,
    database: str,
    terms: list[str],
    tables: dict[str, int],
) -> dict[tuple[str, str], list[str]]:
    """返回 {(表, 列): [命中词, ...]}；用 strpos 避免 LIKE 通配符问题。"""
    hits: dict[tuple[str, str], list[str]] = {}
    values = ", ".join(f"({sql_literal(term)})" for term in terms)
    for table, column, data_type in text_columns(container, database):
        if tables.get(table, -1) == 0:
            continue
        expression = column_text_expression(column, data_type)
        conditions = " or ".join(
            f"strpos({expression}, {sql_literal(term)}) > 0" for term in terms
        )
        hit_count = psql(
            container, database, f"select count(*) from {table} where {conditions};\n"
        ).strip()
        if not hit_count or hit_count == "0":
            continue
        listing = psql(
            container,
            database,
            "select string_agg(v.term, chr(31) order by length(v.term) desc) from (values "
            + values
            + ") as v(term) where exists (select 1 from "
            + table
            + f" where strpos({expression}, v.term) > 0);\n",
        ).strip()
        matched = [term for term in listing.split(chr(31)) if term]
        hits[(table, column)] = matched or terms
    return hits


def build_replace_script(
    hits: dict[tuple[str, str], list[str]],
    mapping: dict[str, str],
    columns: list[tuple[str, str, str]],
) -> tuple[str, int]:
    data_types = {(table, column): data_type for table, column, data_type in columns}
    lines = [
        "SET LOCAL client_min_messages = warning;",
        "SET LOCAL statement_timeout = '600s';",
    ]
    count = 0
    for (table, column), matched in hits.items():
        data_type = data_types.get((table, column), "text")
        expression = column
        for term in sorted(matched, key=len, reverse=True):
            expression = (
                f"replace({expression}, {sql_literal(term)}, {sql_literal(mapping.get(term, 'REDACTED'))})"
            )
        if data_type in ("jsonb", "json"):
            expression = f"{expression}::{data_type}"
        lines.append(f"UPDATE {table} SET {column} = {expression} WHERE {column} IS NOT NULL;")
        count += 1
    return "\n".join(lines) + "\n", count


# --------------------------------------------------------------------------
# 容器生命周期
# --------------------------------------------------------------------------
def stop_app() -> bool:
    try:
        running = docker("inspect", TEST_APP_CONTAINER, "--format", "{{.State.Running}}").strip()
    except RuntimeError:
        return False
    if running != "true":
        return False
    log(f"停止测试容器 {TEST_APP_CONTAINER}")
    docker("stop", TEST_APP_CONTAINER, capture=False)
    return True


def start_app(was_running: bool) -> None:
    if was_running:
        log(f"重新启动测试容器 {TEST_APP_CONTAINER}")
        docker("start", TEST_APP_CONTAINER, capture=False)


def resolve_password_hash(container: str, password_file: Path, image: str | None) -> str:
    if not password_file.is_file():
        raise RuntimeError(f"找不到测试管理员密码文件: {password_file}")
    password = password_file.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError(f"{password_file} 为空")
    if not image:
        try:
            image = docker("inspect", TEST_APP_CONTAINER, "--format", "{{.Config.Image}}").strip()
        except RuntimeError:
            image = "invoice-tool-test:latest"
    snippet = (
        "import sys;from werkzeug.security import generate_password_hash as g;"
        "sys.stdout.write(g(sys.stdin.read().strip()))"
    )
    output = docker(
        "run", "--rm", "-i", "--entrypoint", "python", image, "-c", snippet,
        stdin=password.encode("utf-8"),
    ).strip()
    if not output.startswith(("pbkdf2:", "scrypt:")):
        raise RuntimeError("测试密码哈希生成失败（检查测试镜像是否可用）")
    return output


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def sanitize(args: argparse.Namespace) -> dict:
    container = args.container
    if container not in ALLOWED_CONTAINERS:
        raise ValueError(f"只允许操作容器: {', '.join(sorted(ALLOWED_CONTAINERS))}")
    assert_database_name(SOURCE_DATABASE, writable=False)
    if WRITABLE_DATABASE_PATTERN.fullmatch(SOURCE_DATABASE):
        raise ValueError("源库名不得落在可写测试库命名空间内")
    assert_database_name(TARGET_DATABASE, writable=True)
    assert_database_name(args.scratch_database, writable=True)
    if args.scratch_database == TARGET_DATABASE:
        raise ValueError("scratch 库名不能与目标库相同")

    state_dir = Path(args.state_dir)
    backup_dir = state_dir / "backups"
    scratch_dir = state_dir / "sanitize"
    raw_dump = (
        Path(args.source_dump)
        if args.source_dump
        else scratch_dir / f"source-{time.strftime('%Y%m%d-%H%M%S')}.dump"
    )

    if args.dry_run:
        return {
            "mode": "dry-run",
            "container": container,
            "source": SOURCE_DATABASE,
            "target": TARGET_DATABASE,
            "scratch": args.scratch_database,
            "purge_tables": len(PURGE_TABLES),
            "purge_settings_keys": len(PURGE_SETTING_KEYS),
            "rewrite_settings_keys": sorted(REWRITE_SETTINGS),
            "column_rules": len(build_column_rules()),
            "probe_query_groups": len(term_queries()),
            "raw_dump_path": str(raw_dump),
            "note": "dry-run 不连接任何数据库、不写任何文件",
        }

    backup_dir.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(scratch_dir, 0o700)

    assert_source_read_only(container)
    password_hash = resolve_password_hash(
        container, Path(args.admin_password_file), args.password_hash_image
    )

    starting_tables = public_tables(container, TARGET_DATABASE)
    log(f"现有 {TARGET_DATABASE} 共 {len(starting_tables)} 张表")

    was_running = stop_app()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        # 1) 备份现有测试库
        target_backup = backup_dir / f"invoice-test-before-sanitize-{stamp}.dump"
        log(f"备份现有 {TARGET_DATABASE} -> {target_backup}")
        with open(target_backup, "wb") as handle:
            proc = subprocess.run(
                ["docker", "exec", container, "pg_dump", "-U", "invoice_owner", "-d",
                 TARGET_DATABASE, "-Fc", "--no-owner", "--no-acl"],
                stdout=handle, check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError("备份现有测试库失败")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(target_backup, 0o600)

        # 2) 导出生产库（只读）
        if not Path(args.source_dump or raw_dump).exists():
            log(f"导出 {SOURCE_DATABASE} -> {raw_dump}")
            with open(raw_dump, "wb") as handle:
                proc = subprocess.run(
                    ["docker", "exec", container, "pg_dump", "-U", "invoice_owner", "-d",
                     SOURCE_DATABASE, "-Fc", "--no-owner", "--no-acl"],
                    stdout=handle, check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError("导出生产库失败")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(raw_dump, 0o600)
        else:
            log(f"复用已有导出 {raw_dump}")

        # 3) scratch 库：重建 + 恢复（DDL 不能进事务块）
        log(f"重建 scratch 库 {args.scratch_database}")
        psql_ddl(container, "postgres", f"DROP DATABASE IF EXISTS {args.scratch_database};")
        psql_ddl(container, "postgres", f"CREATE DATABASE {args.scratch_database} OWNER invoice_owner;")
        with open(raw_dump, "rb") as handle:
            result = subprocess.run(
                ["docker", "exec", "-i", container, "pg_restore", "-U", "invoice_owner",
                 "-d", args.scratch_database, "--no-owner", "--no-acl", "--exit-on-error"],
                stdin=handle, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(
                "恢复 scratch 库失败: "
                + result.stderr.decode("utf-8", "replace").strip()[-600:]
            )
        psql_script(container, args.scratch_database, "ANALYZE;")

        # 4) 脱敏
        log("执行脱敏规则")
        script, purged = build_sanitize_script(container, args.scratch_database, password_hash)
        psql_script(container, args.scratch_database, script)

        # 5) 自检：源值不得残留
        log("采集源库关键词（只读）")
        mapping = fetch_terms(container)
        terms = list(mapping)
        log(f"关键词 {len(terms)} 个，扫描全库文本列")
        tables_after = public_tables(container, args.scratch_database)
        columns = text_columns(container, args.scratch_database)
        hits = probe(container, args.scratch_database, terms, tables_after)
        replaced = 0
        if hits:
            log(f"探针命中 {len(hits)} 个列，按确定性映射原地替换")
            replace_script, replaced = build_replace_script(hits, mapping, columns)
            psql_script(container, args.scratch_database, replace_script)
            remaining = probe(container, args.scratch_database, terms, tables_after)
            if remaining:
                report = scratch_dir / f"probe-hits-{stamp}.txt"
                report.write_text(
                    "\n".join(f"{table}.{column} <- {', '.join(words)}"
                              for (table, column), words in remaining.items()),
                    encoding="utf-8",
                )
                raise RuntimeError(
                    f"仍有 {len(remaining)} 个列残留敏感值，详见 {report}；已中止，未切换数据库"
                )

        # 6) 原子换名（DDL 不能进事务块）
        log(f"切换 {args.scratch_database} -> {TARGET_DATABASE}")
        psql_ddl(container, "postgres", f"DROP DATABASE IF EXISTS {TARGET_DATABASE} WITH (FORCE);")
        psql_ddl(
            container,
            "postgres",
            f"ALTER DATABASE {args.scratch_database} RENAME TO {TARGET_DATABASE};",
        )
        psql_ddl(container, "postgres", f"ALTER DATABASE {TARGET_DATABASE} OWNER TO invoice_test;")
        psql_script(
            container,
            TARGET_DATABASE,
            "REASSIGN OWNED BY invoice_owner TO invoice_test;",
        )

        summary = {
            "source": SOURCE_DATABASE,
            "target": TARGET_DATABASE,
            "tables": len(tables_after),
            "purged_tables": len(purged),
            "target_backup": str(target_backup),
            "probe_terms": len(terms),
            "probe_columns_rewritten": replaced,
            "probe_residual": 0,
            "raw_source_dump": str(raw_dump) if args.keep_raw else None,
        }
        log("脱敏完成: " + json.dumps(summary, ensure_ascii=False))
        return summary
    finally:
        if not args.keep_raw and raw_dump.exists() and not args.source_dump:
            raw_dump.unlink()
            log("已删除源库原始导出")
        start_app(was_running)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--container", default="invoice-tool-postgres")
    parser.add_argument("--scratch-database", default=DEFAULT_SCRATCH_DATABASE)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--admin-password-file", default=DEFAULT_ADMIN_PASSWORD_FILE)
    parser.add_argument("--password-hash-image", default=None)
    parser.add_argument("--source-dump", default=None, help="复用已有的生产导出，跳过重新导出")
    parser.add_argument("--keep-raw", action="store_true", help="保留源库原始导出（含未脱敏数据）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不连接数据库")
    args = parser.parse_args(argv)
    try:
        result = sanitize(args)
    except Exception as error:  # noqa: BLE001 - 顶层统一失败处理
        log(f"失败: {error}")
        return 1
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
