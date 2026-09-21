"""Tests for the dual-mode service order map: static-image endpoint,
FlexStaticMapsService marker/scale changes, and template wiring.

All Google API calls are mocked. No real network requests. Flask wiring is
smoke-tested against the real app module imported into a temp directory
(same pattern as test_travel_tools.py).
"""
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

REPO_DIR = Path(__file__).resolve().parent


def make_test_png(width=640, height=400, color=(100, 150, 200)):
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeImageResponse:
    def __init__(self, payload: bytes, content_type="image/png"):
        self._payload = payload
        self.status = 200
        self.headers = {"Content-Type": content_type}

    def read(self, amount=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FlexStaticMapsServiceUrlTest(unittest.TestCase):
    """The site map draws unlabeled colored markers; the URL must reflect that."""

    def _capture(self, markers, **kwargs):
        from travel_tools.static_maps import FlexStaticMapsService

        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            return FakeImageResponse(make_test_png())

        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            result = FlexStaticMapsService("test-key").get_map(markers, None, **kwargs)
        self.assertTrue(result.success)
        return captured["url"]

    def test_unlabeled_marker_omits_label_param(self):
        url = self._capture([{"color": "red", "position": "29.7,-95.3"}])
        self.assertIn("markers=color:red%7C29.7%2C-95.3", url)
        self.assertNotIn("label", url)

    def test_labeled_marker_keeps_label_param(self):
        url = self._capture([{"label": "A", "color": "green", "position": "29.7,-95.3"}])
        self.assertIn("markers=color:green%7Clabel:A%7C29.7%2C-95.3", url)

    def test_scale_param_forwarded(self):
        url = self._capture([{"label": "1", "color": "red", "position": "29.7,-95.3"}], size="640x640", scale=2)
        self.assertIn("scale=2", url)
        self.assertIn("size=640x640", url)

    def test_no_scale_by_default(self):
        url = self._capture([{"label": "1", "color": "red", "position": "29.7,-95.3"}])
        self.assertNotIn("scale", url)

    def test_center_and_zoom_forwarded(self):
        url = self._capture(
            [{"label": "1", "color": "red", "position": "29.7,-95.3"}],
            center="39.8283,-98.5795",
            zoom=4,
        )
        self.assertIn("center=39.8283%2C-98.5795", url)
        self.assertIn("zoom=4", url)

    def test_no_center_or_zoom_by_default(self):
        url = self._capture([{"label": "1", "color": "red", "position": "29.7,-95.3"}])
        self.assertNotIn("center=", url)
        self.assertNotIn("zoom=", url)


class ServiceOrderMapStaticWebTest(unittest.TestCase):
    """Endpoint guards, validation, and successful image composition."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        shutil.copytree(REPO_DIR / "ai_daily_report", Path(cls.temp_dir.name) / "ai_daily_report")
        shutil.copytree(REPO_DIR / "travel_tools", Path(cls.temp_dir.name) / "travel_tools")
        sys.path.insert(0, str(cls.temp_dir.name))
        spec = importlib.util.spec_from_file_location("invoice_tool_som_static_test", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
        with cls.module.app.app_context():
            cls.module.init_db()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.temp_dir.name))
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            self.module.db().execute("delete from settings where key = 'google_static_maps_api_key'")
            self.module.db().commit()

    def _login(self, client, user_id):
        with client.session_transaction() as sess:
            sess["user_id"] = user_id

    def _create_user(self, name, role):
        from werkzeug.security import generate_password_hash

        with self.module.app.app_context():
            cursor = self.module.db().execute(
                "insert into users (name, email, password_hash, role, is_active, created_at) values (?, ?, ?, ?, 1, datetime('now'))",
                (name, f"{name}@example.invalid", generate_password_hash("pw-123456"), role),
            )
            self.module.db().commit()
            return cursor.lastrowid

    def _admin_id(self):
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select id from users where role = 'admin' order by id limit 1"
            ).fetchone()
        return row["id"] if row else self._create_user("som-admin", "admin")

    def _external_employee_id(self):
        return self._create_user("som-ext-emp", "external_employee")

    def _external_manager_id(self):
        return self._create_user("som-ext-mgr", "external_manager")

    def _set_key(self, value):
        with self.module.app.app_context():
            self.module.db().execute(
                "insert or replace into settings (key, value) values ('google_static_maps_api_key', ?)",
                (value,),
            )
            self.module.db().commit()

    def _points(self, count=2):
        return [
            {"latitude": 29.7 + index * 0.1, "longitude": -95.3 - index * 0.1, "inspection_status": "fresh"}
            for index in range(count)
        ]

    def test_static_image_requires_login(self):
        client = self.module.app.test_client()
        resp = client.post("/service-orders/map/static-image", json={"points": []})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_static_image_forbidden_for_external_employee(self):
        client = self.module.app.test_client()
        self._login(client, self._external_employee_id())
        resp = client.post("/service-orders/map/static-image", json={"points": self._points()})
        self.assertEqual(resp.status_code, 403)

    def test_static_image_invalid_body_rejected(self):
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        resp = client.post("/service-orders/map/static-image", json={"points": "nope"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_request")

    def test_static_image_without_points_reports_no_visible_sites(self):
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        resp = client.post("/service-orders/map/static-image", json={"points": []})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "no_visible_sites")

    def test_static_image_without_key_reports_not_configured(self):
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        resp = client.post("/service-orders/map/static-image", json={"points": self._points()})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()["error"], "static_maps_api_not_configured")

    def test_static_image_success_labeled(self):
        self._set_key("test-key")
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        png = make_test_png()
        with patch("travel_tools.static_maps.FlexStaticMapsService") as maps_cls:
            maps_cls.return_value.get_map.return_value = type(
                "R", (), {"success": True, "image_bytes": png, "content_type": "image/png", "status": "success", "error": None}
            )()
            resp = client.post(
                "/service-orders/map/static-image",
                json={"points": self._points(3)},
            )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["available"])
        self.assertTrue(payload["image"].startswith("data:image/png;base64,"))
        self.assertEqual(payload["count"], 3)
        self.assertTrue(payload["labeled"])
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["labels"], ["1", "2", "3"])
        # Marker payloads passed to the static maps client
        sent_markers = maps_cls.return_value.get_map.call_args[0][0]
        self.assertEqual(len(sent_markers), 3)
        self.assertEqual(sent_markers[0]["color"], "green")
        self.assertEqual(sent_markers[0]["label"], "1")

    def test_static_image_pins_the_us_view(self):
        """The static site map must not auto-zoom to the filtered sites."""
        self._set_key("test-key")
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        png = make_test_png()
        for count in (1, 3, 40):
            with patch("travel_tools.static_maps.FlexStaticMapsService") as maps_cls:
                maps_cls.return_value.get_map.return_value = type(
                    "R", (), {"success": True, "image_bytes": png, "content_type": "image/png", "status": "success", "error": None}
                )()
                resp = client.post(
                    "/service-orders/map/static-image",
                    json={"points": self._points(count)},
                )
            self.assertEqual(resp.status_code, 200)
            kwargs = maps_cls.return_value.get_map.call_args[1]
            self.assertEqual(kwargs.get("center"), "39.8283,-98.5795")
            self.assertEqual(kwargs.get("zoom"), 4)
            self.assertEqual(kwargs.get("size"), "640x640")

    def test_static_image_beyond_label_limit_unlabeled(self):
        self._set_key("test-key")
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        png = make_test_png()
        with patch("travel_tools.static_maps.FlexStaticMapsService") as maps_cls:
            maps_cls.return_value.get_map.return_value = type(
                "R", (), {"success": True, "image_bytes": png, "content_type": "image/png", "status": "success", "error": None}
            )()
            resp = client.post(
                "/service-orders/map/static-image",
                json={"points": self._points(40)},
            )
        payload = resp.get_json()
        self.assertFalse(payload["labeled"])
        self.assertEqual(payload["count"], 40)
        sent_markers = maps_cls.return_value.get_map.call_args[0][0]
        self.assertTrue(all("label" not in marker for marker in sent_markers))

    def test_static_image_truncates_at_200(self):
        self._set_key("test-key")
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        png = make_test_png()
        with patch("travel_tools.static_maps.FlexStaticMapsService") as maps_cls:
            maps_cls.return_value.get_map.return_value = type(
                "R", (), {"success": True, "image_bytes": png, "content_type": "image/png", "status": "success", "error": None}
            )()
            resp = client.post(
                "/service-orders/map/static-image",
                json={"points": self._points(260)},
            )
        payload = resp.get_json()
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["count"], 200)
        sent_markers = maps_cls.return_value.get_map.call_args[0][0]
        self.assertEqual(len(sent_markers), 200)

    def test_static_image_includes_headquarters_marker(self):
        self._set_key("test-key")
        self.module.HEADQUARTERS_LATITUDE = "39.5"
        self.module.HEADQUARTERS_LONGITUDE = "-98.35"
        client = self.module.app.test_client()
        self._login(client, self._admin_id())
        png = make_test_png()
        with patch("travel_tools.static_maps.FlexStaticMapsService") as maps_cls:
            maps_cls.return_value.get_map.return_value = type(
                "R", (), {"success": True, "image_bytes": png, "content_type": "image/png", "status": "success", "error": None}
            )()
            resp = client.post(
                "/service-orders/map/static-image",
                json={"points": self._points(2), "include_headquarters": True},
            )
        payload = resp.get_json()
        self.assertTrue(payload["headquarters"])
        sent_markers = maps_cls.return_value.get_map.call_args[0][0]
        self.assertEqual(len(sent_markers), 3)
        self.assertEqual(sent_markers[0]["color"], "purple")
        self.assertNotIn("label", sent_markers[0])
        # Site labels stay aligned with site points (no HQ offset)
        self.assertEqual(payload["labels"], ["1", "2"])
        # scale=2 and 640x640 requested
        kwargs = maps_cls.return_value.get_map.call_args[1]
        self.assertEqual(kwargs.get("size"), "640x640")
        self.assertEqual(kwargs.get("scale"), 2)

    def test_static_image_external_manager_allowed(self):
        self._set_key("test-key")
        client = self.module.app.test_client()
        self._login(client, self._external_manager_id())
        png = make_test_png()
        with patch("travel_tools.static_maps.FlexStaticMapsService") as maps_cls:
            maps_cls.return_value.get_map.return_value = type(
                "R", (), {"success": True, "image_bytes": png, "content_type": "image/png", "status": "success", "error": None}
            )()
            resp = client.post(
                "/service-orders/map/static-image",
                json={"points": self._points(1)},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["available"])


class ServiceOrderMapTemplateWiringTest(unittest.TestCase):
    """The page must carry the mode toggle, the static panel, and the loader."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        module_path = Path(cls.temp_dir.name) / "app.py"
        shutil.copyfile(REPO_DIR / "app.py", module_path)
        shutil.copytree(REPO_DIR / "ai_daily_report", Path(cls.temp_dir.name) / "ai_daily_report")
        shutil.copytree(REPO_DIR / "travel_tools", Path(cls.temp_dir.name) / "travel_tools")
        sys.path.insert(0, str(cls.temp_dir.name))
        spec = importlib.util.spec_from_file_location("invoice_tool_som_template_test", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.module.app.template_folder = str(REPO_DIR / "templates")
        cls.module.app.static_folder = str(REPO_DIR / "static")
        with cls.module.app.app_context():
            cls.module.init_db()

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.temp_dir.name))
        cls.temp_dir.cleanup()

    def _page_body(self):
        client = self.module.app.test_client()
        with self.module.app.app_context():
            row = self.module.db().execute(
                "select id from users where role = 'admin' order by id limit 1"
            ).fetchone()
            admin_id = row["id"]
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        resp = client.get("/service-orders/map")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_toggle_and_static_panel_present(self):
        body = self._page_body()
        self.assertIn("staticModeToggle", body)
        self.assertIn("staticMapPanel", body)
        self.assertIn("staticMapImage", body)
        self.assertIn("staticSiteList", body)
        self.assertIn("静态模式", body)

    def test_map_and_site_list_share_a_row_container(self):
        """Map and site list render side by side (not stacked) in static mode."""
        body = self._page_body()
        self.assertIn('class="static-map-body"', body)
        image_at = body.index("staticMapImage")
        list_at = body.index("staticSiteList")
        body_at = body.index("static-map-body")
        self.assertLess(body_at, image_at)
        self.assertLess(image_at, list_at)

    def test_static_js_mentions_fixed_us_view(self):
        script = (Path(__file__).resolve().parent / "static" / "service-order-map-static.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("固定美国本土视图", script)

    def test_static_endpoint_url_in_config(self):
        body = self._page_body()
        self.assertIn("/service-orders/map/static-image", body)

    def test_loader_references_common_and_static_scripts(self):
        body = self._page_body()
        self.assertIn("service-order-map-common.js", body)
        self.assertIn("service-order-map-static.js", body)
        self.assertIn("serviceOrderMapMode", body)

    def test_google_maps_script_only_loaded_dynamically(self):
        # Configure a browser key so the rendered loader contains the Google
        # branch; then verify the maps API URL lives inside the dynamic
        # loader (JS string), not as a static <script src> tag that would
        # fire even in static mode.
        with self.module.app.app_context():
            self.module.db().execute(
                "insert or replace into settings (key, value) values ('google_maps_browser_api_key', 'test-browser-key')"
            )
            self.module.db().commit()
        body = self._page_body()
        self.assertNotIn('<script async src="https://maps.googleapis.com', body)
        self.assertIn("maps.googleapis.com/maps/api/js", body)
        self.assertIn("test-browser-key", body)


if __name__ == "__main__":
    unittest.main()
