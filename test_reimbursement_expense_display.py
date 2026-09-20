"""工单结算单：报销来源金额的显示、已调整徽章、以及保存不被冲掉。

覆盖 v0.1.258 修复：
  1. 单元格显示「实际生效金额」——未调整时等于报销来源合计，而不是 0。
  2. 「已调整」徽章只在用户真的改过金额时出现，未改过时不出现。
  3. 用户改过的金额在保存（含 totals 重算、重进页面）后保持不变。
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent
MONEY_FIELDS = ("lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other")


class ReimbursementExpenseDisplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_reimb_display_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from customer_reimbursement_expense_links")
            connection.execute("delete from customer_reimbursement_items")
            connection.execute("delete from customer_reimbursements")
            connection.execute("delete from expense_items")
            connection.execute("delete from expenses")
            connection.execute("delete from service_orders where order_number = 'SO-DISPLAY'")
            connection.execute("delete from users where email = 'display-admin@example.com'")
            connection.execute("delete from projects where project_type = 'expense'")
            user_id = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('Display Admin', 'display-admin@example.com', 'unused', 'admin', '2026-09-15T12:00:00')
                """
            ).lastrowid
            order_id = connection.execute(
                """
                insert into service_orders (
                    order_number, client_name, site_address, client_order_number, start_date,
                    created_by, created_at
                ) values ('SO-DISPLAY', 'Test client', 'Test address', 'CLIENT-DISPLAY', '2026-09-15', ?, '2026-09-15T12:00:00')
                """,
                (user_id,),
            ).lastrowid
            lodging_project_id = connection.execute(
                """
                insert into projects (name, name_key, project_type, unit_price, is_active, created_at)
                values ('Accommodation/Lodging', 'accommodation/lodging', 'expense', 0, 1, '2026-09-15T12:00:00')
                """
            ).lastrowid
            expense_id = connection.execute(
                """
                insert into expenses (
                    service_order_id, expense_number, project, expense_date, amount, status,
                    created_by, beneficiary_id, created_at, updated_at
                ) values (?, 'EXP-LODGE', 'Accommodation/Lodging', '2026-09-15', 300, 'approved', ?, ?,
                          '2026-09-15T12:00:00', '2026-09-15T12:00:00')
                """,
                (order_id, user_id, user_id),
            ).lastrowid
            connection.execute(
                """
                insert into expense_items (expense_id, project_id, project, amount, sort_order)
                values (?, ?, 'Accommodation/Lodging', 300, 0)
                """,
                (expense_id, lodging_project_id),
            )
            reimbursement_id = connection.execute(
                """
                insert into customer_reimbursements (
                    service_order_id, file_name, stored_filename, status, expense_selection_mode,
                    created_by, created_at
                ) values (?, 'settlement.pdf', 'settlement.pdf', 'draft', 'legacy', ?, '2026-09-15T12:00:00')
                """,
                (order_id, user_id),
            ).lastrowid
            # 与该报销同姓名同日期的一行结算明细，来源金额应落到 lodging
            connection.execute(
                """
                insert into customer_reimbursement_items (
                    customer_reimbursement_id, worker_name, project_date,
                    standard_hours, standard_rate, labor_total, total, sort_order
                ) values (?, 'Display Admin', '2026-09-15', 0, 0, 0, 0, 0)
                """,
                (reimbursement_id,),
            )
            connection.commit()
            self.order_id = order_id
            self.user_id = user_id
            self.reimbursement_id = reimbursement_id

    def _item(self):
        with self.module.app.app_context():
            return dict(self.module.customer_reimbursement_items(self.reimbursement_id)[0])

    def _effective(self, row, field_name):
        with self.module.app.app_context():
            return float(self.module.customer_reimbursement_item_expense_amount(row, field_name))

    def _recalc(self, rows=None):
        """重算结算合计并提交。

        每个 app context 拿到的是独立 SQLite 连接，测试读取时必须 commit，
        否则后续 context 看不到这次写入。
        """
        with self.module.app.app_context():
            totals = self.module.update_customer_reimbursement_totals(self.reimbursement_id, rows)
            self.module.db().commit()
            return totals

    # ---- 1. 未调整时，生效金额等于报销来源金额，不是 0 ----
    def test_untouched_cell_uses_auto_transferred_amount(self):
        self._recalc()
        item = self._item()
        self.assertEqual(item["auto_lodging"], 300)
        self.assertEqual(item["lodging"], 0, "未调整时人工列为 0，代表沿用来源金额")
        self.assertEqual(self._effective(item, "lodging"), 300, "单元格生效金额应为来源金额")

    # ---- 2. 未调整时不应出现「已调整」徽章 ----
    def test_untouched_cell_has_no_adjusted_badge(self):
        self._recalc()
        item = self._item()
        # 模板判定：manual_amount and manual_amount != auto_amount
        manual = item["lodging"] or 0
        is_adjusted = bool(manual) and manual != item["auto_lodging"]
        self.assertFalse(is_adjusted, "未调整的单元格不应显示已调整")

    # ---- 3. 手改后徽章出现 ----
    def test_edited_cell_shows_adjusted_badge(self):
        self._recalc()
        item = self._item()
        item["lodging"] = 250.0
        manual = item["lodging"] or 0
        is_adjusted = bool(manual) and manual != item["auto_lodging"]
        self.assertTrue(is_adjusted)

    # ---- 4. 关键回归：手改值保存后不被来源金额冲掉 ----
    def test_manual_override_survives_save_and_recalc(self):
        self._recalc()
        item = self._item()
        item["lodging"] = 250.0           # 用户手改
        item.pop("auto_expense_sources", None)
        self._recalc([item])
        after = self._item()
        self.assertEqual(after["lodging"], 250.0, "用户手改的 250 必须保留")
        self.assertEqual(after["auto_lodging"], 300, "来源金额仍应记录为 300")
        self.assertEqual(
            self._effective(after, "lodging"),
            250.0,
            "生效金额应为手改后的 250，不能与来源相加",
        )

    # ---- 5. 表单回传 auto_* 后，totals 重算仍保留手改值 ----
    def test_form_roundtrip_preserves_manual_override(self):
        self._recalc()
        with self.module.app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = self.user_id
            with self.module.app.app_context():
                db = self.module.db()
                db.execute("update customer_reimbursement_items set lodging = 250 where customer_reimbursement_id = ?",
                           (self.reimbursement_id,))
                db.commit()
            # 保存（POST）→ 后端 from_form 读到 lodging=250 与 echo 回来的 auto_lodging=300
            response = client.post(
                f"/service-orders/{self.order_id}/customer-reimbursement",
                data={
                    "action": "save",
                    "worker_name": ["Display Admin"],
                    "project_date": ["2026-09-15"],
                    "standard_hours": ["0"],
                    "transport_hours": ["0"],
                    "public_transport_hours": ["0"],
                    "overtime_hours": ["0"],
                    "holiday_hours": ["0"],
                    "lodging": ["250"],
                    "auto_lodging": ["300"],
                    "airfare": ["0"], "auto_airfare": ["0"],
                    "baggage": ["0"], "auto_baggage": ["0"],
                    "rental_car": ["0"], "auto_rental_car": ["0"],
                    "fuel": ["0"], "auto_fuel": ["0"],
                    "parking": ["0"], "auto_parking": ["0"],
                    "taxi": ["0"], "auto_taxi": ["0"],
                    "miles": ["0"],
                    "other": ["0"], "auto_other": ["0"],
                    "source_report_id": [""],
                    "source_worker_user_id": [""],
                },
                follow_redirects=False,
            )
            self.assertIn(response.status_code, (302, 200))
            after = self._item()
            self.assertEqual(after["lodging"], 250, "保存后手改值不应被冲掉")
            self.assertEqual(after["auto_lodging"], 300)

    # ---- 6. 结算合计只计一次，不重复累加来源 ----
    def test_totals_do_not_double_count_sources(self):
        totals = self._recalc()
        self.assertEqual(totals["lodging_total"], 300, "来源 300 只能计一次")
        self.assertEqual(totals["total_amount"], 300)
        with self.module.app.app_context():
            again = self.module.customer_reimbursement_totals(
                self.module.customer_reimbursement_items(self.reimbursement_id)
            )
            self.assertEqual(again["lodging_total"], 300, "重复读取不应改变结果")
            self.assertEqual(again["total_amount"], 300)

    # ---- 7. 未调整 + 有来源：页面渲染的 input value 即为来源金额 ----
    def test_rendered_input_shows_source_amount(self):
        self._recalc()
        with self.module.app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = self.user_id
            response = client.get(f"/service-orders/{self.order_id}/customer-reimbursement")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertNotIn("已调整", html, "未调整时不应渲染已调整徽章")
            self.assertIn('name="auto_lodging" value="300.0"', html)
            self.assertIn('name="lodging" type="number" step="0.01" min="0" value="300.0"', html,
                          "住宿单元格应显示来源金额 300")

    # ---- 8. 已调整时页面显示手改值 + 徽章，且隐藏域仍回传来源金额 ----
    def test_rendered_input_shows_manual_value_with_badge(self):
        self._recalc()
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update customer_reimbursement_items set lodging = 250 where customer_reimbursement_id = ?",
                       (self.reimbursement_id,))
            db.commit()
        with self.module.app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = self.user_id
            response = client.get(f"/service-orders/{self.order_id}/customer-reimbursement")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("已调整", html, "手改过应渲染已调整徽章")
            self.assertIn('value="300.0"', html, "隐藏域仍应回传来源金额")
            self.assertIn('name="lodging" type="number" step="0.01" min="0" value="250.0"', html,
                          "住宿单元格应显示手改后的 250")

    # ---- 9. manual_review（选择报销来源）路径同样保持手改值 ----
    def test_manual_review_mode_preserves_override(self):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute("update customer_reimbursements set expense_selection_mode = 'manual_review' where id = ?",
                       (self.reimbursement_id,))
            db.execute(
                """
                insert into customer_reimbursement_expense_links (
                    customer_reimbursement_id, expense_item_id, amount_snapshot,
                    project_snapshot, expense_status_snapshot, selected_by, selected_at
                ) values (?, (select id from expense_items limit 1), 300, 'Accommodation/Lodging',
                          'approved', ?, '2026-09-15T12:00:00')
                """,
                (self.reimbursement_id, self.user_id),
            )
            db.commit()
        totals = self._recalc()
        self.assertEqual(totals["lodging_total"], 300, "manual_review 下来源金额应生效")
        item = self._item()
        self.assertEqual(item["auto_lodging"], 300)
        item["lodging"] = 250.0
        item.pop("auto_expense_sources", None)
        self._recalc([item])
        after = self._item()
        self.assertEqual(after["lodging"], 250.0, "manual_review 下保存后手改值不应被冲掉")
        self.assertEqual(self._effective(after, "lodging"), 250.0)


if __name__ == "__main__":
    unittest.main()
