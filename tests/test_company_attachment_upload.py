"""Exercise attachment-only posts without importing the database application."""
import inspect
import unittest
from unittest.mock import Mock

from flask import Flask, abort, redirect, request, url_for

from invoice_tool.company_profile.routes import register_company_page_routes


class CompanyAttachmentUploadTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.testing = True
        self.values = {
            name.removeprefix('_cb_resolve_'): Mock()
            for name in inspect.signature(register_company_page_routes).parameters
            if name.startswith('_cb_resolve_')
        }
        self.database = Mock()
        self.values.update(
            login_required=lambda fn: fn,
            is_internal_user=lambda: True,
            can_manage_company_info=Mock(return_value=True),
            request=request, abort=abort, redirect=redirect, url_for=url_for,
            db=lambda: self.database,
            uploaded_attachments_from_request=lambda: ['synthetic-file'],
            COMPANY_ATTACHMENT_DIR='test-only',
        )
        register_company_page_routes(self.app, **{
            '_cb_resolve_' + key: (lambda value=value: value)
            for key, value in self.values.items()
        })

    def test_upload_does_not_write_company_settings(self):
        response = self.app.test_client().post('/company-info', data={'action': 'upload_attachments'})
        self.assertEqual(response.location, '/company-info?panel=files')
        self.values['save_named_attachment'].assert_called_once_with(
            'synthetic-file', 'test-only', 'company_attachments')
        self.values['set_setting'].assert_not_called()
        self.database.commit.assert_called_once()

    def test_invalid_upload_rolls_back(self):
        self.values['save_named_attachment'].side_effect = ValueError('invalid')
        response = self.app.test_client().post('/company-info', data={'action': 'upload_attachments'})
        self.assertEqual(response.location, '/company-info?panel=files')
        self.database.rollback.assert_called_once()
        self.database.commit.assert_not_called()
        self.values['set_setting'].assert_not_called()

    def test_read_only_user_cannot_upload(self):
        self.values['can_manage_company_info'].return_value = False
        response = self.app.test_client().post('/company-info', data={'action': 'upload_attachments'})
        self.assertEqual(response.status_code, 403)
        self.values['save_named_attachment'].assert_not_called()
        self.database.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
