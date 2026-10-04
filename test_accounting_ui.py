import unittest
from datetime import date

import app as app_module
from invoice_tool.accounting import (
    EventKey,
    InvoiceRecognitionService,
    PostingLine,
    PostingService,
)


class AccountingVoucherUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, SECRET_KEY="accounting-ui-test")
        with cls.app.app_context():
            connection = app_module.db()
            connection.execute(
                "insert into settings(key,value) values('accounting_base_enabled','1') "
                "on conflict(key) do update set value='1'"
            )
            connection.executemany(
                "insert into accounts(account_code,account_name,account_type,normal_balance) "
                "values(?,?,?,?)",
                (("1000", "Bank", "asset", "debit"),
                 ("1100", "Accounts Receivable", "asset", "debit"),
                 ("2100", "Sales Tax Payable", "liability", "credit"),
                 ("3000", "Opening Balance Equity", "equity", "credit"),
                 ("4000", "Revenue", "revenue", "credit")),
            )
            cls.bank_account_id = connection.execute(
                "select id from accounts where account_code='1000'"
            ).fetchone()[0]
            connection.execute(
                "insert into accounting_periods(period_start,period_end,status) "
                "values('2026-10-01','2026-10-31','open')"
            )
            cls.admin_id = connection.execute(
                "insert into users(name,email,password_hash,role,created_at) "
                "values('Voucher Admin','voucher-admin@example.invalid','x','admin',"
                "CURRENT_TIMESTAMP::text)"
            ).lastrowid
            cls.employee_id = connection.execute(
                "insert into users(name,email,password_hash,role,created_at) "
                "values('Voucher Employee','voucher-employee@example.invalid','x','employee',"
                "CURRENT_TIMESTAMP::text)"
            ).lastrowid
            customer_id = connection.execute(
                "insert into clients(client_number,name,short_name,created_at) "
                "values('C-UI-CORR','Correction UI Client','CORR',CURRENT_TIMESTAMP::text)"
            ).lastrowid
            project_id = connection.execute(
                "insert into projects(name,created_at) values('Correction UI Project',"
                "CURRENT_TIMESTAMP::text)"
            ).lastrowid
            cls.invoice_id = connection.execute(
                "insert into invoices(invoice_number,client_id,issue_date,due_date,currency,"
                "status,created_by,created_at) values('INV-UI-CORR',?,'2026-10-03',"
                "'2026-10-31','USD','completed',?,CURRENT_TIMESTAMP::text)",
                (customer_id, cls.admin_id),
            ).lastrowid
            connection.execute(
                "insert into invoice_items(invoice_id,project_id,description,amount,tax_rate) "
                "values(?,?, 'Correction UI service',100,0)",
                (cls.invoice_id, project_id),
            )
            invoice_service = InvoiceRecognitionService(connection)
            invoice_service.recognize(
                cls.invoice_id, source_version=1, accounting_date=date(2026, 10, 3),
                actor_id=cls.admin_id,
            )
            invoice_service.correct(
                cls.invoice_id, revenue_delta="25.00", sales_tax_delta="0",
                accounting_date=date(2026, 10, 4), reason_code="UI acceptance",
                actor_id=cls.admin_id,
            )
            result = PostingService(connection).post_event(
                key=EventKey("ui_test", 501, 1, "invoice.confirmed"),
                business_anchor_type="ui_test", business_anchor_id=501,
                business_date=date(2026, 10, 4), accounting_date=date(2026, 10, 4),
                lines=(PostingLine("1000", debit="125.00", memo="UI test debit"),
                       PostingLine("3000", credit="125.00", memo="UI test credit")),
                description="Voucher UI acceptance", actor_id=cls.admin_id,
            )
            cls.voucher_id = result.voucher_id
            connection.commit()

    def login_as(self, client, user_id):
        with client.session_transaction() as session:
            session["user_id"] = user_id

    def test_admin_can_view_list_and_detail(self):
        client = self.app.test_client()
        self.login_as(client, self.admin_id)
        listing = client.get("/finance/accounting/vouchers")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("会计凭证", listing.get_data(as_text=True))
        self.assertIn("Voucher UI acceptance", listing.get_data(as_text=True))
        detail = client.get(f"/finance/accounting/vouchers/{self.voucher_id}")
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        self.assertIn("会计分录", html)
        self.assertIn("UI test debit", html)
        self.assertIn("125.00", html)
        receipts = client.get("/finance/accounting/receipts")
        self.assertEqual(receipts.status_code, 200)
        self.assertIn("客户收款与预收", receipts.get_data(as_text=True))
        periods = client.get("/finance/accounting/periods")
        self.assertEqual(periods.status_code, 200)
        self.assertIn("会计期间", periods.get_data(as_text=True))
        openings = client.get("/finance/accounting/opening-balances")
        self.assertEqual(openings.status_code, 200)
        self.assertIn("期初建账", openings.get_data(as_text=True))
        trial_balance = client.get("/finance/accounting/trial-balance")
        self.assertEqual(trial_balance.status_code, 200)
        trial_html = trial_balance.get_data(as_text=True)
        self.assertIn("试算平衡表", trial_html)
        self.assertIn("借贷平衡", trial_html)
        ledger = client.get(
            f"/finance/accounting/accounts/{self.bank_account_id}/ledger"
        )
        self.assertEqual(ledger.status_code, 200)
        self.assertIn("UI test debit", ledger.get_data(as_text=True))
        correction = client.get(
            f"/finance/accounting/invoices/{self.invoice_id}/correct"
        )
        self.assertEqual(correction.status_code, 200)
        correction_html = correction.get_data(as_text=True)
        self.assertIn("发票会计更正", correction_html)
        self.assertIn("UI acceptance", correction_html)
        self.assertIn("125.00", correction_html)
        employee_client = self.app.test_client()
        self.login_as(employee_client, self.employee_id)
        self.assertEqual(
            employee_client.get("/finance/accounting/vouchers").status_code, 403
        )
        self.assertEqual(
            employee_client.get("/finance/accounting/receipts").status_code, 403
        )
        self.assertEqual(
            employee_client.get("/finance/accounting/periods").status_code, 403
        )
        self.assertEqual(
            employee_client.get("/finance/accounting/opening-balances").status_code, 403
        )
        self.assertEqual(
            employee_client.get("/finance/accounting/trial-balance").status_code, 403
        )
        self.assertEqual(
            employee_client.get(
                f"/finance/accounting/invoices/{self.invoice_id}/correct"
            ).status_code,
            403,
        )


if __name__ == "__main__":
    unittest.main()
