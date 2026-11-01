"""Expense thumbnails stay small, authorized and separate from original previews."""
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from flask import Flask, abort, send_file
from jinja2 import Environment
from PIL import Image

from invoice_tool.attachments.thumbnails import attachment_thumbnail_response
from invoice_tool.employee_expenses.expense_attachment_routes import register_expense_attachment_routes

ROOT = Path(__file__).resolve().parents[1]


class ExpenseThumbnailsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'receipt.jpg'
        Image.new('RGB', (4284, 5712), '#e4c19b').save(self.path, quality=90)
        self.original = self.path.read_bytes()
        self.app = Flask(__name__)
        self.app.testing = True
        self.connection = Mock()
        self.connection.execute.return_value.fetchone.return_value = dict(id=167, expense_id=46, original_filename='receipt.jpg')
        self.allowed = True
        def require_expense(expense_id):
            self.assertEqual(expense_id, 46)
            if not self.allowed:
                abort(403)
        register_expense_attachment_routes(self.app,
            resolve_EXPENSE_ATTACHMENTS_DIR=lambda: self.temp.name,
            db=lambda: self.connection, expense_attachment_path=lambda attachment: self.path,
            login_required=lambda view: view, require_expense=require_expense,
            run_expense_attachment_interpretation=Mock(),
            safe_attachment_response=lambda path, name: send_file(path, mimetype='image/jpeg'))
        self.client = self.app.test_client()

    def test_large_original_becomes_small_thumbnail_and_is_unchanged(self):
        response = self.client.get('/expense-attachments/167?thumbnail=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        with Image.open(BytesIO(response.data)) as image:
            self.assertLessEqual(max(image.size), 160)
        self.assertLess(len(response.data), len(self.original) / 20)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertIn('private', response.headers['Cache-Control'])
        self.assertIn('Cookie', response.headers['Vary'])

    def test_browser_cache_avoids_decoding_but_not_authorization(self):
        first = self.client.get('/expense-attachments/167?thumbnail=1')
        headers = {'If-None-Match': first.headers['ETag']}
        with patch('invoice_tool.attachments.thumbnails.Image.open') as opened:
            response = self.client.get('/expense-attachments/167?thumbnail=1', headers=headers)
        self.assertEqual(response.status_code, 304)
        opened.assert_not_called()
        self.allowed = False
        self.assertEqual(self.client.get('/expense-attachments/167?thumbnail=1', headers=headers).status_code, 403)

    def test_full_preview_and_download_still_return_original(self):
        for url in ('/expense-attachments/167', '/expense-attachments/167/download'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, self.original)
                response.close()

    def test_non_image_never_falls_back_to_sending_original(self):
        self.path.write_bytes(b'%PDF-1.7 fixture')
        self.assertEqual(self.client.get('/expense-attachments/167?thumbnail=1').status_code, 404)


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(); self.images=[]; self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag == 'img': self.images.append(dict(attrs))
        if tag == 'a': self.links.append(dict(attrs))


class ExpenseThumbnailMarkupTest(unittest.TestCase):
    def render_macro(self, filename, macro_name, **values):
        source = (ROOT / 'templates' / filename).read_text(encoding='utf-8')
        start = source.index('{% macro ' + macro_name)
        end = source.index('{% endmacro %}', start) + len('{% endmacro %}')
        snippet = source[start:end]
        env = Environment(autoescape=True)
        env.globals.update(is_image_attachment=lambda *args: True,
            interpretation_controls=lambda attachment: '',
            url_for=lambda endpoint, attachment_id, **query: f'/{endpoint}/{attachment_id}' + ('?' + urlencode(query) if query else ''))
        module = env.from_string(snippet).make_module()
        html = str(getattr(module, macro_name)(**values))
        parsed=Markup();parsed.feed(html)
        return parsed

    def test_detail_and_edit_use_lazy_thumbnails_but_preview_original(self):
        attachment=dict(id=167, content_type='image/jpeg', original_filename='receipt.jpg')
        for filename, name in [('expense_detail.html','attachment_list'), ('expense_form.html','attachment_cards')]:
            with self.subTest(filename=filename):
                markup=self.render_macro(filename,name,attachment_list=[attachment])
                self.assertEqual(len(markup.images),1)
                self.assertTrue(markup.images[0]['src'].endswith('?thumbnail=1'))
                self.assertEqual(markup.images[0]['loading'],'lazy')
                self.assertEqual(markup.images[0]['decoding'],'async')
                self.assertTrue(all('thumbnail=' not in link['href'] for link in markup.links if 'data-image-preview' in link))

    def test_duplicate_comparison_uses_thumbnail_only_for_image_source(self):
        markup=self.render_macro('expense_detail.html','suspect_attachment',href='/expense-attachments/167',name='receipt.jpg',content_type='image/jpeg')
        self.assertEqual(markup.images[0]['src'],'/expense-attachments/167?thumbnail=1')
        self.assertEqual(markup.links[0]['href'],'/expense-attachments/167')


if __name__ == '__main__':
    unittest.main()
