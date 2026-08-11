import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class BasicDataPermissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_basic_permission_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from buyers")
            connection.execute("delete from manufacturers")
            connection.execute("delete from owners")
            connection.execute("delete from users")
            cursor = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('Employee', 'employee@example.com', 'unused', 'employee', '2026-08-03T12:00:00')
                """
            )
            self.user_id = cursor.lastrowid
            for resource_key in ("owners", "manufacturers", "buyers"):
                connection.execute(
                    "update role_menu_permissions set is_enabled = 0 where role = 'employee' and menu_key = ?",
                    (resource_key,),
                )
                connection.execute(
                    "update role_action_permissions set is_enabled = 0 where role = 'employee' and resource_key = ?",
                    (resource_key,),
                )
                connection.execute(
                    """
                    update role_action_permissions set is_enabled = 1
                    where role = 'employee' and resource_key = ? and action_key = 'edit'
                    """,
                    (resource_key,),
                )
            cursor = connection.execute(
                "insert into owners (owner_number, name, created_at) values ('OWN999', 'Test owner', '2026-08-03T12:00:00')"
            )
            self.owner_id = cursor.lastrowid
            cursor = connection.execute(
                "insert into manufacturers (manufacturer_number, name, created_at) values ('MFG999', 'Test maker', '2026-08-03T12:00:00')"
            )
            self.manufacturer_id = cursor.lastrowid
            cursor = connection.execute(
                """
                insert into buyers (
                    buyer_number, country, country_code, name, owner_id, owner, manufacturer_id,
                    detailed_address, equipment_manufacturer, created_at
                ) values ('BUY99999', 'US', 'US', 'Test site', ?, 'Test owner', ?,
                          '100 Test St, Houston, TX 77001', 'Test maker', '2026-08-03T12:00:00')
                """,
                (self.owner_id, self.manufacturer_id),
            )
            self.buyer_id = cursor.lastrowid
            connection.commit()

        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_edit_permission_implies_view_and_menu_for_basic_data(self):
        expectations = {
            "/owners": ("/owners", "editOwner", "新增业主"),
            "/manufacturers": ("/manufacturers", "editManufacturer", "新增厂家"),
            "/buyers": ("/buyers", "editBuyer", "新增站点"),
        }
        for path, (menu_href, edit_marker, create_label) in expectations.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                html = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'href="{menu_href}"', html)
                self.assertIn(edit_marker, html)
                self.assertNotIn(create_label, html)

    def test_employee_with_edit_permission_can_update_all_three_resources(self):
        owner_response = self.client.post(
            f"/owners/{self.owner_id}/edit",
            data={"owner_number": "OWN999", "name": "Updated owner"},
        )
        self.assertEqual(owner_response.status_code, 302)

        manufacturer_response = self.client.post(
            f"/manufacturers/{self.manufacturer_id}/edit",
            data={"manufacturer_number": "MFG999", "name": "Updated maker"},
        )
        self.assertEqual(manufacturer_response.status_code, 302)

        buyer_response = self.client.post(
            f"/buyers/{self.buyer_id}/edit",
            data={
                "buyer_number": "BUY99999",
                "country_code": "US",
                "name": "Updated site",
                "owner_id": str(self.owner_id),
                "manufacturer_id": str(self.manufacturer_id),
                "detailed_address": "101 Test St, Houston, TX 77001",
            },
        )
        self.assertEqual(buyer_response.status_code, 302)

        with self.module.app.app_context():
            connection = self.module.db()
            self.assertEqual(connection.execute("select name from owners where id = ?", (self.owner_id,)).fetchone()["name"], "Updated owner")
            self.assertEqual(connection.execute("select name from manufacturers where id = ?", (self.manufacturer_id,)).fetchone()["name"], "Updated maker")
            self.assertEqual(connection.execute("select name from buyers where id = ?", (self.buyer_id,)).fetchone()["name"], "Updated site")

    def test_buttons_and_routes_still_require_the_specific_action(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                update role_action_permissions set is_enabled = 0
                where role = 'employee' and resource_key = 'owners' and action_key = 'edit'
                """
            )
            connection.execute(
                """
                update role_action_permissions set is_enabled = 1
                where role = 'employee' and resource_key = 'owners' and action_key = 'view'
                """
            )
            connection.commit()

        owners_response = self.client.get("/owners")
        self.assertEqual(owners_response.status_code, 200)
        self.assertNotIn("editOwner", owners_response.get_data(as_text=True))
        denied_edit = self.client.post(
            f"/owners/{self.owner_id}/edit",
            data={"owner_number": "OWN999", "name": "Not allowed"},
        )
        self.assertEqual(denied_edit.status_code, 403)

        denied_import = self.client.post("/buyers/import")
        self.assertEqual(denied_import.status_code, 403)

    def test_menu_permission_alone_implies_read_only_access(self):
        with self.module.app.app_context():
            connection = self.module.db()
            for resource_key in ("owners", "manufacturers", "buyers"):
                connection.execute(
                    "update role_action_permissions set is_enabled = 0 where role = 'employee' and resource_key = ?",
                    (resource_key,),
                )
                connection.execute(
                    "update role_menu_permissions set is_enabled = 1 where role = 'employee' and menu_key = ?",
                    (resource_key,),
                )
            connection.commit()

        for path in ("/owners", "/manufacturers", "/buyers"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
        owners_html = self.client.get("/owners").get_data(as_text=True)
        self.assertIn('href="/owners"', owners_html)
        self.assertNotIn("editOwner", owners_html)
        self.assertNotIn("新增业主", owners_html)

    def test_saving_menu_permission_also_persists_view_permission(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("update users set role = 'admin' where id = ?", (self.user_id,))
            connection.commit()

        response = self.client.post(
            "/settings/menu-permissions",
            data={"menu__employee__owners": "1"},
        )
        self.assertEqual(response.status_code, 302)

        with self.module.app.app_context():
            permission = self.module.db().execute(
                """
                select is_enabled from role_action_permissions
                where role = 'employee' and resource_key = 'owners' and action_key = 'view'
                """
            ).fetchone()
            self.assertEqual(permission["is_enabled"], 1)

    def test_buyers_default_sort_is_by_buyer_number(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                insert into buyers (
                    buyer_number, country, country_code, name, owner_id, owner, manufacturer_id,
                    detailed_address, equipment_manufacturer, created_at
                ) values ('BUY00001', 'US', 'US', 'First numbered site', ?, 'Test owner', ?,
                          '1 First St, Houston, TX 77001', 'Test maker', '2026-08-10T12:00:00')
                """,
                (self.owner_id, self.manufacturer_id),
            )
            connection.commit()

        response = self.client.get("/buyers")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("BUY00001"), html.index("BUY99999"))


if __name__ == "__main__":
    unittest.main()
