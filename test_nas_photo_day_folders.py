import importlib.util
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image


REPO_DIR = Path(__file__).resolve().parent


def load_module(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_image(path, color="green"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 18), color).save(path, format="JPEG")


class PhotoWorkerDayFolderTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.worker = load_module("invoice_tool_photo_worker_day_test", REPO_DIR / "photo_worker.py")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_existing_timestamp_picture_and_thumbnail_move_into_day_folder(self):
        order_dir = self.root / "SO2608001"
        picture = order_dir / "pictures" / "20260809_143000.jpg"
        thumbnail = order_dir / "thumbnails" / picture.name
        second_picture = order_dir / "pictures" / "20260809_143000-2.jpg"
        second_thumbnail = order_dir / "thumbnails" / second_picture.name
        create_image(picture)
        create_image(thumbnail)
        create_image(second_picture, "blue")
        create_image(second_thumbnail, "blue")

        changed = self.worker.rename_existing_pictures_by_datetime(order_dir)

        expected_picture = order_dir / "pictures" / "2026-08-09" / picture.name
        expected_thumbnail = order_dir / "thumbnails" / "2026-08-09" / picture.name
        expected_second = order_dir / "pictures" / "2026-08-09" / second_picture.name
        self.assertEqual(changed, 2)
        self.assertTrue(expected_picture.is_file())
        self.assertTrue(expected_thumbnail.is_file())
        self.assertTrue(expected_second.is_file())
        self.assertFalse(picture.exists())
        self.assertEqual(self.worker.rename_existing_pictures_by_datetime(order_dir), 0)

    def test_new_picture_without_exif_uses_file_date_folder(self):
        source = self.root / "SO2608001" / "incoming" / "phone" / "photo.jpg"
        create_image(source)
        modified = datetime(2026, 8, 8, 16, 20, 30).timestamp()
        os.utime(source, (modified, modified))
        relative = Path("phone/photo.jpg")

        output = self.worker.timestamp_output_relative(relative, source)

        self.assertEqual(output.as_posix(), "2026-08-08/20260808_162030.jpg")

    def test_compose_mount_defaults_to_debian_storage_and_can_be_overridden(self):
        compose = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")
        updater = (REPO_DIR / "scripts" / "auto-update.sh").read_text(encoding="utf-8")
        expected = "${SHARED_PHOTOS_HOST_DIR:-/srv/invoice-tool/shared-photos}"
        self.assertEqual(compose.count(expected), 2)
        self.assertNotIn("/volume2/TeamFolder", compose)
        self.assertIn('log "shared photos auto-detected: $SHARED_PHOTOS_HOST_DIR"', updater)
        self.assertIn('VOLUME2_PHOTOS="/volume2/TeamFolder/', updater)
        self.assertIn('APP_DIR="${INVOICE_TOOL_DIR:-$DEFAULT_APP_DIR}"', updater)
        self.assertIn('LOG_FILE="${INVOICE_TOOL_UPDATE_LOG:-$(dirname "$APP_DIR")/invoice-tool-auto-update.log}"', updater)
        self.assertIn('/volume[0-9]*/docker/invoice-tool)', updater)
        self.assertNotIn('APP_DIR="${INVOICE_TOOL_DIR:-/volume1/docker/invoice-tool}"', updater)


class SharedPhotoBrowseDayFolderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        cls.module = load_module("invoice_tool_shared_photo_day_test_app", module_path)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.shared_root = Path(cls.temp_dir.name) / "shared-photos"
        cls.module.SHARED_PHOTOS_DIR = str(cls.shared_root)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        shutil.rmtree(self.shared_root, ignore_errors=True)
        create_image(self.shared_root / "SO2608001" / "pictures" / "2026-08-09" / "day.jpg")
        create_image(self.shared_root / "SO2608001" / "pictures" / "legacy.jpg", "blue")
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute("delete from users")
            cursor = connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values ('Employee', 'employee@example.com', 'unused', 'employee', '2026-08-10T12:00:00')
                """
            )
            self.user_id = cursor.lastrowid
            connection.commit()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_browser_can_filter_by_day_and_still_show_all_legacy_photos(self):
        day_response = self.client.get("/shared-photos/browse?path=SO2608001&day=2026-08-09")
        self.assertEqual(day_response.status_code, 200)
        day_data = day_response.get_json()
        self.assertEqual(day_data["selected_day"], "2026-08-09")
        self.assertEqual([image["name"] for image in day_data["images"]], ["day.jpg"])
        self.assertIn({"name": "2026-08-09", "count": 1}, day_data["folders"])

        all_response = self.client.get("/shared-photos/browse?path=SO2608001")
        all_data = all_response.get_json()
        all_names = [image["name"] for image in all_data["images"]]
        self.assertEqual(all_names, ["2026-08-09/day.jpg", "legacy.jpg"])
        self.assertIn({"name": "2026-08-09", "count": 1}, all_data["folders"])

    def test_invalid_day_is_rejected(self):
        response = self.client.get("/shared-photos/browse?path=SO2608001&day=../../data")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
