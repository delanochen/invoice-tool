"""Attachment-heavy settlement lists must not request image originals on entry."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]


class AttachmentMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.links = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'img':
            self.images.append(attrs)
        elif tag == 'a':
            self.links.append(attrs)
        elif tag == 'button':
            self.buttons.append(attrs)


def render_attachment_list(attachments, can_edit=False):
    source = (ROOT / 'templates/customer_reimbursement_form.html').read_text(encoding='utf-8')
    start = source.index('<table class="list-table attachment-table">')
    end = source.index('</table>', start) + len('</table>')
    env = Environment(autoescape=True)
    env.filters['local_datetime'] = lambda value: value
    template = env.from_string(source[start:end])
    html = template.render(attachments=attachments, can_edit=can_edit,
        is_customer_reimbursement_image_attachment=lambda item: item['original_filename'].endswith('.png'),
        url_for=lambda endpoint, attachment_id: f'/{endpoint}/{attachment_id}')
    parsed = AttachmentMarkup()
    parsed.feed(html)
    return html, parsed


class SettlementAttachmentLoadingTest(unittest.TestCase):
    def test_164_attachments_do_not_load_147_original_images(self):
        attachments = [dict(id=index, original_filename=f'proof-{index}.png' if index < 147 else f'receipt-{index}.pdf',
                            uploader_name='Test', uploaded_at='2026-10-28') for index in range(164)]
        for can_edit in (False, True):
            with self.subTest(can_edit=can_edit):
                html, parsed = render_attachment_list(attachments, can_edit)
                self.assertEqual(parsed.images, [])
                image_links = [link for link in parsed.links if 'data-image-preview' in link]
                self.assertEqual(len(image_links), 147)
                self.assertEqual(len([link for link in parsed.links if 'data-attachment-download' in link]), 164)
                self.assertEqual(len([link for link in parsed.links if 'data-document-preview' in link]), 17)
                self.assertEqual(image_links[0]['href'], '/preview_customer_reimbursement_attachment/0')
                self.assertEqual(len(parsed.buttons), 164 if can_edit else 0)

    def test_empty_and_escaped_attachment_names(self):
        html, parsed = render_attachment_list([])
        self.assertIn('还没有附件', html)
        self.assertEqual(parsed.images, [])
        html, parsed = render_attachment_list([dict(id=1, original_filename='<unsafe>.png', uploader_name='', uploaded_at='')])
        self.assertIn('&lt;unsafe&gt;.png', html)
        self.assertEqual(parsed.links[0]['data-preview-name'], '<unsafe>.png')


if __name__ == '__main__':
    unittest.main()
