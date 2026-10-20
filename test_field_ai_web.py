"""Focused web regressions; no database writes or shared pytest reset hooks."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from flask import Flask, g
from invoice_tool.field_ai.routes import register_field_ai_routes
from invoice_tool.field_ai.services import FieldAIService

class FieldAIWebTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(Path(__file__).parent / 'templates'))
        self.app.secret_key = 'unit-test-only'
        self.user = {'id': 42, 'role': 'employee'}
        self.allowed = True
        self.provider = Mock(return_value={'content': '回答 [1]'})
        self.service_patch = patch('invoice_tool.field_ai.routes.FieldAIService')
        self.service = self.service_patch.start().return_value
        self.addCleanup(self.service_patch.stop)
        self.service.visible_groups.return_value = []
        self.service.list_wechat.return_value = []
        self.service.gather_context.return_value = {'wechat': [], 'technical_knowledge': []}
        @self.app.before_request
        def load():
            g.user = self.user
        register_field_ai_routes(self.app, {'db': Mock(), 'login_required': lambda fn: fn,
            'has_menu_permission': lambda key: self.allowed,
            'deepseek_assistant_settings': lambda: {'api_key': 'test'},
            'call_deepseek_chat': self.provider})
        # Isolate this feature template from the full application shell.
        self.app.jinja_loader = __import__('jinja2').ChoiceLoader([
            __import__('jinja2').DictLoader({'base.html': '{% block content %}{% endblock %}'}), self.app.jinja_loader])
        self.client = self.app.test_client()

    def test_permission_blocks_get_and_post(self):
        self.allowed = False
        for method in (self.client.get, self.client.post):
            self.assertEqual(method('/field-ai').status_code, 403)
        self.service.list_wechat.assert_not_called()
        self.provider.assert_not_called()

    def test_template_filters_dates_and_escaping(self):
        self.service.list_wechat.return_value = [{'chat_name': '<script>x</script>', 'content': '<img>', 'message_time': '2026-10-20', 'sender_name': 'A'}]
        r = self.client.get('/field-ai?keyword=pump&sender=A&start=2026-10-01&end=2026-10-20')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'&lt;script&gt;', r.data)
        self.assertEqual(self.service.list_wechat.call_args.kwargs['end'], '2026-10-21')
        self.assertEqual(self.client.get('/field-ai?start=bad').status_code, 400)

    def test_csrf_and_empty_evidence_never_call_llm(self):
        self.assertEqual(self.client.post('/field-ai', data={'question': 'pump'}).status_code, 403)
        self.client.get('/field-ai')
        with self.client.session_transaction() as session:
            token = session['field_ai_csrf']
        self.assertEqual(self.client.post('/field-ai', data={'question': 'pump', 'csrf_token': token}).status_code, 200)
        self.provider.assert_not_called()

    def test_provider_only_receives_wechat_and_citations_render(self):
        self.service.gather_context.return_value = {'wechat': [{'chat_name': '授权群', 'message_time': '2026-10-20', 'sender_name': 'Alice', 'content': 'pump fault'}], 'technical_knowledge': [{'content': 'must-not-send'}]}
        self.client.get('/field-ai')
        with self.client.session_transaction() as session:
            token = session['field_ai_csrf']
        r = self.client.post('/field-ai', data={'question': 'pump', 'csrf_token': token})
        self.assertEqual(r.status_code, 200)
        payload = self.provider.call_args.args[0][1]['content']
        self.assertNotIn('must-not-send', payload)
        self.assertIn(b'Alice', r.data)
        self.assertFalse(self.provider.call_args.kwargs['include_tools'])

    def test_scope_and_parameter_order(self):
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = []
        service = FieldAIService(db=lambda: connection)
        with patch.object(service, 'authorized_order_ids', return_value={7}):
            service.list_wechat(self.user, keyword='pump', sender='Alice', group='x@chatroom', start='2026-10-01')
            sql, params = connection.execute.call_args.args
            self.assertEqual(params, ('%pump%', '%alice%', 'x@chatroom', '2026-10-01', 7, 50, 0))
            self.assertIn("r.chat_type='group'", sql)
            self.assertIn('m.enabled=true', sql)
        with patch.object(service, 'authorized_order_ids', return_value=set()):
            service.list_wechat(self.user)
            self.assertIn('1=0', connection.execute.call_args.args[0])
        for role in ('admin', 'manager', 'finance'):
            self.assertIsNone(service.authorized_order_ids({'id': 1, 'role': role}))

if __name__ == '__main__':
    unittest.main()
