"""Workspace source persistence regressions, guarded PostgreSQL invoice_test only."""
from decimal import Decimal
from werkzeug.datastructures import MultiDict
import test_settlement_settled_amount as fixtures
from invoice_tool.customer_reimbursements.workspace_sources import save_workspace_sources


class WorkspacePostgreSQLTest(fixtures.SettlementSettledAmountTest):
    def workspace_save(self, reimbursement_id, item_id, amount):
        db = self.module.db()
        reimbursement = db.execute('select * from customer_reimbursements where id=?', (reimbursement_id,)).fetchone()
        order = db.execute('select * from service_orders where id=?', (self.order_id,)).fetchone()
        form = MultiDict({'workspace_sources_present': '1', 'expense_item_id': str(item_id),
                         f'settled_amount_{item_id}': amount, f'adjustment_reason_{item_id}': 'Customer share',
                         'lodging_person_nights': '1'})
        self.module.g.user = {'id': self.uid}
        self.assertTrue(save_workspace_sources(self.api, reimbursement, order, form))

    def test_workspace_partial_and_zero_reload_keep_employee_original(self):
        item_id = self.add_expense('EX-WORKSPACE', 'Parking Charge停车费', 373.99)
        reimbursement_id = self.make_settlement()
        self.save_selection(reimbursement_id, [item_id])
        for amount in ('7.47', '0'):
            with self.subTest(amount=amount), self.module.app.test_request_context():
                self.workspace_save(reimbursement_id, item_id, amount)
                self.module.db().commit()
            candidate = self.candidates(reimbursement_id)[item_id]
            self.assertEqual(Decimal(str(candidate['settled_amount'])), Decimal(amount))
            self.assertEqual(Decimal(str(candidate['amount'])), Decimal('373.99'))
            self.assertEqual(Decimal(str(self.linked_rows(reimbursement_id)[item_id]['amount'])), Decimal(amount))

    def test_workspace_frozen_travel_replaces_old_auto_snapshot(self):
        item_id = self.add_expense('EX-WORKSPACE-FROZEN', 'Parking Charge停车费', 373.99)
        reimbursement_id = self.make_settlement()
        self.save_selection(reimbursement_id, [item_id])
        with self.module.app.test_request_context():
            db = self.module.db()
            db.execute('update customer_reimbursements set sources_frozen=true where id=?', (reimbursement_id,))
            self.workspace_save(reimbursement_id, item_id, '7.47')
            row = {key: 0 for key in (
                'standard_hours', 'transport_hours', 'public_transport_hours', 'overtime_hours', 'holiday_hours',
                'standard_rate', 'transport_rate', 'public_transport_rate', 'overtime_rate', 'holiday_rate',
                'lodging', 'airfare', 'baggage', 'rental_car', 'fuel', 'parking', 'taxi', 'miles', 'mileage_rate', 'other')}
            row.update(worker_name='Settled Adj', project_date=fixtures.DAY, parking=373.99, auto_parking=373.99)
            rows = [self.module.calculate_customer_reimbursement_item(row)]
            # Form route order: normalize old display values, rebuild selected sources, then calculate totals.
            self.module.save_customer_reimbursement_items(reimbursement_id, rows)
            rows = self.module.merge_approved_expenses_into_customer_reimbursement(rows, self.order_id, None, reimbursement_id, 'manual_review')
            self.module.save_customer_reimbursement_items(reimbursement_id, rows)
            self.module.update_customer_reimbursement_totals(reimbursement_id, rows)
            db.commit()
            saved = self.module.customer_reimbursement_items(reimbursement_id)[0]
            actual = self.module.customer_reimbursement_item_expense_amount(saved, 'parking')
            self.assertEqual(Decimal(str(actual)), Decimal('7.47'))
            total = db.execute('select travel_total from customer_reimbursements where id=?', (reimbursement_id,)).fetchone()['travel_total']
            self.assertEqual(Decimal(str(total)), Decimal('7.47'))
