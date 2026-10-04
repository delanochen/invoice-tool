import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parent / "scripts" / "upgrade_postgresql_0302.py"
SPEC = importlib.util.spec_from_file_location("upgrade_postgresql_0302", MODULE_PATH)
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


class UpgradePostgresql0302Test(unittest.TestCase):
    @staticmethod
    def fake_run(*, triggers=2, marker=True):
        def run(container, database, sql=None, input_text=None):
            value = triggers if "pg_trigger" in sql else int(marker)
            return SimpleNamespace(returncode=0, stdout=f"{value}\n")
        return run

    def test_ready_requires_both_triggers_and_marker(self):
        with patch.object(upgrade, "run", side_effect=self.fake_run()):
            self.assertTrue(upgrade.schema_ready("container", "invoice_test"))
        with patch.object(upgrade, "run", side_effect=self.fake_run(triggers=1)):
            self.assertFalse(upgrade.schema_ready("container", "invoice_test"))
        with patch.object(upgrade, "run", side_effect=self.fake_run(marker=False)):
            self.assertFalse(upgrade.schema_ready("container", "invoice_test"))


if __name__ == "__main__":
    unittest.main()
