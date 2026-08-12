import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class VolumeMigrationScriptTest(unittest.TestCase):
    def test_script_uses_safe_copy_verify_switch_sequence(self):
        source = (REPO_DIR / "scripts" / "migrate-to-volume2.sh").read_text(encoding="utf-8")
        self.assertIn('SOURCE_DIR="${INVOICE_TOOL_SOURCE_DIR:-/volume1/docker/invoice-tool}"', source)
        self.assertIn('TARGET_DIR="${INVOICE_TOOL_TARGET_DIR:-/volume2/docker/invoice-tool}"', source)
        self.assertLess(source.index('cp -a "$SOURCE_DIR/." "$STAGE_DIR/"'), source.index('compose "$SOURCE_DIR" down'))
        self.assertIn('if [ "$SOURCE_DB_SIZE" != "$TARGET_DB_SIZE" ]', source)
        self.assertIn('if [ "$NEW_DATA_MOUNT" != "$TARGET_DIR/data" ]', source)
        self.assertIn('INVOICE_TOOL_DIR=/volume2/docker/invoice-tool', source)
        self.assertIn('/volume2/docker/invoice-tool/scripts/auto-update.sh', source)

    def test_script_preserves_both_existing_folders(self):
        source = (REPO_DIR / "scripts" / "migrate-to-volume2.sh").read_text(encoding="utf-8")
        self.assertIn('invoice-tool-before-migration-$TIMESTAMP', source)
        self.assertIn('invoice-tool-migrated-$TIMESTAMP', source)
        self.assertNotIn('rm -rf "$SOURCE_DIR"', source)
        self.assertNotIn('rm -rf "$TARGET_DIR"', source)


if __name__ == "__main__":
    unittest.main()
