import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent


class PaymentTermsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_payment_terms_test_app", module_path)
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
                "email_delivery_logs", "invoice_items", "invoices", "expense_items",
                "expense_attachments", "expenses", "service_orders",
                "clients", "audit_logs", "users",
            ):
                connection.execute(f"delete from {table}")
            term = connection.execute(
                "select id from payment_terms where name = '20日截止、次月19日付款'"
            ).fetchone()
            self.term_id = term["id"]
            self.user_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Admin', 'admin@example.com', 'unused', 'admin', 1, '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.employee_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, country_code, created_at)
                values ('Technician', 'tech@example.com', 'unused', 'employee', 1, 'US', '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.client_id = connection.execute(
                """
                insert into clients (client_number, name, short_name, payment_term_id, created_at)
                values ('99999', 'Payment Term Client', 'PT Client', ?, '2026-08-13T00:00:00')
                """,
                (self.term_id,),
            ).lastrowid
            self.open_order_id = self._insert_order(connection, "SO-OPEN", "open")
            self.closed_order_id = self._insert_order(connection, "SO-CLOSED", "closed")
            self.eligible_invoice_id = self._insert_invoice(connection, "INV-ELIGIBLE", self.open_order_id)
            self.closed_invoice_id = self._insert_invoice(connection, "INV-CLOSED", self.closed_order_id)
            self.sent_invoice_id = self._insert_invoice(connection, "INV-SENT", self.open_order_id)
            connection.execute(
                """
                insert into email_delivery_logs (entity_type, entity_id, recipient, subject, sent_at)
                values ('invoice', ?, 'client@example.com', 'Sent invoice', '2026-08-13T00:00:00')
                """,
                (self.sent_invoice_id,),
            )
            self.paid_invoice_id = self._insert_invoice(connection, "INV-PAID", self.open_order_id, paid=True)
            connection.commit()
        self.http = self.module.app.test_client()
        with self.http.session_transaction() as session:
            session["user_id"] = self.user_id

    def _insert_order(self, connection, number, status):
        return connection.execute(
            """
            insert into service_orders (
                order_number, client_id, client_name, site_address, client_order_number,
                status, created_by, created_at
            ) values (?, ?, 'Payment Term Client', 'Test Site', ?, ?, ?, '2026-08-13T00:00:00')
            """,
            (number, self.client_id, number, status, self.user_id),
        ).lastrowid

    def _insert_invoice(self, connection, number, order_id, paid=False):
        return connection.execute(
            """
            insert into invoices (
                invoice_number, client_id, service_order_id, issue_date, due_date,
                status, paid_at, created_by, created_at
            ) values (?, ?, ?, '2026-08-21', '2026-09-20', 'completed', ?, ?, '2026-08-13T00:00:00')
            """,
            (number, self.client_id, order_id, "2026-08-30T00:00:00" if paid else None, self.user_id),
        ).lastrowid

    def test_cutoff_boundaries_and_month_end(self):
        term = {
            "rule_type": "monthly_cutoff", "fixed_days": 30, "cutoff_day": 20,
            "due_day": 19, "before_due_months": 1, "after_due_months": 2,
        }
        self.assertEqual(str(self.module.calculate_payment_due_date("2026-08-20", term)), "2026-09-19")
        self.assertEqual(str(self.module.calculate_payment_due_date("2026-08-21", term)), "2026-10-19")
        end_of_month_term = dict(term, due_day=31)
        self.assertEqual(str(self.module.calculate_payment_due_date("2027-01-20", end_of_month_term)), "2027-02-28")

    def test_payment_term_rule_requires_only_relevant_fields(self):
        with self.module.app.test_request_context(
            "/settings/payment-terms", method="POST",
            data={"name": "Fixed", "rule_type": "fixed_days", "fixed_days": "45"},
        ):
            values = self.module.payment_term_form_values()
            self.assertEqual(values["fixed_days"], 45)
            self.assertIsNone(values["cutoff_day"])
        with self.module.app.test_request_context(
            "/settings/payment-terms", method="POST",
            data={"name": "Cutoff", "rule_type": "monthly_cutoff", "cutoff_day": "20"},
        ):
            with self.assertRaisesRegex(ValueError, "请填写到期日"):
                self.module.payment_term_form_values()

    def test_preview_and_recalculation_protect_sent_paid_and_closed_invoices(self):
        response = self.http.get("/settings/payment-terms")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("INV-ELIGIBLE", page)
        self.assertNotIn("INV-CLOSED", page)
        self.assertNotIn("INV-SENT", page)
        self.assertNotIn("INV-PAID", page)
        self.assertIn("2026-10-19", page)

        response = self.http.post("/settings/payment-terms/recalculate-invoices", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.module.app.app_context():
            connection = self.module.db()
            due_dates = {
                row["invoice_number"]: row["due_date"]
                for row in connection.execute("select invoice_number, due_date from invoices").fetchall()
            }
            self.assertEqual(due_dates["INV-ELIGIBLE"], "2026-10-19")
            self.assertEqual(due_dates["INV-CLOSED"], "2026-09-20")
            self.assertEqual(due_dates["INV-SENT"], "2026-09-20")
            self.assertEqual(due_dates["INV-PAID"], "2026-09-20")

    def test_navigation_and_client_assignment_are_visible(self):
        response = self.http.get("/clients")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("账期方案", page)
        self.assertIn("20日截止、次月19日付款", page)
        self.assertIn("系统配置", page)

    def test_shared_report_export_generates_xlsx(self):
        response = self.http.post(
            "/reports/export-visible.xlsx",
            json={
                "title": "测试报表",
                "headers": ["编号", "金额"],
                "rows": [["INV-001", "$100.00"]],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(response.data.startswith(b"PK"))
        response = self.http.get("/service-orders")
        page = response.get_data(as_text=True)
        self.assertIn("report-export.js", page)
        self.assertIn("export-visible.xlsx", page)

    def test_login_page_renders_without_authenticated_session(self):
        with self.http.session_transaction() as session:
            session.clear()
        response = self.http.get("/login?next=/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("登录", response.get_data(as_text=True))

    def test_service_report_worker_rows_and_long_distance_warning(self):
        with self.module.app.app_context():
            self.module.db().execute(
                "update service_orders set start_date = '2026-08-13' where id = ?",
                (self.open_order_id,),
            )
            self.module.db().commit()
        response = self.http.post(
            f"/service-orders/{self.open_order_id}/reports/new",
            data={
                "save_token": "travel-row-test-token",
                "report_date": "2026-08-13",
                "actual_work_date": "2026-08-13",
                "arrival_time_hour": "08", "arrival_time_minute": "00",
                "departure_time_hour": "16", "departure_time_minute": "00",
                "mileage_billing_method": "per_person",
                "worker_user_id": [str(self.employee_id)],
                "worker_travel_mode": ["following"],
                "worker_driving_miles": ["600"],
                "worker_travel_hours": ["8"],
                "worker_public_transport_hours": [""],
                "worker_work_description": ["现场协助"],
                "site_address": "Test Site",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            report = self.module.db().execute(
                "select * from service_reports where service_order_id = ? order by id desc limit 1",
                (self.open_order_id,),
            ).fetchone()
            worker = self.module.db().execute(
                "select * from service_report_workers where report_id = ?", (report["id"],)
            ).fetchone()
            self.assertEqual(report["driving_miles"], 600)
            self.assertEqual(report["travel_hours"], 8)
            self.assertEqual(worker["travel_mode"], "following")
            self.assertEqual(worker["work_description"], "现场协助")
            order = dict(self.module.db().execute(
                "select * from service_orders where id = ?", (self.open_order_id,)
            ).fetchone())
            order["buyer_owner"] = "Test Owner"
            document_bytes = self.module.build_service_report_docx(report, order)
            self.assertTrue(document_bytes.startswith(b"PK"))
            warnings = self.module.excessive_following_mileage_rows(self.open_order_id)
            self.assertEqual(len(warnings), 1)
            grade_id = self.module.db().execute(
                """
                insert into employee_grades (
                    grade_name, car_allowance_method, car_mileage_rate, car_hourly_rate, created_at
                ) values ('Travel Test', 'mileage', 0.5, 10, '2026-08-13T00:00:00')
                """
            ).lastrowid
            self.module.db().execute(
                "update users set employee_grade_id = ? where id = ?",
                (grade_id, self.employee_id),
            )
            self.module.db().commit()
            payroll = self.module.payroll_rows_for_range(
                self.module.date(2026, 8, 6), self.module.date(2026, 8, 19),
                self.module.date(2026, 9, 2), str(self.employee_id),
            )
            self.assertEqual(payroll["rows"][0]["car_allowance"], 80)

    def test_sanhe_service_order_number_warning_in_list_and_calendar(self):
        self.assertEqual(
            self.module.client_order_number_warning(
                "Sanhe Tongfei Refrigeration Co., Ltd.", "SHPG123456789012"
            ),
            "",
        )
        self.assertTrue(
            self.module.client_order_number_warning("三河同飞制冷股份有限公司", "SO-INVALID")
        )
        self.assertEqual(
            self.module.client_order_number_warning("Another Customer", "SO-INVALID"),
            "",
        )
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                "update clients set name = 'Sanhe Tongfei Refrigeration Co., Ltd.' where id = ?",
                (self.client_id,),
            )
            connection.execute(
                "update service_orders set client_order_number = 'SO-INVALID', start_date = '2026-08-13' where id = ?",
                (self.open_order_id,),
            )
            connection.commit()

        list_page = self.http.get("/service-orders").get_data(as_text=True)
        self.assertIn("client-order-number-warning", list_page)
        self.assertIn("SO-INVALID", list_page)
        calendar_page = self.http.get("/service-orders/calendar?year=2026&month=8").get_data(as_text=True)
        self.assertIn("client-order-number-warning", calendar_page)
        self.assertIn("SO-INVALID", calendar_page)

    def test_image_attachments_are_recognized_by_mime_type_or_filename(self):
        self.assertTrue(self.module.is_image_attachment("image/jpeg", "receipt.bin"))
        self.assertTrue(self.module.is_image_attachment("", "receipt.JPG"))
        self.assertTrue(self.module.is_image_attachment(None, "receipt.webp"))
        self.assertFalse(self.module.is_image_attachment("application/pdf", "receipt.pdf"))

    def test_calendar_keeps_multiday_order_on_one_lane(self):
        shared_first = {"order_id": 1, "order_number": "SO-1"}
        shared_second = {"order_id": 1, "order_number": "SO-1"}
        monday_only = {"order_id": 2, "order_number": "SO-2"}
        thursday_only = {"order_id": 3, "order_number": "SO-3"}
        week = [
            {"events": [shared_first, monday_only]},
            {"events": []},
            {"events": []},
            {"events": [shared_second, thursday_only]},
            {"events": []},
            {"events": []},
            {"events": []},
        ]
        self.module.assign_service_order_calendar_lanes(week)
        self.assertEqual(shared_first["lane"], shared_second["lane"])
        self.assertNotEqual(shared_first["lane"], monday_only["lane"])
        self.assertNotEqual(shared_second["lane"], thursday_only["lane"])
        self.assertEqual(monday_only["lane"], thursday_only["lane"])
        self.assertTrue(all(day["lane_count"] == 2 for day in week))

    def test_service_order_detail_shows_expense_count_and_total(self):
        with self.module.app.app_context():
            connection = self.module.db()
            for number, amount in (("EX-TOTAL-1", 454.11), ("EX-TOTAL-2", 201.48)):
                connection.execute(
                    """
                    insert into expenses (
                        service_order_id, expense_number, project, expense_date, amount,
                        currency, status, created_by, created_at, updated_at
                    ) values (?, ?, 'MRO Supplies', '2026-08-13', ?, 'USD', 'draft', ?,
                              '2026-08-13T00:00:00', '2026-08-13T00:00:00')
                    """,
                    (self.open_order_id, number, amount, self.user_id),
                )
            connection.commit()

        page = self.http.get(f"/service-orders/{self.open_order_id}").get_data(as_text=True)
        self.assertIn("2 张报销 · 合计 $655.59", page)
        self.assertIn('data-selected-summary="2 张报销 · 合计 $655.59"', page)

    def test_projects_can_filter_by_type_and_name(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute(
                """
                insert into projects (
                    name, name_key, project_type, default_amount, unit_price,
                    tax_rate, is_active, created_at
                ) values ('Filter Expense Alpha', 'filter expense alpha', 'expense', 0, 0, 0, 1,
                          '2026-08-15T00:00:00')
                """
            )
            connection.execute(
                """
                insert into projects (
                    name, name_key, project_type, default_amount, unit_price,
                    tax_rate, is_active, created_at
                ) values ('Filter Invoice Beta', 'filter invoice beta', 'invoice', 0, 0, 0, 1,
                          '2026-08-15T00:00:00')
                """
            )
            connection.commit()

        page = self.http.get("/projects?project_type=expense&q=Alpha").get_data(as_text=True)
        self.assertIn("Filter Expense Alpha", page)
        self.assertNotIn("Filter Invoice Beta", page)
        self.assertIn('value="expense" selected', page)
        self.assertIn('value="Alpha"', page)

    def test_rental_vehicle_fuel_is_customer_billable_but_personal_fuel_is_not(self):
        with self.module.app.app_context():
            connection = self.module.db()
            fuel_project = connection.execute(
                "select * from projects where project_type = 'expense' and lower(name) like '%fuel%' limit 1"
            ).fetchone()
            if fuel_project is None:
                fuel_project_id = connection.execute(
                    """
                    insert into projects (
                        name, name_key, project_type, default_amount, unit_price,
                        tax_rate, is_active, created_at
                    ) values ('Fuel Expenses', 'fuel expenses', 'expense', 0, 0, 0, 1,
                              '2026-08-15T00:00:00')
                    """
                ).lastrowid
                fuel_project = connection.execute(
                    "select * from projects where id = ?", (fuel_project_id,)
                ).fetchone()
            expense_id = connection.execute(
                """
                insert into expenses (
                    service_order_id, expense_number, project_id, project, expense_date,
                    amount, currency, status, created_by, created_at, updated_at
                ) values (?, 'EX-FUEL-TYPES', ?, ?, '2026-08-13', 300, 'USD', 'approved', ?,
                          '2026-08-13T00:00:00', '2026-08-13T00:00:00')
                """,
                (self.open_order_id, fuel_project["id"], fuel_project["name"], self.user_id),
            ).lastrowid
            connection.executemany(
                """
                insert into expense_items (
                    expense_id, project_id, project, amount, description,
                    fuel_vehicle_type, sort_order
                ) values (?, ?, ?, ?, '', ?, ?)
                """,
                [
                    (expense_id, fuel_project["id"], fuel_project["name"], 100, "personal", 0),
                    (expense_id, fuel_project["id"], fuel_project["name"], 200, "rental", 1),
                ],
            )
            connection.commit()
            self.assertEqual(
                self.module.approved_rental_vehicle_fuel_total(self.open_order_id),
                200,
            )
            reimbursement_id = connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, created_by, created_at
                ) values (?, 'fuel-test.pdf', 'fuel-test.pdf', ?, '2026-08-15T00:00:00')
                """,
                (self.open_order_id, self.user_id),
            ).lastrowid
            self.module.update_customer_reimbursement_totals(reimbursement_id, [])
            reimbursement = connection.execute(
                "select * from customer_reimbursements where id = ?", (reimbursement_id,)
            ).fetchone()
            transferred_row = connection.execute(
                "select * from customer_reimbursement_items where customer_reimbursement_id = ?",
                (reimbursement_id,),
            ).fetchone()
            self.assertEqual(transferred_row["worker_name"], "Admin")
            self.assertEqual(transferred_row["project_date"], "2026-08-13")
            self.assertEqual(transferred_row["auto_fuel"], 200)
            self.assertEqual(reimbursement["rental_fuel_total"], 0)
            self.assertEqual(reimbursement["travel_total"], 200)
            self.assertEqual(reimbursement["total_amount"], 200)
            connection.execute(
                "update service_orders set start_date = '2026-08-13' where id = ?",
                (self.open_order_id,),
            )
            connection.execute("update users set role = 'manager' where id = ?", (self.user_id,))
            connection.commit()

        form_page = self.http.get(
            f"/service-orders/{self.open_order_id}/expenses/new"
        ).get_data(as_text=True)
        self.assertIn("个人／自有车辆（仅员工报销）", form_page)
        self.assertIn("租赁车辆（可计入工单结算）", form_page)

        with self.module.app.test_request_context(
            "/expenses/new",
            method="POST",
            data={
                "project_id": [str(fuel_project["id"])],
                "item_amount": ["50"],
                "item_description": ["Rental fuel"],
                "fuel_vehicle_type": ["rental"],
            },
        ):
            with self.module.app.app_context():
                rows = self.module.expense_items_from_form()
                self.assertEqual(rows[0]["fuel_vehicle_type"], "rental")

        with self.module.app.test_request_context(
            "/expenses/new",
            method="POST",
            data={
                "project_id": [str(fuel_project["id"])],
                "item_amount": ["50"],
                "item_description": ["Missing vehicle type"],
                "fuel_vehicle_type": [""],
            },
        ):
            with self.module.app.app_context():
                with self.assertRaisesRegex(ValueError, "必须选择"):
                    self.module.expense_items_from_form()

    def test_approved_expense_categories_transfer_to_matching_reimbursement_columns(self):
        category_amounts = {
            "Accommodation/Lodging": 10,
            "Airfare": 20,
            "Car Rental Fee": 30,
            "Checked Baggage Fee": 40,
            "Fuel Expenses": 50,
            "Parking Charge": 60,
            "Taxi Fare / Ride-Hailing Fare": 70,
            "MRO Supplies": 80,
        }
        with self.module.app.app_context():
            connection = self.module.db()
            project_ids = {}
            for project_name in category_amounts:
                project = connection.execute(
                    "select id from projects where project_type = 'expense' and name_key = ? limit 1",
                    (self.module.project_name_key(project_name),),
                ).fetchone()
                if project is None:
                    project_id = connection.execute(
                        """
                        insert into projects (
                            name, name_key, project_type, default_amount, unit_price,
                            tax_rate, is_active, created_at
                        ) values (?, ?, 'expense', 0, 0, 0, 1, '2026-08-15T00:00:00')
                        """,
                        (project_name, self.module.project_name_key(project_name)),
                    ).lastrowid
                else:
                    project_id = project["id"]
                project_ids[project_name] = project_id

            expense_id = connection.execute(
                """
                insert into expenses (
                    service_order_id, expense_number, project_id, project, expense_date,
                    amount, currency, status, created_by, created_at, updated_at
                ) values (?, 'EX-AUTO-CATEGORIES', ?, 'Accommodation/Lodging', '2026-08-14',
                          1359, 'USD', 'approved', ?, '2026-08-14T00:00:00', '2026-08-14T00:00:00')
                """,
                (self.open_order_id, project_ids["Accommodation/Lodging"], self.user_id),
            ).lastrowid
            for sort_order, (project_name, amount) in enumerate(category_amounts.items()):
                connection.execute(
                    """
                    insert into expense_items (
                        expense_id, project_id, project, amount, description,
                        fuel_vehicle_type, sort_order
                    ) values (?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        expense_id,
                        project_ids[project_name],
                        project_name,
                        amount,
                        "rental" if project_name == "Fuel Expenses" else None,
                        sort_order,
                    ),
                )
            connection.execute(
                """
                insert into expense_items (
                    expense_id, project_id, project, amount, description,
                    fuel_vehicle_type, sort_order
                ) values (?, ?, 'Fuel Expenses', 999, '', 'personal', 99)
                """,
                (expense_id, project_ids["Fuel Expenses"]),
            )
            reimbursement_id = connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, created_by, created_at
                ) values (?, 'category-test.pdf', 'category-test.pdf', ?, '2026-08-15T00:00:00')
                """,
                (self.open_order_id, self.user_id),
            ).lastrowid
            connection.execute(
                """
                insert into customer_reimbursement_items (
                    customer_reimbursement_id, worker_name, project_date, other, sort_order
                ) values (?, 'Admin', '2026-08-14', 5, 0)
                """,
                (reimbursement_id,),
            )
            connection.commit()

            totals = self.module.update_customer_reimbursement_totals(reimbursement_id)
            row = connection.execute(
                "select * from customer_reimbursement_items where customer_reimbursement_id = ?",
                (reimbursement_id,),
            ).fetchone()
            self.assertEqual(row["auto_lodging"], 10)
            self.assertEqual(row["auto_airfare"], 20)
            self.assertEqual(row["auto_rental_car"], 30)
            self.assertEqual(row["auto_baggage"], 40)
            self.assertEqual(row["auto_fuel"], 50)
            self.assertEqual(row["auto_parking"], 60)
            self.assertEqual(row["auto_taxi"], 70)
            self.assertEqual(row["auto_other"], 80)
            self.assertEqual(row["other"], 5)
            self.assertEqual(totals["employee_expense_total"], 360)
            self.assertEqual(totals["travel_total"], 365)
            self.assertEqual(totals["total_amount"], 365)

            connection.execute(
                "update customer_reimbursements set expense_transfer_cutoff_at = '2026-08-15T00:00:00' where id = ?",
                (reimbursement_id,),
            )
            late_expense_id = connection.execute(
                """
                insert into expenses (
                    service_order_id, expense_number, project_id, project, expense_date,
                    amount, currency, status, reviewed_at, created_by, created_at, updated_at
                ) values (?, 'EX-LATE-MRO', ?, 'MRO Supplies', '2026-08-14', 500, 'USD',
                          'approved', '2026-08-16T00:00:00', ?,
                          '2026-08-14T00:00:00', '2026-08-16T00:00:00')
                """,
                (self.open_order_id, project_ids["MRO Supplies"], self.user_id),
            ).lastrowid
            connection.execute(
                """
                insert into expense_items (
                    expense_id, project_id, project, amount, description, sort_order
                ) values (?, ?, 'MRO Supplies', 500, '', 0)
                """,
                (late_expense_id, project_ids["MRO Supplies"]),
            )
            totals = self.module.update_customer_reimbursement_totals(reimbursement_id)
            row = connection.execute(
                "select * from customer_reimbursement_items where customer_reimbursement_id = ?",
                (reimbursement_id,),
            ).fetchone()
            self.assertEqual(row["auto_other"], 80)
            self.assertEqual(totals["total_amount"], 365)

    def test_employee_lodging_over_limit_is_warning_not_validation_error(self):
        with self.module.app.app_context():
            connection = self.module.db()
            self.module.set_setting("payroll_lodging_limit", 100)
            lodging_project = connection.execute(
                "select * from projects where project_type = 'expense' and name_key = ? limit 1",
                (self.module.project_name_key("Accommodation/Lodging"),),
            ).fetchone()
            if lodging_project is None:
                lodging_project_id = connection.execute(
                    """
                    insert into projects (
                        name, name_key, project_type, default_amount, unit_price,
                        tax_rate, is_active, created_at
                    ) values ('Accommodation/Lodging', 'accommodation/lodging', 'expense',
                              0, 0, 0, 1, '2026-08-15T00:00:00')
                    """
                ).lastrowid
            else:
                lodging_project_id = lodging_project["id"]
            connection.commit()

        with self.module.app.test_request_context(
            "/expenses/new",
            method="POST",
            data={
                "project_id": [str(lodging_project_id)],
                "item_amount": ["450"],
                "item_description": ["Three employees, three nights"],
                "fuel_vehicle_type": [""],
            },
        ):
            with self.module.app.app_context():
                rows = self.module.expense_items_from_form()
                self.assertEqual(rows[0]["amount"], 450)

        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("update users set role = 'manager' where id = ?", (self.user_id,))
            connection.execute(
                "update service_orders set start_date = '2026-08-13' where id = ?",
                (self.open_order_id,),
            )
            connection.commit()
        page = self.http.get(f"/service-orders/{self.open_order_id}/expenses/new").get_data(as_text=True)
        self.assertIn("expenseLodgingLimit = 100", page)
        self.assertIn("isLodging: true", page)

    def test_draft_customer_reimbursement_syncs_all_report_worker_days(self):
        with self.module.app.app_context():
            connection = self.module.db()
            third_worker_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, is_active, created_at)
                values ('Third Worker', 'third@example.com', 'unused', 'employee', 1,
                        '2026-08-15T00:00:00')
                """
            ).lastrowid
            worker_ids = [self.user_id, self.employee_id, third_worker_id]

            def add_report(day):
                report_id = connection.execute(
                    """
                    insert into service_reports (
                        service_order_id, report_date, actual_work_date,
                        arrival_time, departure_time, created_by, created_at, updated_at
                    ) values (?, ?, ?, '08:00', '16:00', ?,
                              '2026-08-15T00:00:00', '2026-08-15T00:00:00')
                    """,
                    (self.open_order_id, day, day, self.user_id),
                ).lastrowid
                for worker_id in worker_ids:
                    connection.execute(
                        """
                        insert into service_report_workers (
                            report_id, user_id, driving_miles, travel_mode,
                            travel_hours, public_transport_hours
                        ) values (?, ?, 10, 'self_drive', 0, 0)
                        """,
                        (report_id, worker_id),
                    )
                return report_id

            first_report_id = add_report("2026-08-10")
            reimbursement_id = connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, status, created_by, created_at
                ) values (?, 'report-sync.pdf', 'report-sync.pdf', 'draft', ?,
                          '2026-08-15T00:00:00')
                """,
                (self.open_order_id, self.user_id),
            ).lastrowid
            initial_rows = self.module.customer_reimbursement_seed_rows(self.open_order_id)
            self.assertEqual(len(initial_rows), 3)
            initial_rows[0]["lodging"] = 75
            initial_rows[0] = self.module.calculate_customer_reimbursement_item(initial_rows[0], 0)
            self.module.save_customer_reimbursement_items(reimbursement_id, initial_rows)

            for day in ("2026-08-11", "2026-08-12", "2026-08-13"):
                add_report(day)
            connection.commit()

            self.module.update_customer_reimbursement_totals(reimbursement_id)
            rows = connection.execute(
                """
                select * from customer_reimbursement_items
                where customer_reimbursement_id = ?
                order by project_date, worker_name
                """,
                (reimbursement_id,),
            ).fetchall()
            self.assertEqual(len(rows), 12)
            self.assertEqual(len({row["project_date"] for row in rows}), 4)
            self.assertEqual(sum(1 for row in rows if row["source_report_id"]), 12)
            self.assertEqual(sum(float(row["lodging"] or 0) for row in rows), 75)

            connection.execute(
                "update service_reports set departure_time = '18:00' where id = ?",
                (first_report_id,),
            )
            self.module.update_customer_reimbursement_totals(reimbursement_id)
            updated = connection.execute(
                """
                select * from customer_reimbursement_items
                where customer_reimbursement_id = ? and source_report_id = ?
                """,
                (reimbursement_id, first_report_id),
            ).fetchall()
            self.assertEqual(len(updated), 3)
            self.assertTrue(all(row["standard_hours"] == 8 for row in updated))
            self.assertTrue(all(row["overtime_hours"] == 2 for row in updated))


if __name__ == "__main__":
    unittest.main()
