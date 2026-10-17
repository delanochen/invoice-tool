"""Behavioral regression coverage for the contract extraction."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app as module
from flask import abort, url_for


class ContractsModuleContractTest(unittest.TestCase):
    def setUp(self):
        module.app.config.update(TESTING=True, SECRET_KEY='contract-tests')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_patch = patch.object(module, 'CONTRACT_ATTACHMENTS_DIR', self.temp.name)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        with module.app.app_context():
            connection = module.db()
            self.user_id = connection.execute(
                "insert into users (name,email,password_hash,role,is_active,created_at) values ('Contract admin','contract-admin@example.test','unused','admin',1,?)",
                (module.now(),),
            ).lastrowid
            self.client_id = connection.execute(
                "insert into clients (client_number,name,short_name,created_at) values ('CT-TEST','Contract client','CT Test',?)",
                (module.now(),),
            ).lastrowid
            connection.commit()
        self.http = module.app.test_client()
        with self.http.session_transaction() as session:
            session['user_id'] = self.user_id

    def form(self, **extra):
        values = dict(client_id=str(self.client_id), contract_type='framework', title='Test contract', status='draft', currency='USD')
        values.update(extra)
        return values

    def create(self, **extra):
        response = self.http.post('/contracts/new', data=self.form(**extra))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/contracts/', response.location)
        with module.app.app_context():
            return module.db().execute('select id from contracts where client_id = ?', (self.client_id,)).fetchone()['id']

    def test_routes_keep_urls_methods_and_legacy_exports(self):
        expected = {
            'contracts': ('/contracts', {'GET'}, {}),
            'new_contract': ('/contracts/new', {'GET', 'POST'}, {}),
            'contract_detail': ('/contracts/17', {'GET'}, {'contract_id':17}),
            'edit_contract': ('/contracts/17/edit', {'GET','POST'}, {'contract_id':17}),
            'delete_contract': ('/contracts/17/delete', {'POST'}, {'contract_id':17}),
            'preview_contract_attachment': ('/contract-attachments/17', {'GET'}, {'attachment_id':17}),
            'download_contract_attachment': ('/contract-attachments/17/download', {'GET'}, {'attachment_id':17}),
            'delete_contract_attachment': ('/contract-attachments/17/delete', {'POST'}, {'attachment_id':17}),
        }
        for name, (url, methods, values) in expected.items():
            rules = list(module.app.url_map.iter_rules(name))
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].methods - {'HEAD','OPTIONS'}, methods)
            self.assertIs(getattr(module, name), module.app.view_functions[name])
            self.assertEqual(getattr(module, name).__module__, 'invoice_tool.contracts.routes')
            with module.app.test_request_context():
                self.assertEqual(url_for(name, **values), url)

    def test_create_read_edit_delete_and_commercial_document(self):
        identifier = self.create()
        for url in ('/contracts', f'/contracts/{identifier}', f'/contracts/{identifier}/edit'):
            response = self.http.get(url)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:100])
        with module.app.app_context():
            row = module.db().execute("select * from commercial_documents where doc_type = 'contract' and doc_ref_id = ?", (identifier,)).fetchone()
            self.assertIsNotNone(row)
        self.assertEqual(self.http.post(f'/contracts/{identifier}/edit', data=self.form(title='Updated')).status_code, 302)
        with module.app.app_context():
            self.assertEqual(module.db().execute('select title from contracts where id = ?', (identifier,)).fetchone()['title'], 'Updated')
        self.assertEqual(self.http.post(f'/contracts/{identifier}/delete').status_code, 302)
        with module.app.app_context():
            self.assertIsNone(module.db().execute('select id from contracts where id = ?', (identifier,)).fetchone())

    def test_attachment_upload_preview_download_and_delete_uses_current_root(self):
        identifier = self.create(attachments=(io.BytesIO(b'%PDF-1.7\nTest'), 'terms.pdf'))
        with module.app.app_context():
            attachment = module.db().execute('select * from contract_attachments where contract_id = ?', (identifier,)).fetchone()
            path = Path(module.contract_attachment_path(attachment))
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(Path(self.temp.name)))
        aid = attachment['id']
        preview = self.http.get(f'/contract-attachments/{aid}')
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, 'application/pdf')
        preview.close()
        download = self.http.get(f'/contract-attachments/{aid}/download')
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download.headers['Content-Disposition'])
        download.close()
        self.assertEqual(self.http.post(f'/contract-attachments/{aid}/delete').status_code, 302)
        self.assertFalse(path.exists())
        self.assertEqual(self.http.get(f'/contract-attachments/{aid}').status_code, 404)

    def test_project_validation_rolls_back_and_dynamic_number_helper_is_used(self):
        with patch.object(module, 'next_contract_number', return_value='CT-DYNAMIC'):
            identifier = self.create()
        with module.app.app_context():
            self.assertEqual(module.db().execute('select contract_number from contracts where id = ?', (identifier,)).fetchone()['contract_number'], 'CT-DYNAMIC')
        response = self.http.post('/contracts/new', data=self.form(contract_type='project', title='Invalid'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('项目合同必须填写项目名称', response.get_data(as_text=True))
        with module.app.app_context():
            self.assertEqual(module.db().execute('select count(*) as count from contracts where client_id = ?', (self.client_id,)).fetchone()['count'], 1)

    def test_view_manage_and_attachment_access_checks_are_preserved(self):
        identifier = self.create()
        with patch.object(module, 'can_view_contracts', return_value=False):
            self.assertEqual(self.http.get('/contracts').status_code, 403)
        with patch.object(module, 'can_manage_contracts', return_value=False):
            self.assertEqual(self.http.get('/contracts/new').status_code, 403)
            self.assertEqual(self.http.post(f'/contracts/{identifier}/delete').status_code, 403)
        self.http.get('/logout')
        self.assertEqual(self.http.get(f'/contracts/{identifier}').status_code, 302)

    def test_linked_order_blocks_contract_deletion(self):
        identifier = self.create()
        with module.app.app_context():
            connection = module.db()
            connection.execute("insert into service_orders (order_number,client_id,client_name,site_address,client_order_number,status,contract_id,created_by,created_at) values ('SO-CT-TEST',?,'Test','Site','CT-TEST','open',?,?,?)", (self.client_id, identifier, self.user_id, module.now()))
            connection.commit()
        self.assertEqual(self.http.post(f'/contracts/{identifier}/delete').status_code, 302)
        with module.app.app_context():
            self.assertIsNotNone(module.db().execute('select id from contracts where id = ?', (identifier,)).fetchone())

    def test_attachment_endpoints_require_parent_contract_access(self):
        identifier = self.create(attachments=(io.BytesIO(b'%PDF-1.7\nTest'), 'private.pdf'))
        with module.app.app_context():
            attachment = module.db().execute('select * from contract_attachments where contract_id = ?', (identifier,)).fetchone()
            path = Path(module.contract_attachment_path(attachment))
        with patch.object(module, 'require_contract_access', side_effect=lambda cid: abort(403)) as guard:
            aid = attachment['id']
            self.assertEqual(self.http.get(f'/contract-attachments/{aid}').status_code, 403)
            self.assertEqual(self.http.get(f'/contract-attachments/{aid}/download').status_code, 403)
            self.assertEqual(self.http.post(f'/contract-attachments/{aid}/delete').status_code, 403)
            self.assertEqual(guard.call_count, 3)
            guard.assert_called_with(identifier)
        self.assertTrue(path.is_file())
