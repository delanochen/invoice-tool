# -*- coding: utf-8 -*-
"""Service report worker rows: 0 miles / 0 hours are legal for self_drive and
following (随行) modes. Empty or negative values remain invalid."""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


class ServiceReportZeroMileageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_zero_mileage_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            for table in (
                "service_report_save_tokens", "service_report_workers", "service_reports",
                "service_orders", "clients", "users",
            ):
                connection.execute(f"delete from {table}")
            self.user_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Admin', 'zero-admin@example.com', 'unused', 'admin', 1, '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.employee_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, country_code, created_at)
                values ('Technician', 'zero-tech@example.com', 'unused', 'employee', 1, 'US', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, created_at)
                values ('99998', 'Zero Client', 'Z', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_id, client_name, site_address, client_order_number,
                    status, start_date, created_by, created_at
                ) values ('SO-ZERO', ?, 'Zero Client', 'Test Site', 'ORD-Z', 'open', '2026-08-13', ?, '2026-08-13T00:00:00')
                """,
                (self.client_id, self.user_id),
            ).lastrowid
            connection.commit()
        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.user_id

    def _submit(self, mode, miles, hours, public_hours=""):
        return self.http.post(
            f"/service-orders/{self.order_id}/reports/new",
            data={
                "save_token": f"zero-token-{mode}-{miles}-{hours}",
                "report_date": "2026-08-13",
                "actual_work_date": "2026-08-13",
                "arrival_time_hour": "08", "arrival_time_minute": "00",
                "departure_time_hour": "16", "departure_time_minute": "00",
                "mileage_billing_method": "per_person",
                "worker_user_id": [str(self.employee_id)],
                "worker_travel_mode": [mode],
                "worker_driving_miles": [str(miles)],
                "worker_travel_hours": [str(hours)],
                "worker_public_transport_hours": [str(public_hours)],
                "worker_work_description": ["现场协助"],
                "site_address": "Test Site",
            },
        )

    def _report_count(self):
        with self.module.app.app_context():
            return self.module.db().execute(
                "select count(*) as c from service_reports where service_order_id = ?",
                (self.order_id,),
            ).fetchone()["c"]

    def test_self_drive_zero_miles_zero_hours_allowed(self):
        resp = self._submit("self_drive", 0, 0)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._report_count(), 1, "0 miles / 0 hours should save")

    def test_following_zero_miles_zero_hours_allowed(self):
        resp = self._submit("following", 0, 0)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._report_count(), 1, "following 0 miles / 0 hours should save")

    def test_zero_miles_with_hours_allowed(self):
        resp = self._submit("self_drive", 0, 1.5)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._report_count(), 1)

    def test_empty_miles_still_rejected(self):
        resp = self._submit("self_drive", "", 1)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._report_count(), 0, "empty miles must stay invalid")

    def test_empty_hours_still_rejected(self):
        resp = self._submit("self_drive", 10, "")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._report_count(), 0, "empty hours must stay invalid")

    def test_negative_miles_still_rejected(self):
        resp = self._submit("self_drive", -5, 1)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._report_count(), 0, "negative miles must stay invalid")

    def test_negative_hours_still_rejected(self):
        resp = self._submit("following", 5, -1)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._report_count(), 0, "negative hours must stay invalid")


if __name__ == "__main__":
    unittest.main()
