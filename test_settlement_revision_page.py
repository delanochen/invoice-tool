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
        page = response.get_data(as_text=True)
        self.assertIn(f'action="/customer-reimbursements/{reimbursement_id}/revision"', page)
        self.assertIn("document.body.appendChild(d); d.showModal()", page)
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
        response = client.get(f'/service-orders/{self.order_id}/customer-reimbursement', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn(f'action="/customer-reimbursements/{revision_id}/promote"', page)
        self.assertIn(f'name="version_id" value="{revision_id}"', page)
        self.assertIn('data-grid-no-calc', page)

    def test_pending_revision_is_default_but_current_version_remains_viewable(self):
        reimbursement_id = self.make_settlement()
        client = self.client_for_settlement(reimbursement_id, 'approved')
        with self.module.app.test_request_context():
            self.module.g.user = {'id': self.uid, 'name': 'Settled Adj'}
            revision = settlement_revisions.create_customer_reimbursement_revision(self.api, reimbursement_id, 'Customer adjustment')
            revision_id = revision['id']
            self.module.db().commit()

        response = client.get(f'/service-orders/{self.order_id}/customer-reimbursement')
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'version_id={revision_id}', response.headers['Location'])

        revision_page = client.get(response.headers['Location'])
        self.assertEqual(revision_page.status_code, 200)
        revision_html = revision_page.get_data(as_text=True)
        self.assertIn(f'name="version_id" value="{revision_id}"', revision_html)
        self.assertIn('此版本可继续编辑', revision_html)

        current_page = client.get(f'/service-orders/{self.order_id}/customer-reimbursement?version_id={reimbursement_id}')
        self.assertEqual(current_page.status_code, 200)
        current_html = current_page.get_data(as_text=True)
        self.assertIn(f'name="version_id" value="{reimbursement_id}"', current_html)
        self.assertIn('当前正式生效版本', current_html)
