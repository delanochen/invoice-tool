"""Business routes with explicit current-app resolvers; no reverse import."""


def register_pwa_manifest_routes(app, *,
    _cb_resolve_jsonify,
):
    def app_manifest():
        """Main-site PWA manifest (v0.1.244).

    With this manifest, "添加到主屏幕 / 安装应用" creates a real standalone
    app: the phone keeps a single app window that RESUMES where the user left
    off, instead of stacking a new browser tab on every tap. A fresh launch
    starts in the field-work workspace, which is the mobile/PWA home screen.
    """
        response = _cb_resolve_jsonify()(
            name="Prasinos Power",
            short_name="Prasinos",
            id="/",
            start_url="/field/",
            scope="/",
            display="standalone",
            background_color="#0f766e",
            theme_color="#0f766e",
            icons=[
                dict(src=f"/static/field-icon-{size}.png", sizes=f"{size}x{size}", type="image/png")
                for size in (192, 512)
            ],
        )
        response.headers["Cache-Control"] = "no-cache"
        return response


    app.add_url_rule('/manifest.webmanifest', endpoint='app_manifest', view_func=app_manifest, methods=['GET'])
    return {'app_manifest': app_manifest}


def register_service_worker_routes(app, *,
    _cb_resolve_app,
):
    def app_service_worker():
        """Minimal passthrough service worker (v0.1.244).

    Deliberately NO response caching: the tool is dynamic and pages must stay
    live. The worker exists only so the browser treats the site as installable
    PWA; every request goes to the network exactly like before.
    """
        response = _cb_resolve_app().send_static_file("app-sw.js")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response


    app.add_url_rule('/sw.js', endpoint='app_service_worker', view_func=app_service_worker, methods=['GET'])
    return {'app_service_worker': app_service_worker}
