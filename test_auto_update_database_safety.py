import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class AutoUpdateDatabaseSafetyTest(unittest.TestCase):
    def setUp(self):
        self.script = (REPO_DIR / "scripts" / "auto-update.sh").read_text(encoding="utf-8")
        self.compose = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")

    def test_compose_uses_the_approved_data_directory_contract(self):
        self.assertIn('${DATA_HOST_DIR:-/volume1/invoice-tool/invoice-tool/data}:/app/data', self.compose)

    def test_container_requires_the_prepared_data_directory_identity_marker(self):
        self.assertIn('REQUIRE_DATA_DIRECTORY_IDENTITY: "1"', self.compose)
        self.assertIn('DATA_DIRECTORY_IDENTITY: "invoice-tool-primary-volume1-20260816"', self.compose)
        self.assertIn('DATA_IDENTITY_FILE=', self.script)
        self.assertIn('created target data directory identity marker', self.script)
        app_source = (REPO_DIR / "app.py").read_text(encoding="utf-8")
        self.assertIn('IS_RELEASE_BUILD', app_source)
        self.assertIn('"1" if IS_RELEASE_BUILD else "0"', app_source)

    def test_updater_rejects_compose_without_the_production_data_contract(self):
        self.assertIn("verify_compose_data_contract()", self.script)
        self.assertIn("compose file does not use the approved data directory contract", self.script)
        self.assertIn("compose file does not require the production data identity", self.script)

    def test_known_legacy_database_is_switched_only_after_business_validation(self):
        self.assertIn('validate_reviewed_legacy_switch()', self.script)
        self.assertIn('target database has fewer business records', self.script)
        self.assertIn('SO2608006_reports', self.script)
        self.assertIn('so2608006_reports != 4', self.script)

    def test_successful_legacy_switch_retires_the_old_database_and_env_override(self):
        self.assertIn('retire_legacy_database()', self.script)
        self.assertIn('invoices.db.retired-', self.script)
        self.assertIn("sed '/^DATA_HOST_DIR=/d'", self.script)

    def test_deploy_backs_up_the_real_running_database(self):
        self.assertIn("container_data_dir()", self.script)
        self.assertIn("backup_database running", self.script)
        self.assertIn("source.backup(target)", self.script)
        self.assertIn('source.execute("PRAGMA integrity_check")', self.script)
        self.assertIn('target.execute("PRAGMA integrity_check")', self.script)

    def test_unreviewed_data_path_switch_is_rejected(self):
        self.assertNotIn('INVOICE_TOOL_ALLOW_DATA_SWITCH', self.script)
        self.assertIn("refusing database path switch", self.script)

    def test_deploy_exports_and_rechecks_the_absolute_mount(self):
        self.assertIn('export DATA_HOST_DIR="$EXPECTED_DATA_DIR"', self.script)
        self.assertIn("verify_database_mount()", self.script)
        self.assertIn("container data mount mismatch after deploy", self.script)
        self.assertIn("SELECT COUNT(*) FROM service_reports", self.script)
        self.assertIn("SELECT COUNT(*) FROM expenses", self.script)


if __name__ == "__main__":
    unittest.main()
