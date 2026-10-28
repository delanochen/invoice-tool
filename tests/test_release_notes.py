"""Release documentation, filtering, permission and safe rendering regressions."""
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from flask import Flask, abort, g, redirect, request
from jinja2 import ChoiceLoader, DictLoader

from invoice_tool.app_settings.admin_routes import register_system_settings_routes
from invoice_tool.app_settings.release_notes import ROOT, load_release_notes, release_notes_context
from invoice_tool.authorization.policy_helpers import required_action_for_request


class ReleaseNotesTest(unittest.TestCase):
    def test_current_release_is_documented_and_versions_are_unique(self):
        records = load_release_notes()
        current = (ROOT / 'VERSION').read_text(encoding='utf-8-sig').strip()
        self.assertEqual(records[0]['version'], current)
        note = next(row for row in records if row['version'] == current)
        self.assertTrue(note['reason'].strip())
        self.assertTrue(all(change.strip() for change in note['changes']))
        self.assertEqual(len(records), len({row['version'] for row in records}))

    def test_search_pagination_and_invalid_page(self):
        records = load_release_notes()
        result = release_notes_context(records, '0.1.434', 'nonsense')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['releases'][0]['version'], '0.1.434')
        self.assertEqual(release_notes_context(records, 'no-such-release')['total'], 0)
        self.assertEqual(release_notes_context(records, '', -100)['page'], 1)
        result = release_notes_context(records, '', 99999)
        self.assertEqual(result['page'], result['pages'])
        self.assertLessEqual(len(result['releases']), 25)

    def test_empty_reason_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'notes.json'
            path.write_text(json.dumps([dict(version='1.0.0', reason='', changes=['change'])]))
            with self.assertRaises(ValueError):
                load_release_notes(path)

    def test_authenticated_route_permissions_and_html_escaping(self):
        app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        app.jinja_loader = ChoiceLoader([DictLoader({'base.html': '{% block content %}{% endblock %}'}), app.jinja_loader])
        def login_required(view):
            def wrapped():
                if not g.user:
                    return redirect('/login')
                return view()
            return wrapped
        from flask import render_template
        values = dict(login_required=login_required, admin_required=lambda view: view,
                      request=request, render_template=render_template)
        dependencies = {name: (lambda value=values.get(name.removeprefix('_cb_resolve_')): value)
                        for name in inspect.signature(register_system_settings_routes).parameters if name != 'app'}
        register_system_settings_routes(app, **dependencies)
        @app.before_request
        def authorize():
            role = request.headers.get('X-Test-Role')
            g.user = {'id': 1} if role else None
            if g.user and required_action_for_request(g=g, request=request) == ('system_settings', 'view') and role != 'admin':
                abort(403)
        @app.context_processor
        def version():
            return {'app_version': '0.1.435'}
        client = app.test_client()
        self.assertEqual(client.get('/settings/release-notes').status_code, 302)
        self.assertEqual(client.get('/settings/release-notes', headers={'X-Test-Role': 'employee'}).status_code, 403)
        response = client.get('/settings/release-notes?q=0.1.434', headers={'X-Test-Role': 'admin'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('交通工时', response.get_data(as_text=True))
        response = client.get('/settings/release-notes?q=<script>alert(1)</script>', headers={'X-Test-Role': 'admin'})
        self.assertNotIn('<script>alert(1)</script>', response.get_data(as_text=True))
        self.assertIn('&lt;script&gt;', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
