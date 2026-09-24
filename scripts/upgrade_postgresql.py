"""Apply the reviewed additive PostgreSQL schema upgrade for v0.1.274.

Why this exists: production PostgreSQL was cut over from a frozen 0252-compat
snapshot. v0.1.274 added report schema objects for SQLite inside app.init_db()
(service_report_workers.origin_address / trip_type, service_report_mileage_evidence)
but PostgreSQL mode never runs init_db DDL, so every page loading existing
report workers (编辑 / 复制日报, 保存, 里程佐证) failed with a missing-column 500.
This runner closes the gap without touching the '0252-compat-v2' marker: the
upgrade is additive-only, so the running verifier stays valid before AND after.

Fail-closed contract (mirrors the cutover tooling):
  * Refuses to run unless the live schema version marker is exactly the
    reviewed '0252-compat-v2' baseline.
  * Classifies the live schema (baseline / upgraded / unexpected) from actual
    catalog state, never from assumptions; unexpected states abort.
  * Applies migrations/postgresql/0274-report-workers-trip.sql as the
    migration (owner) role in ONE psql single-transaction with ON_ERROR_STOP;
    any failure leaves the database at the verified baseline.
  * Re-verifies the upgraded shape afterwards.

Like scripts/backup_postgresql.py this script never imports the app and never
connects directly: all SQL runs through `docker exec ... psql` inside the
postgres container, so it works from the deploy host without psycopg.

Usage (deploy host):
    python3 scripts/upgrade_postgresql.py --database invoice_tool_production
Idempotent: an already-upgraded database exits 0 without executing DDL.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "postgresql" / "0274-report-workers-trip.sql"
POSTGRES_CONTAINER = "invoice-tool-postgres"
MIGRATION_ROLE = "invoice_owner"
EXPECTED_BASELINE_VERSION = "0252-compat-v2"

# Column shape of the reviewed 0252 baseline (migrations/postgresql/schema-0252.json).
BASELINE_WORKER_COLUMNS = [
    "id", "report_id", "user_id", "driving_miles", "travel_mode", "travel_hours",
    "public_transport_hours", "work_description",
]
UPGRADE_WORKER_COLUMNS = BASELINE_WORKER_COLUMNS + ["origin_address", "trip_type"]
EVIDENCE_COLUMNS = [
    "id", "report_id", "worker_user_id", "route_fingerprint", "origin_address",
    "destination_address", "trip_type", "distance_meters", "duration_seconds",
    "one_way_miles", "reported_miles", "attachment_id", "status", "generated_by",
    "generated_at",
]


def log(message):
    print(message, file=sys.stderr, flush=True)


def psql(container, database, sql=None, stdin_file=None, single_transaction=False):
    """Run psql inside the postgres container; return (returncode, stdout)."""
    command = [
        "docker", "exec",
        *(["-i"] if stdin_file is not None else []),
        container, "psql", "-U", MIGRATION_ROLE, "-d", database,
        "-v", "ON_ERROR_STOP=1", "-At",
    ]
    if single_transaction:
        command.append("--single-transaction")
    if sql is not None:
        command += ["-c", sql]
    result = subprocess.run(
        command,
        stdin=stdin_file,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode and result.stderr:
        # Driver errors can contain endpoint details; surface them only as a
        # short reason, never the full connection string.
        first_line = result.stderr.strip().splitlines()[-1]
        log("psql failed: " + first_line[:200])
    return result.returncode, result.stdout.strip()


def read_schema_state(container, database):
    """Return {'version': str, 'workers': [...], 'evidence': [...]} or raise."""
    code, version = psql(
        container, database,
        "select coalesce((select version from invoice_schema_version where singleton=1), '')",
    )
    if code:
        raise RuntimeError("无法读取 invoice_schema_version（数据库或角色不可用）。")
    code, rows = psql(
        container, database,
        "select table_name, column_name from information_schema.columns "
        "where table_schema='public' and table_name in "
        "('service_report_workers', 'service_report_mileage_evidence') "
        "order by table_name, ordinal_position",
    )
    if code:
        raise RuntimeError("无法读取 information_schema.columns。")
    workers, evidence = [], []
    for line in filter(None, rows.splitlines()):
        table, column = line.split("|", 1)
        (evidence if table == "service_report_mileage_evidence" else workers).append(column)
    return {"version": version, "workers": workers, "evidence": evidence}


def classify(state):
    """Map live catalog state to 'baseline' / 'upgraded' / None (unexpected)."""
    if state["workers"] == BASELINE_WORKER_COLUMNS and not state["evidence"]:
        return "baseline"
    if state["workers"] == UPGRADE_WORKER_COLUMNS and state["evidence"] == EVIDENCE_COLUMNS:
        return "upgraded"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True, help="PostgreSQL database name")
    parser.add_argument(
        "--container", default=POSTGRES_CONTAINER,
        help="postgres container name (default: invoice-tool-postgres)",
    )
    args = parser.parse_args()

    if not MIGRATION.is_file():
        log("error: 迁移文件缺失: " + str(MIGRATION))
        return 1

    try:
        state = read_schema_state(args.container, args.database)
    except RuntimeError as error:
        log("error: " + str(error))
        return 1

    if state["version"] != EXPECTED_BASELINE_VERSION:
        log("error: schema 版本标记不是 %s（实际 %r）；拒绝在未知状态下执行增量升级。"
            % (EXPECTED_BASELINE_VERSION, state["version"]))
        return 1

    shape = classify(state)
    if shape == "upgraded":
        print(json.dumps({"status": "already_upgraded", "schema": state["version"]}))
        log("schema 已包含 v0.1.274 增量对象，无需变更。")
        return 0
    if shape != "baseline":
        log("error: 实际 schema 与审查过的 0252 基线/升级形态均不符，拒绝执行。\n"
            + json.dumps(state, ensure_ascii=False, indent=2))
        return 1

    log("应用增量升级 %s ..." % MIGRATION.name)
    with MIGRATION.open("rb") as handle:
        code, _ = psql(
            args.container, args.database,
            stdin_file=handle, single_transaction=True,
        )
    if code:
        log("error: 增量升级失败，事务已回滚，数据库保持 0252 基线。")
        return 1

    try:
        verified = read_schema_state(args.container, args.database)
    except RuntimeError as error:
        log("error: 升级后复核失败: " + str(error))
        return 1
    if classify(verified) != "upgraded":
        log("error: 升级后 schema 形态不符合预期，拒绝报告成功。\n"
            + json.dumps(verified, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"status": "upgraded", "schema": verified["version"]}))
    log("v0.1.274 增量对象已就绪：service_report_workers 出发地/行程类型 + 里程佐证表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
