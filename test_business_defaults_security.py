import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
CONFIG_KEYS = (
    "COMPANY_NAME",
    "COMPANY_ADDRESS",
    "COMPANY_EMAIL",
    "COMPANY_PHONE",
    "COMPANY_REGISTRATION_NUMBER",
    "COMPANY_EIN",
    "COMPANY_TAX_NOTE",
    "PAYMENT_METHOD",
    "PAYMENT_BENEFICIARY",
    "PAYMENT_BANK_NAME",
    "PAYMENT_ACCOUNT_NUMBER",
    "PAYMENT_ROUTING_NUMBER",
    "PAYMENT_SWIFT_BIC",
    "INVOICE_TERMS",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_TLS",
    "HEADQUARTERS_LATITUDE",
    "HEADQUARTERS_LONGITUDE",
)


class BusinessDefaultsSecurityTest(unittest.TestCase):
    def load_app(self, values=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location(
            f"business_defaults_security_{len(self._cleanups)}",
            source,
        )
        module = importlib.util.module_from_spec(spec)
        environment = {
            "ADMIN_EMAIL": "business-defaults-admin@example.test",
            "ADMIN_PASSWORD": "business-defaults-test-password",
            "APP_VERSION": "0.0.0",
            "REQUIRE_DATA_DIRECTORY_IDENTITY": "0",
        }
        environment.update({key: "" for key in CONFIG_KEYS})
        environment.update(values or {})
        with patch.dict(os.environ, environment, clear=False):
            spec.loader.exec_module(module)
        module.app.config.update(TESTING=True, SECRET_KEY="test")
        return module

    def test_empty_database_does_not_receive_real_business_defaults(self):
        module = self.load_app()
        with module.app.app_context():
            company = module.get_company_profile()
            payment = module.get_payment_instructions()
            smtp = module.get_smtp_settings()
        self.assertTrue(all(value == "" for value in company.values()))
        self.assertTrue(all(value == "" for value in payment.values()))
        self.assertTrue(all(value == "" for value in smtp.values()))
        with module.app.app_context():
            self.assertEqual(module.get_invoice_terms(), "")

    def test_explicit_environment_values_seed_and_are_used(self):
        module = self.load_app(
            {
                "COMPANY_NAME": "Configured Company",
                "PAYMENT_BANK_NAME": "Configured Bank",
                "PAYMENT_ACCOUNT_NUMBER": "configured-account",
                "SMTP_HOST": "smtp.example.test",
            }
        )
        with module.app.app_context():
            self.assertEqual(module.get_company_profile()["name"], "Configured Company")
            self.assertEqual(module.get_payment_instructions()["bank_name"], "Configured Bank")
            self.assertEqual(module.get_payment_instructions()["account_number"], "configured-account")
            self.assertEqual(module.get_smtp_settings()["host"], "smtp.example.test")

    def test_existing_database_settings_survive_reinitialization(self):
        module = self.load_app(
            {
                "COMPANY_NAME": "Environment Company",
                "PAYMENT_BANK_NAME": "Environment Bank",
            }
        )
        with module.app.app_context():
            module.set_setting("company_name", "Saved Company")
            module.set_setting("payment_bank_name", "Saved Bank")
            module.db().commit()
            module.init_db()
            self.assertEqual(module.get_company_profile()["name"], "Saved Company")
            self.assertEqual(module.get_payment_instructions()["bank_name"], "Saved Bank")


if __name__ == "__main__":
    unittest.main()
