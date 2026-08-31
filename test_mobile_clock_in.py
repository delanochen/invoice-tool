import importlib.util
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image


REPO_DIR = Path(__file__).resolve().parent


class MobileClockInTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        spec = importlib.util.spec_from_file_location("invoice_tool_clock_in_test_app", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
        cls.module.SHARED_PHOTOS_DIR = str(Path(cls.temp_dir.name) / "nas-photos")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from clock_in_photos")
            connection.execute("delete from audit_logs")
            connection.execute("delete from buyers")
            connection.execute("delete from users")
            user = connection.execute(
                "insert into users (name, email, password_hash, role, created_at) values ('现场员工', 'clock@example.com', 'unused', 'employee', ?)",
                (self.module.now(),),
            )
            self.user_id = user.lastrowid
            site = connection.execute(
                "insert into buyers (buyer_number, name, detailed_address, created_at) values ('BUY-CLOCK', '测试站点', '100 Test Road', ?)",
                (self.module.now(),),
            )
            self.site_id = site.lastrowid
            connection.commit()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    @staticmethod
    def jpeg_file():
        buffer = BytesIO()
        Image.new("RGB", (640, 480), "green").save(buffer, format="JPEG")
        buffer.seek(0)
        return buffer

    def test_page_lists_site_and_operator(self):
        response = self.client.get("/mobile-clock-in")
        self.assertEqual(response.status_code, 200)
        self.assertIn("测试站点", response.get_data(as_text=True))
        self.assertIn("现场员工", response.get_data(as_text=True))

    def test_login_links_to_internal_app_install_page(self):
        login = self.client.get("/login")
        self.assertIn("下载 iPhone 内部版 App", login.get_data(as_text=True))
        install = self.client.get("/mobile-app")
        self.assertEqual(install.status_code, 200)
        self.assertIn("安装包正在准备中", install.get_data(as_text=True))

    def test_upload_records_metadata_and_writes_nas_photo(self):
        response = self.client.post(
            "/api/mobile-clock-in",
            data={
                "site_id": str(self.site_id),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "time_offset_minutes": "0",
                "latitude": "30.267200",
                "longitude": "-97.743100",
                "accuracy": "8.5",
                "photo": (self.jpeg_file(), "clock-in.jpg"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["ok"])
        with self.module.app.app_context():
            row = self.module.db().execute("select * from clock_in_photos").fetchone()
            self.assertEqual(row["buyer_id"], self.site_id)
            self.assertEqual(row["user_id"], self.user_id)
            self.assertAlmostEqual(row["latitude"], 30.2672)
            self.assertTrue((Path(self.module.SHARED_PHOTOS_DIR) / row["relative_path"]).is_file())

    def test_upload_rejects_unknown_site(self):
        response = self.client.post(
            "/api/mobile-clock-in",
            data={"site_id": "999999"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
