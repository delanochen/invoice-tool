"""No database access: exercise WSGI streaming and download admission."""
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from flask import Flask, Response, send_file

from invoice_tool.attachments.download_capacity import limited_archive_download


@pytest.fixture
def app():
    app = Flask(__name__)
    app.testing = True

    @app.get('/archive')
    @limited_archive_download
    def archive():
        return send_file(BytesIO(b'archive content'), mimetype='application/zip')

    @app.get('/page')
    def page():
        return 'page available'

    return app


def test_slow_download_reserves_slot_until_body_closed_but_page_works(app):
    download = app.test_client().get('/archive', buffered=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            page = pool.submit(lambda: app.test_client().get('/page')).result(timeout=2)
            busy = pool.submit(lambda: app.test_client().get('/archive')).result(timeout=2)
        assert page.status_code == 200
        assert busy.status_code == 503
        assert busy.headers['Retry-After'] == '10'
        assert busy.headers['Cache-Control'] == 'no-store'
        assert download.data == b'archive content'
    finally:
        download.close()
    resumed = app.test_client().get('/archive')
    assert resumed.status_code == 200
    resumed.close()


def test_build_failure_releases_slot(app):
    @app.get('/broken')
    @limited_archive_download
    def broken():
        raise ValueError('archive failed')

    with pytest.raises(ValueError):
        app.test_client().get('/broken')
    response = app.test_client().get('/archive')
    assert response.status_code == 200
    response.close()


def test_build_in_progress_does_not_queue_other_requests(app):
    started, finish = Event(), Event()

    @app.get('/building')
    @limited_archive_download
    def building():
        started.set()
        assert finish.wait(3)
        return Response(b'zip')

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(lambda: app.test_client().get('/building'))
        try:
            assert started.wait(2)
            assert app.test_client().get('/page').status_code == 200
            assert app.test_client().get('/archive').status_code == 503
        finally:
            finish.set()
            pending.result(timeout=3).close()


def test_stream_failure_releases_slot_on_wsgi_close(app):
    @app.get('/stream-error')
    @limited_archive_download
    def stream_error():
        def chunks():
            yield b'first'
            raise OSError('client disconnected')
        return Response(chunks())

    response = app.test_client().get('/stream-error', buffered=False)
    try:
        with pytest.raises(OSError):
            _ = response.data
    finally:
        response.close()
    response = app.test_client().get('/archive')
    assert response.status_code == 200
    response.close()


def test_order_archive_streaming_cleanup_and_paths(tmp_path):
    import inspect
    import os
    import re
    import zipfile
    from pathlib import Path
    from werkzeug.utils import secure_filename
    from invoice_tool.service_orders.attachment_routes import register_order_attachments_routes

    app = Flask(__name__)
    app.testing = True
    picture = tmp_path / 'SO-TEST' / 'pictures' / 'day' / 'photo.jpg'
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b'photo fixture')
    values = dict(
        Path=Path, os=os, re=re, zipfile=zipfile,
        login_required=lambda view: view,
        require_service_order=lambda order_id: {'order_number': 'SO-TEST'},
        has_action_permission=lambda *args: False,
        has_menu_permission=lambda *args: True,
        can_view_customer_reimbursement=lambda: False,
        shared_photos_root=lambda: tmp_path,
        ALLOWED_IMAGE_EXTENSIONS={'jpg'}, secure_filename=secure_filename,
        send_file=send_file,
    )
    resolvers = {}
    for name in inspect.signature(register_order_attachments_routes).parameters:
        if name != 'app':
            value = values.get(name.removeprefix('_cb_resolve_'))
            resolvers[name] = lambda value=value: value
    register_order_attachments_routes(app, **resolvers)
    response = app.test_client().get('/service-orders/1/attachments.zip', buffered=False)
    try:
        data = response.data
        assert 'Content-Length' not in response.headers
        assert response.headers['Content-Disposition'].startswith('attachment;')
        assert response.headers['Cache-Control'] == 'private, no-store'
        with zipfile.ZipFile(BytesIO(data)) as archive:
            assert archive.read('现场工作照片/day-photo.jpg') == b'photo fixture'
            assert len([name for name in archive.namelist() if name.endswith('/')]) == 6
    finally:
        response.close()
    # A second download also proves the capacity was returned on WSGI close.
    response = app.test_client().get('/service-orders/1/attachments.zip')
    assert response.status_code == 200
    response.close()
