"""Public static assets must be shared-cacheable without exposing session content.

Cloudflare cannot key a shared cache on a cookie, so ``Vary: Cookie`` on
``/static/`` made every request revalidate against the origin. Flask re-adds
that header in ``process_response``, after every ``after_request`` hook, so the
fix normalises the headers in WSGI middleware and these tests pin the behaviour
end to end.
"""

import unittest
from pathlib import Path

from invoice_tool.request_lifecycle.static_cache import (
    PublicStaticCacheMiddleware,
    PUBLIC_STATIC_CACHE_CONTROL,
)

ROOT = Path(__file__).resolve().parent


class MiddlewareTest(unittest.TestCase):
    """Header normalisation without booting the whole application."""

    @staticmethod
    def call(path, status="200 OK", headers=None, method="GET"):
        headers = list(headers or [])
        seen = {}

        def fake_app(environ, start_response):
            start_response(status, headers)
            return [b"body"]

        def capture(start_status, sent_headers, exc_info=None):
            seen["status"] = start_status
            seen["headers"] = list(sent_headers)
            return lambda data: None

        body = PublicStaticCacheMiddleware(fake_app)(
            {"PATH_INFO": path, "REQUEST_METHOD": method}, capture
        )
        self_body = b"".join(body) if not callable(body) else body
        return seen, self_body

    def test_static_response_drops_cookie_vary_and_becomes_immutable(self):
        seen, body = self.call(
            "/static/erp-topnav.css",
            headers=[
                ("Content-Type", "text/css"),
                ("Vary", "Cookie"),
                ("Cache-Control", "max-age=14400"),
                ("Set-Cookie", "session=abc; Path=/"),
            ],
        )
        headers = {name.lower(): value for name, value in seen["headers"]}
        self.assertEqual(headers["cache-control"], PUBLIC_STATIC_CACHE_CONTROL)
        self.assertEqual(headers["vary"], "Accept-Encoding")
        self.assertNotIn("set-cookie", headers)
        self.assertEqual(headers["content-type"], "text/css")
        self.assertEqual(body, b"body")

    def test_dynamic_and_private_paths_keep_their_headers(self):
        for path in ("/", "/attachments/thumbnails/x.png", "/login"):
            seen, _ = self.call(
                path, headers=[("Vary", "Cookie"), ("Cache-Control", "no-store, private")]
            )
            headers = {name.lower(): value for name, value in seen["headers"]}
            self.assertEqual(headers["vary"], "Cookie")
            self.assertEqual(headers["cache-control"], "no-store, private")

    def test_non_success_static_response_is_untouched(self):
        seen, _ = self.call("/static/missing.css", status="404 NOT FOUND",
                            headers=[("Vary", "Cookie")])
        headers = {name.lower(): value for name, value in seen["headers"]}
        self.assertEqual(headers["vary"], "Cookie")
        self.assertNotIn("cache-control", headers)

    def test_post_to_static_is_untouched(self):
        seen, _ = self.call("/static/erp-topnav.css", method="POST",
                            headers=[("Vary", "Cookie")])
        headers = {name.lower(): value for name, value in seen["headers"]}
        self.assertEqual(headers["vary"], "Cookie")


class FlaskSessionIntegrationTest(unittest.TestCase):
    """The real Flask session layer must keep Vary: Cookie only off /static."""

    def test_session_hook_does_not_leak_cookie_vary_onto_static(self):
        from flask import Flask, session

        app = Flask(__name__, static_folder=str(ROOT / "static"))
        app.config.update(TESTING=True, SECRET_KEY="test")

        @app.get("/private")
        def private():
            session["user"] = 1
            return "secret"

        app.wsgi_app = PublicStaticCacheMiddleware(app.wsgi_app)
        client = app.test_client()

        asset = client.get("/static/erp-topnav.css?v=0.0.0")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.headers["Cache-Control"], PUBLIC_STATIC_CACHE_CONTROL)
        self.assertEqual(asset.headers["Vary"], "Accept-Encoding")
        self.assertNotIn("Set-Cookie", asset.headers)

        page = client.get("/private")
        self.assertIn("Cookie", page.headers["Vary"])


if __name__ == "__main__":
    unittest.main()
