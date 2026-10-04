import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parent / "scripts" / "upgrade_postgresql_0304.py"
SPEC = importlib.util.spec_from_file_location("upgrade_postgresql_0304", MODULE_PATH)
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


class UpgradePostgresql0304Test(unittest.TestCase):
    @staticmethod
    def fake_run(*, columns=True, marker=True):
        def run(container, database, sql=None, input_text=None):
            value = 5 if columns and "information_schema.columns" in sql else int(marker)
            if "information_schema.columns" in sql and not columns:
                value = 4
            return SimpleNamespace(returncode=0, stdout=f"{value}\n")
        return run

    def test_ready_requires_columns_and_marker(self):
        with patch.object(upgrade, "run", side_effect=self.fake_run()):
            self.assertTrue(upgrade.schema_ready("container", "invoice_test"))
        with patch.object(upgrade, "run", side_effect=self.fake_run(columns=False)):
            self.assertFalse(upgrade.schema_ready("container", "invoice_test"))
        with patch.object(upgrade, "run", side_effect=self.fake_run(marker=False)):
            self.assertFalse(upgrade.schema_ready("container", "invoice_test"))


if __name__ == "__main__":
    unittest.main()
