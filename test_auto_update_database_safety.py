import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class AutoUpdateDatabaseSafetyTest(unittest.TestCase):
    def setUp(self):
        self.script = (REPO_DIR / "scripts" / "auto-update.sh").read_text(encoding="utf-8")
        self.compose = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")

    def test_compose_uses_an_explicitly_overridable_data_directory(self):
        self.assertIn('${DATA_HOST_DIR:-./data}:/app/data', self.compose)

    def test_deploy_backs_up_the_real_running_database(self):
        self.assertIn("container_data_dir()", self.script)
        self.assertIn("backup_database running", self.script)
        self.assertIn("source.backup(target)", self.script)
        self.assertIn('source.execute("PRAGMA integrity_check")', self.script)
        self.assertIn('target.execute("PRAGMA integrity_check")', self.script)

    def test_unreviewed_data_path_switch_is_rejected(self):
        self.assertIn('ALLOW_DATA_SWITCH="${INVOICE_TOOL_ALLOW_DATA_SWITCH:-0}"', self.script)
        self.assertIn('if [ "$ALLOW_DATA_SWITCH" != "1" ]', self.script)
        self.assertIn("refusing database path switch", self.script)

    def test_deploy_exports_and_rechecks_the_absolute_mount(self):
        self.assertIn('export DATA_HOST_DIR="$EXPECTED_DATA_DIR"', self.script)
        self.assertIn("verify_database_mount()", self.script)
        self.assertIn("container data mount mismatch after deploy", self.script)
        self.assertIn("SELECT COUNT(*) FROM service_reports", self.script)
        self.assertIn("SELECT COUNT(*) FROM expenses", self.script)


if __name__ == "__main__":
    unittest.main()
