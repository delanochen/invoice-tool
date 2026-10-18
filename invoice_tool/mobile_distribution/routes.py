"""Business routes with explicit current-app resolvers; no reverse import."""


def register_mobile_install_routes(app, *,
    _cb_resolve_IOS_APP_VERSION,
    _cb_resolve_IOS_BUNDLE_ID,
    _cb_resolve_IOS_IPA_PATH,
    _cb_resolve_abort,
    _cb_resolve_app,
    _cb_resolve_html,
    _cb_resolve_os,
    _cb_resolve_render_template,
    _cb_resolve_url_for,
    _cb_resolve_urlencode,
):
    def mobile_app_install():
        ipa_available = _cb_resolve_os().path.isfile(_cb_resolve_IOS_IPA_PATH()) and _cb_resolve_os().path.getsize(_cb_resolve_IOS_IPA_PATH()) > 0
        manifest_url = _cb_resolve_url_for()("mobile_app_manifest", _external=True, _scheme="https")
        install_url = "itms-services://?" + _cb_resolve_urlencode()({"action": "download-manifest", "url": manifest_url})
        return _cb_resolve_render_template()(
            "mobile_app_install.html",
            ipa_available=ipa_available,
            install_url=install_url,
            ios_app_version=_cb_resolve_IOS_APP_VERSION(),
        )


    def mobile_app_manifest():
        if not _cb_resolve_os().path.isfile(_cb_resolve_IOS_IPA_PATH()):
            _cb_resolve_abort()(404)
        ipa_url = _cb_resolve_html().escape(_cb_resolve_url_for()("mobile_app_ipa", _external=True, _scheme="https"), quote=True)
        bundle_id = _cb_resolve_html().escape(_cb_resolve_IOS_BUNDLE_ID(), quote=True)
        version = _cb_resolve_html().escape(_cb_resolve_IOS_APP_VERSION(), quote=True)
        manifest = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>items</key><array><dict>
<key>assets</key><array><dict><key>kind</key><string>software-package</string><key>url</key><string>{ipa_url}</string></dict></array>
<key>metadata</key><dict><key>bundle-identifier</key><string>{bundle_id}</string><key>bundle-version</key><string>{version}</string><key>kind</key><string>software</string><key>title</key><string>Prasinos Power</string></dict>
</dict></array></dict></plist>'''
        return _cb_resolve_app().response_class(manifest, mimetype="application/xml")


    app.add_url_rule('/mobile-app', endpoint='mobile_app_install', view_func=mobile_app_install, methods=['GET'])
    app.add_url_rule('/mobile-app/manifest.plist', endpoint='mobile_app_manifest', view_func=mobile_app_manifest, methods=['GET'])
    return {'mobile_app_install': mobile_app_install, 'mobile_app_manifest': mobile_app_manifest}
