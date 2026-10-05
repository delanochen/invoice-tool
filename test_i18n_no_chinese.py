"""Non-Chinese UI builds must not leave user-visible Han characters behind."""
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class UiI18nCompletenessTest(unittest.TestCase):
    def run_audit(self, script):
        result = subprocess.run(
            ["node", str(ROOT / "scripts" / script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_main_ui_has_no_residual_chinese(self):
        self.run_audit("audit_ui_i18n.js")

    def test_field_work_ui_has_no_residual_chinese(self):
        self.run_audit("audit_field_i18n.js")


if __name__ == "__main__":
    unittest.main()
