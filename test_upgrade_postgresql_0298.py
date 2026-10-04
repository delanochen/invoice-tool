import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parent / "scripts" / "upgrade_postgresql_0298.py"
SPEC = importlib.util.spec_from_file_location("upgrade_postgresql_0298", MODULE_PATH)
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


class UpgradePostgresql0298Test(unittest.TestCase):
    @staticmethod
    def fake_run(*, enabled="0", trigger_count=None):
        trigger_count = len(upgrade.TRIGGERS) if trigger_count is None else trigger_count

        def run(container, database, sql=None, input_text=None):
            if "information_schema.tables" in sql:
                value = len(upgrade.TABLES)
            elif "information_schema.columns" in sql:
                value = 4
            elif "pg_trigger" in sql:
                value = trigger_count
            elif upgrade.MARKER in sql:
                value = 1
            elif "accounting_base_enabled" in sql:
                value = 1 if enabled in {"0", "1"} else 0
            else:
                raise AssertionError(sql)
            return SimpleNamespace(returncode=0, stdout=f"{value}\n")

        return run

    def test_ready_accepts_disabled_or_enabled_feature_flag(self):
        for enabled in ("0", "1"):
            with self.subTest(enabled=enabled), patch.object(
                upgrade, "run", side_effect=self.fake_run(enabled=enabled)
            ):
                self.assertTrue(upgrade.schema_ready("container", "invoice_test"))

    def test_ready_rejects_invalid_feature_flag(self):
        with patch.object(upgrade, "run", side_effect=self.fake_run(enabled="yes")):
            self.assertFalse(upgrade.schema_ready("container", "invoice_test"))

    def test_ready_rejects_missing_protection_trigger(self):
        with patch.object(
            upgrade, "run", side_effect=self.fake_run(trigger_count=len(upgrade.TRIGGERS) - 1)
        ):
            self.assertFalse(upgrade.schema_ready("container", "invoice_test"))


if __name__ == "__main__":
    unittest.main()
