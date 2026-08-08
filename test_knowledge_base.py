import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from reportlab.pdfgen import canvas


REPO_DIR = Path(__file__).resolve().parent


class KnowledgeBaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_knowledge_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from knowledge_documents")
            connection.execute("delete from users")
            cursor = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('Employee', 'employee@example.com', 'unused', 'employee', '2026-08-07T12:00:00')
                """
            )
            self.user_id = cursor.lastrowid
            connection.commit()
        shutil.rmtree(self.module.KNOWLEDGE_BASE_DIR, ignore_errors=True)
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def set_role(self, role):
        with self.module.app.app_context():
            self.module.db().execute("update users set role = ? where id = ?", (role, self.user_id))
            self.module.db().commit()

    @staticmethod
    def pdf_bytes(text="Site safety procedures"):
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer)
        pdf.drawString(72, 760, text)
        pdf.save()
        return buffer.getvalue()

    def upload_pdf(self, filename="manual.pdf", text="Site safety procedures"):
        return self.client.post(
            "/knowledge-base",
            data={
                "title": "Safety Manual",
                "category": "Training",
                "description": "Site safety procedures",
                "document": (io.BytesIO(self.pdf_bytes(text)), filename),
            },
            content_type="multipart/form-data",
        )

    def test_employee_can_view_but_cannot_upload(self):
        response = self.client.get("/knowledge-base")
        self.assertEqual(response.status_code, 200)
        self.assertIn("知识库", response.get_data(as_text=True))
        self.assertEqual(self.upload_pdf().status_code, 403)

    def test_pdf_picker_is_not_wrapped_by_a_full_width_label(self):
        self.set_role("finance")
        html = self.client.get("/knowledge-base").get_data(as_text=True)
        self.assertIn('class="full-span form-field knowledge-file-field"', html)
        self.assertNotIn('<label class="full-span">PDF 文件', html)

    def test_finance_can_upload_preview_download_and_search_pdf(self):
        self.set_role("finance")
        response = self.upload_pdf()
        self.assertEqual(response.status_code, 302)

        with self.module.app.app_context():
            document = self.module.db().execute("select * from knowledge_documents").fetchone()
            self.assertIsNotNone(document)
            self.assertEqual(document["title"], "Safety Manual")
            self.assertTrue(os.path.isfile(self.module.knowledge_document_path(document)))
            document_id = document["id"]

        preview = self.client.get(f"/knowledge-base/{document_id}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "application/pdf")
        preview.close()
        download = self.client.get(f"/knowledge-base/{document_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        download.close()
        search = self.client.get("/knowledge-base?q=Safety&category=Training")
        self.assertIn("Safety Manual", search.get_data(as_text=True))
        with self.module.app.app_context():
            document = self.module.db().execute(
                "select view_count, download_count, search_text from knowledge_documents where id = ?",
                (document_id,),
            ).fetchone()
            self.assertEqual(document["view_count"], 1)
            self.assertEqual(document["download_count"], 1)
            self.assertIn("Site safety procedures", document["search_text"])

    def test_full_text_search_finds_pdf_content(self):
        self.set_role("finance")
        self.upload_pdf(text="Hydraulic compressor maintenance procedure")
        response = self.client.get("/knowledge-base?q=compressor")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Safety Manual", response.get_data(as_text=True))

    def test_non_pdf_content_is_rejected(self):
        self.set_role("finance")
        response = self.client.post(
            "/knowledge-base",
            data={"document": (io.BytesIO(b"not a pdf"), "fake.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            count = self.module.db().execute("select count(*) as count from knowledge_documents").fetchone()["count"]
            self.assertEqual(count, 0)

    def test_admin_can_edit_and_delete_document(self):
        self.set_role("finance")
        self.upload_pdf()
        with self.module.app.app_context():
            document = self.module.db().execute("select * from knowledge_documents").fetchone()
            document_id = document["id"]
            path = self.module.knowledge_document_path(document)

        edit = self.client.post(
            f"/knowledge-base/{document_id}/edit",
            data={
                "title": "Updated Manual",
                "category": "Policy",
                "description": "Updated",
                "expires_on": "2026-08-20",
                "is_pinned": "1",
            },
        )
        self.assertEqual(edit.status_code, 302)
        with self.module.app.app_context():
            updated = self.module.db().execute(
                "select is_pinned, expires_on from knowledge_documents where id = ?", (document_id,)
            ).fetchone()
            self.assertEqual(updated["is_pinned"], 1)
            self.assertEqual(updated["expires_on"], "2026-08-20")
        self.set_role("admin")
        delete = self.client.post(f"/knowledge-base/{document_id}/delete")
        self.assertEqual(delete.status_code, 302)
        self.assertFalse(os.path.exists(path))
        with self.module.app.app_context():
            self.assertIsNone(
                self.module.db().execute("select id from knowledge_documents where id = ?", (document_id,)).fetchone()
            )

    def test_new_version_preserves_history_and_becomes_searchable(self):
        self.set_role("finance")
        self.upload_pdf(text="Original release")
        with self.module.app.app_context():
            document = self.module.db().execute("select * from knowledge_documents").fetchone()
            document_id = document["id"]
            original_path = self.module.knowledge_document_path(document)

        response = self.client.post(
            f"/knowledge-base/{document_id}/versions",
            data={
                "change_note": "Added troubleshooting",
                "document": (io.BytesIO(self.pdf_bytes("Updated inverter troubleshooting guide")), "manual-v2.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with self.module.app.app_context():
            document = self.module.db().execute(
                "select * from knowledge_documents where id = ?", (document_id,)
            ).fetchone()
            versions = self.module.db().execute(
                "select * from knowledge_document_versions where document_id = ? order by version_number",
                (document_id,),
            ).fetchall()
            self.assertEqual(document["current_version"], 2)
            self.assertEqual(len(versions), 2)
            self.assertTrue(os.path.isfile(original_path))
            current_path = self.module.knowledge_document_path(document)
            self.assertTrue(os.path.isfile(current_path))
            self.assertIn("inverter troubleshooting", document["search_text"])
        search = self.client.get("/knowledge-base?q=inverter")
        self.assertIn("Safety Manual", search.get_data(as_text=True))
        self.set_role("admin")
        self.assertEqual(self.client.post(f"/knowledge-base/{document_id}/delete").status_code, 302)
        self.assertFalse(os.path.exists(original_path))
        self.assertFalse(os.path.exists(current_path))


if __name__ == "__main__":
    unittest.main()
