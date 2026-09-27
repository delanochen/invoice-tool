"""PostgreSQL-only test infrastructure.

所有数据库测试统一连服务器 PG 上的专用测试库 invoice_test：

- 角色 invoice_test 是该库 owner，有完整 DDL 权限；
- 该角色对 invoice_production / invoice_production_restore_validation 无
  CONNECT 权（生产库已 REVOKE PUBLIC CONNECT，实测连接被 PG 拒绝）——
  即使连接串配错，PG 也会在协议层拒绝，测试碰不到生产数据；
- PG 5432 不映射宿主机，本地访问必须走 SSH 隧道（本模块自动建立）；
- schema 快照在 tests/schema_postgresql.sql（仅从已应用迁移的 invoice_test
  执行 pg_dump --schema-only，再附加 invoice_schema_version 标记行）。迁移变化后：
      ssh -i ~/.ssh/invoice_test_agent root@192.168.7.202 \
        "docker exec invoice-tool-postgres pg_dump -U invoice_test \
         -d invoice_test --schema-only --no-owner --no-privileges" \
        > tests/schema_postgresql.sql
  然后追加 invoice_schema_version 的 INSERT（文件尾部有样例）。禁止从生产库
  生成或验证测试 fixture。

用法（app 级测试）：
    from tests_pg import tests_pg
    tests_pg.activate()          # 设置 DATABASE_URL（幂等）
    import app as app_module
    ...
    with tests_pg.connection() as conn:   # 裸 psycopg 连接（可执行 DDL）
        ...

隔离模型：pytest 运行时由 conftest.py 在每个 TestCase 类开始前调用
tests_pg.truncate_all()（TRUNCATE RESTART IDENTITY CASCADE）。直接以
unittest.main() 运行的单文件请自行调用 tests_pg.reset_for_class()。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent  # tests_pg.py 位于仓库根

# invoice_schema_version.source_sha256 是 NOT NULL；测试库不做来源校验，
# 使用固定测试值以保持行形状一致，不关联任何生产快照。
SCHEMA_MARKER_SHA256 = "7e7feb3901d075f3fb7a5a90cb73d6a133c1dd3e7915e0f90aa1790ba70ad283"

TEST_DB_NAME = "invoice_test"
LOCAL_TUNNEL_PORT = 15432
# 服务器 PG 容器内网地址。容器重建会换 IP，activate() 会动态解析。
TUNNEL_SSH_HOST = "root@192.168.7.202"
TUNNEL_SSH_KEY = "~/.ssh/invoice_test_agent"
TUNNEL_CONTAINER = "invoice-tool-postgres"

#: 密码文件（勿提交到仓库）。服务器端副本 /root/invoice_test_credentials.txt。
PASSWORD_FILE = Path.home() / ".workbuddy" / "invoice_test_pg.password"

_tunnel_proc = None


class TestDatabaseConfigError(RuntimeError):
    """测试库配置错误（含误指生产的硬性拒绝）。"""


def _read_password():
    if os.environ.get("INVOICE_TEST_DATABASE_URL"):
        return None
    if not PASSWORD_FILE.exists():
        raise TestDatabaseConfigError(
            f"找不到测试库密码文件 {PASSWORD_FILE}。"
            "可改用环境变量 INVOICE_TEST_DATABASE_URL 直接连接串。"
        )
    return PASSWORD_FILE.read_text(encoding="utf-8").strip()


def _resolve_container_ip():
    cmd = (
        "ssh", "-i", os.path.expanduser(TUNNEL_SSH_KEY),
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        TUNNEL_SSH_HOST,
        "docker", "exec", TUNNEL_CONTAINER, "hostname", "-i",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    # hostname -i 输出形如 "172.22.0.2"，取第一个地址
    ip = (result.stdout.split() or [""])[0]
    if not ip or result.returncode != 0:
        raise TestDatabaseConfigError(
            f"无法解析 {TUNNEL_CONTAINER} 的容器 IP："
            f"rc={result.returncode} stderr={result.stderr.strip()[:300]}"
        )
    return ip


def _port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_tunnel():
    """确保本地 15432 → 服务器 PG 容器 5432 的 SSH 隧道可用。"""
    global _tunnel_proc
    if _port_open("127.0.0.1", LOCAL_TUNNEL_PORT):
        return
    container_ip = _resolve_container_ip()
    _tunnel_proc = subprocess.Popen(
        [
            "ssh", "-i", os.path.expanduser(TUNNEL_SSH_KEY),
            "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-N", "-L", f"{LOCAL_TUNNEL_PORT}:{container_ip}:5432",
            TUNNEL_SSH_HOST,
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        if _port_open("127.0.0.1", LOCAL_TUNNEL_PORT):
            return
        if _tunnel_proc.poll() is not None:
            raise TestDatabaseConfigError("SSH 隧道进程意外退出")
        time.sleep(0.3)
    raise TestDatabaseConfigError("SSH 隧道建立超时")


def database_url():
    """返回指向 invoice_test 的连接串，带硬性防生产守卫。"""
    override = os.environ.get("INVOICE_TEST_DATABASE_URL")
    if override:
        url = override
    else:
        password = _read_password()
        _ensure_tunnel()
        url = (
            f"postgresql://invoice_test:{password}"
            f"@127.0.0.1:{LOCAL_TUNNEL_PORT}/{TEST_DB_NAME}"
        )
    _guard_test_url(url)
    return url


def _guard_test_url(url):
    """硬性守卫：测试只允许连 invoice_test，绝不许指向生产库。"""
    marker = url.split("?", 1)[0].rstrip("/").rpartition("/")[2]
    if marker != TEST_DB_NAME:
        raise TestDatabaseConfigError(
            f"测试连接串必须指向 {TEST_DB_NAME}，实际是 {marker!r}。"
            "测试绝不操作生产数据库。"
        )


def activate():
    """设置 DATABASE_URL 指向测试库（幂等，须在 import app 之前调用）。"""
    os.environ["DATABASE_URL"] = database_url()
    os.environ.setdefault("REQUIRE_DATA_DIRECTORY_IDENTITY", "0")
    return os.environ["DATABASE_URL"]


def connection():
    """裸 psycopg 连接（测试可执行 DDL；app 的 db() 走的是另一条路）。"""
    import psycopg

    conn = psycopg.connect(database_url(), autocommit=True)
    return conn


def table_names(conn):
    rows = conn.execute(
        "select table_name from information_schema.tables where table_schema='public'"
    ).fetchall()
    return [r[0] for r in rows]


_seed_sql_cache = None
_seed_tables_cache = None


def _seed_sql():
    """全新 PostgreSQL 测试库的业务种子基线。"""
    global _seed_sql_cache, _seed_tables_cache
    if _seed_sql_cache is None:
        text = (PROJECT_ROOT / "tests" / "seed_reference.sql").read_text(
            encoding="utf-8"
        )
        _seed_sql_cache = text
        # 文件里每张表前有 "-- table: N rows" 注释，顺手取出表名集合，
        # truncate_all 只需对这些表校准序列。
        _seed_tables_cache = set()
        for line in text.splitlines():
            if line.startswith("-- ") and ": " in line and " rows" in line:
                _seed_tables_cache.add(line[3:].split(":")[0].strip())
    return _seed_sql_cache


def _business_tables(conn):
    skip = {
        "invoice_sqlite_columns",
        "invoice_sqlite_master",
        "invoice_schema_version",
    }
    return [n for n in table_names(conn) if n not in skip]


def _truncate_business(conn, tables):
    if tables:
        joined = ",".join(f'"{n}"' for n in sorted(tables))
        conn.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")


def _fix_sequences(conn, tables):
    idents = conn.execute(
        "select table_name, column_name from information_schema.columns "
        "where table_schema='public' and is_identity='YES'"
    ).fetchall()
    for table, column in idents:
        if table not in tables:
            continue
        row = conn.execute(f'select coalesce(max("{column}"), 0) from "{table}"').fetchone()
        seq = conn.execute(
            "select pg_get_serial_sequence(%s, %s)", (table, column)
        ).fetchone()[0]
        if not seq:
            continue
        if row[0]:
            conn.execute("select setval(%s, %s)", (seq, row[0]))
        else:
            conn.execute("select setval(%s, 1, false)", (seq,))


def _topological_order(conn, tables):
    """按外键依赖排序（被引用的表先插入）。"""
    edges = conn.execute(
        "select conrelid::regclass::text, confrelid::regclass::text "
        "from pg_constraint where contype='f' "
        "and connamespace='public'::regnamespace"
    ).fetchall()
    inside = set(tables)
    dependents = {t: set() for t in inside}   # table -> tables referencing it
    references = {t: set() for t in inside}   # table -> tables it references
    for child, parent in edges:
        child = child.split(".")[-1].strip('"')
        parent = parent.split(".")[-1].strip('"')
        if child in inside and parent in inside and child != parent:
            dependents[parent].add(child)
            references[child].add(parent)
    order, ready = [], [t for t in sorted(inside) if not references[t]]
    seen = set(ready)
    while ready:
        t = ready.pop(0)
        order.append(t)
        for d in sorted(dependents[t]):
            references[d].discard(t)
            if not references[d] and d not in seen:
                seen.add(d)
                ready.append(d)
    # 环兜底：把没排进去的按名字序追加（自引用/循环引用时靠 TRUNCATE 后空表可插）
    order.extend(sorted(inside - set(order)))
    return order


def truncate_all(conn=None):
    """把测试库恢复到全新的 PostgreSQL 业务种子状态。

    1. TRUNCATE 全部业务表（保留结构元数据表）；
    2. 整段执行种子基线（tests/seed_reference.sql，一次往返）；
    3. 校准 identity 序列（种子用显式 id 插入，不会推进序列）。
    """
    owned = False
    if conn is None:
        conn = connection()
        owned = True
    try:
        tables = _business_tables(conn)
        _truncate_business(conn, tables)
        conn.execute(_seed_sql())
        # 只有种子表有显式 id 插入；其余表 TRUNCATE RESTART IDENTITY 已复位
        _fix_sequences(conn, _seed_tables_cache)
    finally:
        if owned:
            conn.close()


def snapshot_all(conn=None):
    """快照全部业务表的行（类级：setUpClass 播种之后调用）。

    返回 {table: (columns, rows)}，行内是 psycopg 原生 Python 类型。
    先用一条 union 查各表行数，只对非空表 select *，控制往返数。
    """
    owned = False
    if conn is None:
        conn = connection()
        owned = True
    try:
        tables = _business_tables(conn)
        if not tables:
            return {}
        union = " union all ".join(
            f'select %s as t, count(*)::bigint as n from "{t}"' for t in tables
        )
        counts = conn.execute(union, tuple(tables)).fetchall()
        snapshot = {}
        for t, n in counts:
            if not n:
                continue
            cur = conn.execute(f'select * from "{t}"')
            cols = [d.name for d in cur.description]
            snapshot[t] = (cols, cur.fetchall())
        return snapshot
    finally:
        if owned:
            conn.close()


_baseline = None       # 种子基线快照 {table: (cols, rows)}
_baseline_rows = None  # 种子基线行集合 {table: set(row)}
_baseline_seq = None   # 种子基线的序列修正 [(seq, value, advanced)]


def _ensure_baseline():
    """会话内一次性：建立种子基线快照与序列修正值。"""
    global _baseline, _baseline_rows, _baseline_seq
    if _baseline is not None:
        return
    truncate_all()
    _baseline = snapshot_all()
    _baseline_rows = {t: set(rows) for t, (c, rows) in _baseline.items()}
    conn = connection()
    try:
        idents = conn.execute(
            "select table_name, column_name, "
            "pg_get_serial_sequence(table_name, column_name) "
            "from information_schema.columns "
            "where table_schema='public' and is_identity='YES'"
        ).fetchall()
        fixups = []
        for table, column, seq in idents:
            if not seq or table not in _baseline:
                continue
            value = conn.execute(
                f'select coalesce(max("{column}"), 0) from "{table}"'
            ).fetchone()[0]
            fixups.append((seq, value if value else 1, bool(value)))
        _baseline_seq = fixups
    finally:
        conn.close()


def _apply_baseline_sequences(conn):
    if not _baseline_seq:
        return
    parts, params = [], []
    for seq, value, advanced in _baseline_seq:
        if advanced:
            parts.append("select setval(%s, %s)")
            params.extend([seq, value])
        else:
            parts.append("select setval(%s, 1, false)")
            params.append(seq)
    conn.execute(" union all ".join(parts), tuple(params))


def restore_for_test(cls_snapshot=None):
    """把库恢复到某个测试的起点（每个测试前调用）。

    - 无类快照：恢复到 PostgreSQL 种子基线；
    - 有类快照：种子基线 + 该类 setUpClass 的增量种子（基线之外的新行），
      类内每个测试看到同一份类种子，且测试之间互不污染。
    """
    _ensure_baseline()
    conn = connection()
    try:
        tables = _business_tables(conn)
        _truncate_business(conn, tables)
        conn.execute(_seed_sql())
        _apply_baseline_sequences(conn)
        if cls_snapshot:
            delta = {}
            for table, (cols, rows) in cls_snapshot.items():
                known = _baseline_rows.get(table)
                fresh = rows if known is None else [
                    r for r in rows if tuple(r) not in known
                ]
                if fresh:
                    delta[table] = (cols, fresh)
            if delta:
                for table in _topological_order(conn, set(delta)):
                    if table not in delta:
                        continue
                    cols, rows = delta[table]
                    collist = ",".join(f'"{c}"' for c in cols)
                    placeholders = ",".join(["%s"] * len(cols))
                    with conn.cursor() as cur:
                        cur.executemany(
                            f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders})',
                            rows,
                        )
                _fix_sequences(conn, set(delta))
    finally:
        conn.close()


def ensure_schema():
    """确保测试库有与生产一致的 schema。幂等。

    - invoice_schema_version 有标记行 → schema 就绪，直接返回；
    - 表存在但缺标记行 → schema 已导入过（schema-only dump 不带数据），
      只补标记行；
    - 全新空库 → 灌入 tests/schema_postgresql.sql 快照。
    """
    from database import SCHEMA_VERSION

    conn = connection()
    try:
        if "invoice_schema_version" in table_names(conn):
            row = conn.execute(
                "select version from invoice_schema_version where singleton=1"
            ).fetchone()
            if row and row[0]:
                return  # schema 已就绪
            conn.execute(
                "INSERT INTO invoice_schema_version "
                "(singleton, version, source_sha256) VALUES (1, %s, %s) "
                "ON CONFLICT (singleton) "
                "DO UPDATE SET version = EXCLUDED.version",
                (SCHEMA_VERSION, SCHEMA_MARKER_SHA256),
            )
            return
    finally:
        conn.close()
    # 全新库：逐语句执行快照。pg_dump 输出里的 \restrict 等 psql 元命令
    # psycopg 不认识，先剥掉。
    raw_lines = (PROJECT_ROOT / "tests" / "schema_postgresql.sql").read_text(
        encoding="utf-8"
    ).splitlines()
    sql_text = "\n".join(
        line for line in raw_lines if not line.lstrip().startswith("\\")
    )
    conn = connection()
    try:
        conn.execute(sql_text)
    finally:
        conn.close()


def reset_for_class():
    """unittest.main() 直跑模式：每个 TestCase 类开始前调用一次。"""
    activate()
    truncate_all()
