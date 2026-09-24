"""v0.1.275 hotfix regression tests: PostgreSQL additive schema upgrade.

Production PostgreSQL stayed on the frozen 0252-compat baseline while v0.1.274
added report schema objects for SQLite inside app.init_db()
(service_report_workers.origin_address / trip_type, service_report_mileage_evidence).
PostgreSQL mode never runs init_db DDL, so every page loading existing report
workers (编辑 / 复制日报, 保存, 里程佐证) failed with a missing-column 500.

Covered here:
1. scripts/upgrade_postgresql.py state machine: baseline -> apply, upgraded ->
   no-op, everything else refuses; version-marker gate; single-transaction
   apply through docker exec psql (stubbed, never real Docker).
2. migrations/postgresql/0274-report-workers-trip.sql contract: additive-only,
   idempotent statements, bookkeeping rows matching the reviewed manifest.
3. debian-auto-deploy.sh wiring: the upgrade runs on every deploy attempt
   after the backup and before the "already current" shortcut, and a failed
   upgrade aborts the deploy.
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).parent
MIGRATION = PROJECT_ROOT / "migrations/postgresql/0274-report-workers-trip.sql"
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts/debian-auto-deploy.sh"

_spec = importlib.util.spec_from_file_location(
    "upgrade_postgresql", PROJECT_ROOT / "scripts/upgrade_postgresql.py"
)
upgrade_postgresql = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(upgrade_postgresql)

BASELINE_STATE = {
    "version": "0252-compat-v2",
    "workers": [
        "id", "report_id", "user_id", "driving_miles", "travel_mode",
        "travel_hours", "public_transport_hours", "work_description",
    ],
    "evidence": [],
}
UPGRADED_STATE = {
    "version": "0252-compat-v2",
    "workers": BASELINE_STATE["workers"] + ["origin_address", "trip_type"],
    "evidence": upgrade_postgresql.EVIDENCE_COLUMNS,
}


class ClassifyStateTests(unittest.TestCase):
    def test_baseline_state_requires_upgrade(self):
        self.assertEqual(upgrade_postgresql.classify(BASELINE_STATE), "baseline")

    def test_upgraded_state_is_noop(self):
        self.assertEqual(upgrade_postgresql.classify(UPGRADED_STATE), "upgraded")

    def test_unexpected_states_refuse(self):
        missing_table = dict(BASELINE_STATE, workers=BASELINE_STATE["workers"][:5])
        extra_column = dict(
            UPGRADED_STATE, workers=UPGRADED_STATE["workers"] + ["surprise"]
        )
        self.assertIsNone(upgrade_postgresql.classify(missing_table))
        self.assertIsNone(upgrade_postgresql.classify(extra_column))
        self.assertIsNone(upgrade_postgresql.classify(dict(UPGRADED_STATE, evidence=[])))


class UpgradeRunnerFlowTests(unittest.TestCase):
    """Drive main() with a stubbed subprocess.run emulating docker exec psql."""

    def run_runner(self, state_sequence, stdin_capture):
        captured = {"state_calls": 0}

        def fake_run(command, stdin=None, capture_output=True, text=True, encoding=None):
            joined = " ".join(command)
            if "-i" in command:  # single-transaction apply, schema fed on stdin
                stdin_capture["sql"] = stdin.read().decode("utf-8")
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "invoice_schema_version" in joined:
                return mock.Mock(
                    returncode=0, stdout=state_sequence[0]["version"] + "\n", stderr=""
                )
            captured["state_calls"] += 1
            state = state_sequence[min(captured["state_calls"] - 1, len(state_sequence) - 1)]
            rows = [
                f"service_report_workers|{name}" for name in state["workers"]
            ] + [f"service_report_mileage_evidence|{name}" for name in state["evidence"]]
            return mock.Mock(
                returncode=0, stdout="\n".join(rows) + ("\n" if rows else ""), stderr=""
            )

        with mock.patch.object(upgrade_postgresql.subprocess, "run", side_effect=fake_run):
            with mock.patch.object(
                sys, "argv", ["upgrade_postgresql.py", "--database", "invoice"]
            ):
                return upgrade_postgresql.main()

    def test_baseline_applies_migration_and_verifies(self):
        stdin_capture = {}
        exit_code = self.run_runner([BASELINE_STATE, UPGRADED_STATE], stdin_capture)
        self.assertEqual(exit_code, 0)
        sql = stdin_capture["sql"]
        self.assertIn("ALTER TABLE service_report_workers", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS service_report_mileage_evidence", sql)

    def test_already_upgraded_is_noop(self):
        stdin_capture = {}
        exit_code = self.run_runner([UPGRADED_STATE], stdin_capture)
        self.assertEqual(exit_code, 0)
        self.assertNotIn("sql", stdin_capture)

    def test_version_marker_gate(self):
        stdin_capture = {}
        exit_code = self.run_runner(
            [dict(BASELINE_STATE, version="unknown-marker")], stdin_capture
        )
        self.assertEqual(exit_code, 1)
        self.assertNotIn("sql", stdin_capture)

    def test_unexpected_shape_refuses(self):
        stdin_capture = {}
        exit_code = self.run_runner(
            [dict(BASELINE_STATE, workers=BASELINE_STATE["workers"][:5])], stdin_capture
        )
        self.assertEqual(exit_code, 1)
        self.assertNotIn("sql", stdin_capture)


class MigrationSqlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_additive_columns_are_idempotent(self):
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS origin_address text NOT NULL DEFAULT ''",
            self.sql,
        )
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS trip_type text NOT NULL DEFAULT 'round_trip'",
            self.sql,
        )

    def test_evidence_table_matches_app_definition(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS service_report_mileage_evidence", self.sql)
        self.assertIn("UNIQUE (report_id, worker_user_id)", self.sql)
        self.assertIn(
            "REFERENCES service_reports(id) ON DELETE CASCADE", self.sql
        )
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS idx_sr_mileage_evidence_report", self.sql
        )

    def test_no_destructive_statements(self):
        # Strip comment lines before the keyword sweep so documentation inside
        # the file (e.g. "ON UPDATE NO ACTION") cannot trip the check.
        code_only = "\n".join(
            line for line in self.sql.splitlines() if not line.lstrip().startswith("--")
        ).lower()
        for keyword in ("drop ", "truncate ", "delete from", "alter type", "update set"):
            self.assertNotIn(keyword, code_only)

    def test_bookkeeping_rows_present_and_idempotent(self):
        self.assertIn("ON CONFLICT (table_name, cid) DO NOTHING", self.sql)
        self.assertIn("('service_report_workers', 8, 'origin_address'", self.sql)
        self.assertIn("('service_report_workers', 9, 'trip_type'", self.sql)
        for cid in range(15):
            self.assertIn(f"('service_report_mileage_evidence', {cid},", self.sql)


class DeployWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    def test_upgrade_runs_after_backup_and_before_fetch(self):
        backup_at = self.script.index('backup_database "$STAMP" || exit 1')
        upgrade_at = self.script.index("upgrade_postgresql_schema || exit 1")
        fetch_at = self.script.index("git fetch --quiet origin main")
        self.assertLess(backup_at, upgrade_at)
        self.assertLess(upgrade_at, fetch_at)

    def test_upgrade_only_for_postgresql_backend(self):
        self.assertIn('if [ "$backend" = "postgresql" ]; then\n  upgrade_postgresql_schema', self.script)

    def test_upgrade_failure_aborts_deploy(self):
        self.assertIn("upgrade_postgresql_schema || exit 1", self.script)


if __name__ == "__main__":
    unittest.main()
