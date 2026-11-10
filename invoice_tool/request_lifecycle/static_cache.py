"""Serve public, versioned static assets through shared caches (CDN).

Flask adds ``Vary: Cookie`` from the session layer in ``process_response``,
which runs *after* every ``after_request`` hook, so removing the header there
would only be re-added. A shared cache such as Cloudflare cannot key on a
cookie, so every ``/static/`` request was revalidated against the origin
instead of being served from the edge.

This middleware sits outside Flask and normalises the headers once the whole
stack has finished. Only ``/static/`` is touched: private content such as
attachment thumbnails keeps its own ``Vary: Cookie`` and stays out of shared
caches.
"""

PUBLIC_STATIC_PREFIX = "/static/"
PUBLIC_STATIC_CACHE_CONTROL = "public, max-age=31536000, immutable"


class PublicStaticCacheMiddleware:
    """Let shared caches store ``/static/`` responses across sessions."""

    def __init__(
        self,
        app,
        prefix=PUBLIC_STATIC_PREFIX,
        cache_control=PUBLIC_STATIC_CACHE_CONTROL,
    ):
        self.app = app
        self.prefix = prefix
        self.cache_control = cache_control

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO") or ""
        method = (environ.get("REQUEST_METHOD") or "GET").upper()
        if not path.startswith(self.prefix) or method not in ("GET", "HEAD"):
            return self.app(environ, start_response)

        def normalize(start_status, headers, exc_info=None):
            if not start_status.startswith(("200", "304")):
                return start_response(start_status, headers, exc_info)
            return start_response(start_status, self.normalized(headers), exc_info)

        return self.app(environ, normalize)

    def normalized(self, headers):
        kept = []
        for name, value in headers:
            lowered = name.lower()
            if lowered in ("vary", "cache-control", "set-cookie", "expires", "pragma"):
                continue
            kept.append((name, value))
        kept.append(("Cache-Control", self.cache_control))
        kept.append(("Vary", "Accept-Encoding"))
        return kept
