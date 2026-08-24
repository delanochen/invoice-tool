import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class CustomerReimbursementMroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_mro_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from customer_reimbursement_items")
            connection.execute("delete from customer_reimbursements")
            connection.execute("delete from expense_items")
            connection.execute("delete from expenses")
            connection.execute("delete from service_orders where order_number = 'SO-MRO'")
            connection.execute("delete from projects where project_type = 'expense' and name_key like 'mro supplies%'")
            connection.execute(
                """update projects set name = 'MRO Supplies', name_key = 'mro supplies'
                   where project_type = 'invoice' and name_key like 'mro supplies%'"""
            )
            connection.execute("delete from users where email = 'mro-admin@example.com'")
            user_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('MRO Admin', 'mro-admin@example.com', 'unused', 'admin', '2026-08-12T12:00:00')
                """
            ).lastrowid
            order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_name, site_address, client_order_number, created_by, created_at
                ) values ('SO-MRO', 'Test client', 'Test address', 'CLIENT-MRO', ?, '2026-08-12T12:00:00')
                """,
                (user_id,),
            ).lastrowid
            expense_project_id = connection.execute(
                """
                insert into projects (name, name_key, project_type, unit_price, is_active, created_at)
                values ('MRO Supplies配件及耗材费', 'mro supplies配件及耗材费',
                        'expense', 0, 1, '2026-08-12T12:00:00')
                """
            ).lastrowid
            for number, status, amount in (("EXP-APPROVED", "approved", 125), ("EXP-DRAFT", "draft", 50)):
                expense_id = connection.execute(
                    """
                    insert into expenses (
                        service_order_id, expense_number, project, expense_date, amount, status,
                        created_by, created_at, updated_at
                    ) values (?, ?, 'MRO Supplies配件及耗材费', '2026-08-12', ?, ?, ?,
                              '2026-08-12T12:00:00', '2026-08-12T12:00:00')
                    """,
                    (order_id, number, amount, status, user_id),
                ).lastrowid
                connection.execute(
                    """
                    insert into expense_items (expense_id, project_id, project, amount, sort_order)
                    values (?, ?, 'MRO Supplies配件及耗材费', ?, 0)
                    """,
                    (expense_id, expense_project_id, amount),
                )
            reimbursement_id = connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, status, created_by, created_at
                ) values (?, 'settlement.pdf', 'settlement.pdf', 'draft', ?, '2026-08-12T12:00:00')
                """,
                (order_id, user_id),
            ).lastrowid
            connection.execute(
                """
                insert into customer_reimbursement_items (
                    customer_reimbursement_id, worker_name, project_date,
                    standard_hours, standard_rate, labor_total, total, sort_order
                ) values (?, 'Worker', '2026-08-12', 1, 70, 70, 70, 0)
                """,
                (reimbursement_id,),
            )
            connection.commit()
            self.order_id = order_id
            self.user_id = user_id
            self.reimbursement_id = reimbursement_id

    def test_only_approved_mro_flows_to_other_and_mro_invoice(self):
        with self.module.app.app_context():
            totals = self.module.update_customer_reimbursement_totals(self.reimbursement_id)
            reimbursement = self.module.db().execute(
                "select * from customer_reimbursements where id = ?",
                (self.reimbursement_id,),
            ).fetchone()
            self.module.db().execute(
                """update projects
                   set name = 'MRO Supplies配件及耗材费',
                       name_key = 'mro supplies配件及耗材费'
                   where project_type = 'invoice' and name = 'MRO Supplies'"""
            )
            invoice_items = self.module.customer_reimbursement_invoice_items(reimbursement)
            invoice_projects = {
                self.module.db().execute("select name from projects where id = ?", (item["project_id"],)).fetchone()["name"]: item
                for item in invoice_items
            }

            transferred = self.module.db().execute(
                """
                select * from customer_reimbursement_items
                where customer_reimbursement_id = ? and worker_name = 'MRO Admin'
                """,
                (self.reimbursement_id,),
            ).fetchone()
            self.assertEqual(transferred["auto_other"], 125.0)
            self.assertEqual(totals["mro_supplies_total"], 125.0)
            self.assertEqual(totals["other_total"], 125.0)
            self.assertEqual(totals["travel_total"], 0.0)
            self.assertEqual(totals["employee_expense_total"], 125.0)
            self.assertEqual(totals["total_amount"], 195.0)
            self.assertEqual(reimbursement["mro_supplies_total"], 125.0)
            self.assertEqual(invoice_projects["MRO Supplies配件及耗材费"]["amount"], 125.0)
            self.assertNotIn("Travel Expenses Reimbursement", invoice_projects)

    def test_bilingual_expense_labels_map_to_all_customer_reimbursement_fields(self):
        labels = {
            "Accommodation/Lodging住宿费": "lodging",
            "Airfare机票费": "airfare",
            "Car Rental Fee租车费用": "rental_car",
            "Checked Baggage Fee行李费": "baggage",
            "Fuel Expenses燃油费": "fuel",
            "Parking Charge停车费": "parking",
            "Taxi Fare / Ride-Hailing Fare打车费": "taxi",
            "MRO Supplies配件及耗材费": "other",
        }
        for label, expected_field in labels.items():
            fuel_type = "rental" if expected_field == "fuel" else None
            self.assertEqual(
                self.module.customer_reimbursement_expense_field(label, fuel_type),
                expected_field,
            )

    def test_customer_reimbursement_lodging_over_limit_is_warning_only(self):
        with self.module.app.test_request_context(
            "/customer-reimbursements/test",
            method="POST",
            data={
                "worker_name": ["Worker"],
                "project_date": ["2026-08-12"],
                "standard_hours": ["0"],
                "transport_hours": ["0"],
                "overtime_hours": ["0"],
                "holiday_hours": ["0"],
                "lodging": ["450"],
                "airfare": ["0"],
                "baggage": ["0"],
                "rental_car": ["0"],
                "fuel": ["0"],
                "parking": ["0"],
                "taxi": ["0"],
                "other": ["0"],
                "miles": ["0"],
            },
        ):
            rows = self.module.customer_reimbursement_items_from_form()
            self.assertEqual(rows[0]["lodging"], 450.0)

    def test_expense_detail_report_lists_each_item_and_filters_person(self):
        with self.module.app.app_context():
            connection = self.module.db()
            expense = connection.execute(
                "select id from expenses where expense_number = 'EXP-APPROVED'"
            ).fetchone()
            project = connection.execute(
                "select id from projects where project_type = 'expense' and name_key like 'mro supplies%'"
            ).fetchone()
            connection.execute(
                """
                insert into expense_items (expense_id, project_id, project, amount, description, sort_order)
                values (?, ?, 'MRO Supplies', 25, 'Second receipt', 1)
                """,
                (expense["id"], project["id"]),
            )
            connection.commit()
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.user_id
        page = client.get(f"/reports/expenses?person_id={self.user_id}").get_data(as_text=True)
        self.assertEqual(page.count("EXP-APPROVED"), 2)
        self.assertIn("Second receipt", page)
        self.assertIn("明细金额", page)
        self.assertIn("人员", page)

    def test_coordinate_pair_accepts_one_paste_and_validates_ranges(self):
        parse = self.module.parse_coordinate_pair
        self.assertEqual(parse("33.3252078, -112.7639562"), (33.3252078, -112.7639562))
        self.assertEqual(parse("33.3252078，-112.7639562"), (33.3252078, -112.7639562))
        self.assertEqual(parse("33.3252078 -112.7639562"), (33.3252078, -112.7639562))
        with self.assertRaises(ValueError):
            parse("91, -112")
        with self.assertRaises(ValueError):
            parse("33.3")


if __name__ == "__main__":
    unittest.main()
