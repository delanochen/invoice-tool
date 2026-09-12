import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parent


class AttachmentSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location(
            "attachment_security_test",
            source,
        )
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(
            os.environ,
            {
                "ADMIN_EMAIL": "attachment-admin@example.test",
                "ADMIN_PASSWORD": "attachment-test-password",
                "APP_VERSION": "0.0.0",
                "REQUIRE_DATA_DIRECTORY_IDENTITY": "0",
            },
            clear=False,
        ):
            spec.loader.exec_module(self.module)
        self.module.app.config.update(TESTING=True, SECRET_KEY="test")
        self.files = Path(self.temp.name) / "attachments"
        self.files.mkdir()

    def response_for(self, name, content):
        path = self.files / name
        path.write_bytes(content)
        with self.module.app.test_request_context("/attachment-preview"):
            response = self.module.safe_attachment_response(path, name)
            response = self.module.prevent_stale_business_pages(response)
            response.close()
            return response

    @staticmethod
    def image_bytes(image_format):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "green").save(buffer, format=image_format)
        return buffer.getvalue()

    def test_html_disguised_as_jpg_is_downloaded_as_octet_stream(self):
        response = self.response_for("evil.jpg", b"<html><script>alert(1)</script></html>")
        self.assertEqual(response.mimetype, "application/octet-stream")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertNotEqual(response.mimetype, "text/html")

    def test_html_disguised_as_pdf_is_downloaded_as_octet_stream(self):
        response = self.response_for("evil.pdf", b"<html><script>alert(1)</script></html>")
        self.assertEqual(response.mimetype, "application/octet-stream")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertNotEqual(response.mimetype, "application/pdf")

    def test_valid_jpeg_is_inline(self):
        response = self.response_for("photo.jpg", self.image_bytes("JPEG"))
        self.assertEqual(response.mimetype, "image/jpeg")
        self.assertIn("inline", response.headers["Content-Disposition"])

    def test_valid_png_is_inline(self):
        response = self.response_for("photo.png", self.image_bytes("PNG"))
        self.assertEqual(response.mimetype, "image/png")
        self.assertIn("inline", response.headers["Content-Disposition"])

    def test_valid_pdf_is_inline(self):
        response = self.response_for("document.pdf", b"%PDF-1.7\nnot a complete document")
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("inline", response.headers["Content-Disposition"])

    def test_office_documents_are_downloaded(self):
        for name in ("document.doc", "document.docx", "spreadsheet.xls", "spreadsheet.xlsx"):
            with self.subTest(name=name):
                response = self.response_for(name, b"office content")
                self.assertEqual(response.mimetype, "application/octet-stream")
                self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_historical_html_mime_does_not_override_valid_jpeg(self):
        response = self.response_for("legacy.jpg", self.image_bytes("JPEG"))
        self.assertEqual(response.mimetype, "image/jpeg")
        self.assertIn("inline", response.headers["Content-Disposition"])

    def test_historical_html_mime_does_not_make_html_inline(self):
        response = self.response_for("legacy.jpg", b"<html>not an image</html>")
        self.assertEqual(response.mimetype, "application/octet-stream")
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_webp_and_gif_are_inline(self):
        for image_format, extension in (("WEBP", "webp"), ("GIF", "gif")):
            with self.subTest(image_format=image_format):
                response = self.response_for(f"image.{extension}", self.image_bytes(image_format))
                self.assertEqual(response.mimetype, f"image/{extension}")
                self.assertIn("inline", response.headers["Content-Disposition"])

    def test_all_attachment_responses_have_nosniff(self):
        for name, content in (
            ("photo.jpg", self.image_bytes("JPEG")),
            ("document.pdf", b"%PDF-1.7\n"),
            ("document.docx", b"office content"),
        ):
            with self.subTest(name=name):
                response = self.response_for(name, content)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_missing_file_fails_without_inline_fallback(self):
        path = self.files / "missing.jpg"
        with self.module.app.test_request_context("/attachment-preview"):
            with self.assertRaises((FileNotFoundError, OSError)):
                self.module.safe_attachment_response(path, "missing.jpg")


if __name__ == "__main__":
    unittest.main()
