import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path


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

    def upload_pdf(self, filename="manual.pdf"):
        return self.client.post(
            "/knowledge-base",
            data={
                "title": "Safety Manual",
                "category": "Training",
                "description": "Site safety procedures",
                "document": (io.BytesIO(b"%PDF-1.4\n%%EOF\n"), filename),
            },
            content_type="multipart/form-data",
        )

    def test_employee_can_view_but_cannot_upload(self):
        response = self.client.get("/knowledge-base")
        self.assertEqual(response.status_code, 200)
        self.assertIn("知识库", response.get_data(as_text=True))
        self.assertEqual(self.upload_pdf().status_code, 403)

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
            data={"title": "Updated Manual", "category": "Policy", "description": "Updated"},
        )
        self.assertEqual(edit.status_code, 302)
        self.set_role("admin")
        delete = self.client.post(f"/knowledge-base/{document_id}/delete")
        self.assertEqual(delete.status_code, 302)
        self.assertFalse(os.path.exists(path))
        with self.module.app.app_context():
            self.assertIsNone(
                self.module.db().execute("select id from knowledge_documents where id = ?", (document_id,)).fetchone()
            )


if __name__ == "__main__":
    unittest.main()
