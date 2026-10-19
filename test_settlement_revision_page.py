"""Approved settlement UI must render the registered revision actions (invoice_test only)."""
import test_settlement_settled_amount as fixtures
import settlement_revisions


class RevisionPageTest(fixtures.SettlementSettledAmountTest):
    def client_for_settlement(self, reimbursement_id, status):
        with self.module.app.app_context():
            db = self.module.db()
            db.execute('update service_orders set start_date=? where id=?', (fixtures.DAY, self.order_id))
            db.execute('update customer_reimbursements set status=?, is_current=true where id=?', (status, reimbursement_id))
            db.commit()
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = self.uid
        return client

    def test_approval_redirect_renders_creation_action(self):
        reimbursement_id = self.make_settlement()
        client = self.client_for_settlement(reimbursement_id, 'submitted')
        response = client.post(f'/customer-reimbursements/{reimbursement_id}/approve', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'action="/customer-reimbursements/{reimbursement_id}/revision"', response.get_data(as_text=True))
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select status from customer_reimbursements where id=?', (reimbursement_id,)).fetchone()['status'], 'approved')

    def test_approved_revision_renders_promotion_action(self):
        reimbursement_id = self.make_settlement()
        client = self.client_for_settlement(reimbursement_id, 'approved')
        with self.module.app.test_request_context():
            self.module.g.user = {'id': self.uid, 'name': 'Settled Adj'}
            revision = settlement_revisions.create_customer_reimbursement_revision(self.api, reimbursement_id, 'Customer adjustment')
            revision_id = revision['id']
            self.module.db().execute("update customer_reimbursements set status='approved' where id=?", (revision_id,))
            self.module.db().commit()
        response = client.get(f'/service-orders/{self.order_id}/customer-reimbursement')
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'action="/customer-reimbursements/{revision_id}/promote"', response.get_data(as_text=True))
