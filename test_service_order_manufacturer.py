import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class ServiceOrderManufacturerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_service_order_manufacturer_test_app", module_path)
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
            connection.execute("delete from service_orders")
            connection.execute("delete from buyers")
            connection.execute("delete from manufacturers")
            connection.execute("delete from owners")
            connection.execute("delete from work_order_types")
            connection.execute("delete from clients")
            connection.execute("delete from users")
            self.user_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', '2026-08-11T12:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, created_at)
                values ('CLI999', 'Test client', 'Test', '2026-08-11T12:00:00')
                """
            ).lastrowid
            owner_id = connection.execute(
                "insert into owners (owner_number, name, created_at) values ('OWN999', 'Test owner', '2026-08-11T12:00:00')"
            ).lastrowid
            self.first_manufacturer_id = connection.execute(
                "insert into manufacturers (manufacturer_number, name, created_at) values ('MFG001', 'Default maker', '2026-08-11T12:00:00')"
            ).lastrowid
            self.second_manufacturer_id = connection.execute(
                "insert into manufacturers (manufacturer_number, name, created_at) values ('MFG002', 'Selected maker', '2026-08-11T12:00:00')"
            ).lastrowid
            self.buyer_id = connection.execute(
                """
                insert into buyers (
                    buyer_number, client_id, country, country_code, name, owner_id, owner,
                    manufacturer_id, detailed_address, equipment_manufacturer, created_at
                ) values ('BUY99999', ?, 'US', 'US', 'Test site', ?, 'Test owner', ?,
                          '100 Test St', 'Default maker', '2026-08-11T12:00:00')
                """,
                (self.client_id, owner_id, self.first_manufacturer_id),
            ).lastrowid
            self.work_order_type_id = connection.execute(
                """
                insert into work_order_types (code, name, description, is_active, created_at)
                values ('TEST', 'Test type', '', 1, '2026-08-11T12:00:00')
                """
            ).lastrowid
            self.order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_id, buyer_id, manufacturer_id, client_name,
                    site_address, client_order_number, status, work_order_type_id,
                    region_code, country_code, created_by, created_at
                ) values ('SO2699999', ?, ?, ?, 'Test site', '100 Test St', 'CLIENT-SO-1',
                          'open', ?, 'americas', 'US', ?, '2026-08-11T12:00:00')
                """,
                (
                    self.client_id,
                    self.buyer_id,
                    self.first_manufacturer_id,
                    self.work_order_type_id,
                    self.user_id,
                ),
            ).lastrowid
            connection.commit()

        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_edit_form_lists_manufacturers_and_saves_selected_value(self):
        response = self.http.get(f"/service-orders/{self.order_id}/edit")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('select name="manufacturer_id" id="buyerManufacturer"', html)
        self.assertIn("MFG001 · Default maker", html)
        self.assertIn("MFG002 · Selected maker", html)

        response = self.http.post(
            f"/service-orders/{self.order_id}/edit",
            data={
                "client_id": str(self.client_id),
                "buyer_id": str(self.buyer_id),
                "manufacturer_id": str(self.second_manufacturer_id),
                "site_address": "100 Test St",
                "client_order_number": "CLIENT-SO-1",
                "country_code": "US",
                "work_order_type_id": str(self.work_order_type_id),
                "status": "open",
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.module.app.app_context():
            connection = self.module.db()
            order = connection.execute(
                "select manufacturer_id from service_orders where id = ?",
                (self.order_id,),
            ).fetchone()
            self.assertEqual(order["manufacturer_id"], self.second_manufacturer_id)

        detail = self.http.get(f"/service-orders/{self.order_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Selected maker", detail.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
