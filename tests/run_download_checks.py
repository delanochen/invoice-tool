"""Run download regressions and Linux Gunicorn smoke checks without a database."""
import http.client
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# These tests only need fixture identity and exception assertions. Keep the
# production image free of pytest and bypass repository-wide DB reset hooks.
stub = types.ModuleType('pytest')
stub.fixture = lambda function: function
stub.raises = unittest.TestCase().assertRaises
sys.modules['pytest'] = stub
capacity = runpy.run_path(str(ROOT / 'tests/test_download_capacity.py'))
for name in (
    'test_slow_download_reserves_slot_until_body_closed_but_page_works',
    'test_build_failure_releases_slot',
    'test_build_in_progress_does_not_queue_other_requests',
    'test_stream_failure_releases_slot_on_wsgi_close',
):
    capacity[name](capacity['app']())
with tempfile.TemporaryDirectory() as directory:
    capacity['test_order_archive_streaming_cleanup_and_paths'](Path(directory))
streaming = runpy.run_path(str(ROOT / 'tests/test_streaming_zip.py'))
with tempfile.TemporaryDirectory() as directory:
    patches = []
    def setattr_for_test(obj, name, value):
        operation = patch.object(obj, name, value)
        operation.start()
        patches.append(operation)
    try:
        streaming['test_large_member_is_read_incrementally_and_closed_on_disconnect'](
            Path(directory), types.SimpleNamespace(setattr=setattr_for_test))
    finally:
        for operation in reversed(patches):
            operation.stop()
with tempfile.TemporaryDirectory() as directory:
    streaming['test_stored_zip64_archive_crc_duplicates_and_missing_files'](Path(directory))
print('7 download regressions passed; no database connections', flush=True)

if sys.platform != 'win32':
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory) / 'download_smoke.py'
        fixture.write_text('''from flask import Flask, Response
from invoice_tool.attachments.download_capacity import limited_archive_download
import time
app = Flask(__name__)
@app.get('/page')
def page(): return 'ok'
@app.get('/slow')
@limited_archive_download
def slow():
    def body():
        yield b'first'
        time.sleep(4)
        yield b'last'
    return Response(body(), direct_passthrough=True)
''')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([
                sys.executable, '-m', 'gunicorn', '-c', str(ROOT / 'gunicorn.conf.py'),
                '--pythonpath', directory, '--bind', f'127.0.0.1:{port}', 'download_smoke:app',
            ], cwd=ROOT, stdout=log, stderr=log)
            connections = []
            try:
                for attempt in range(50):
                    try:
                        probe = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                        probe.request('GET', '/page')
                        assert probe.getresponse().read() == b'ok'
                        probe.close()
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError('Gunicorn did not start')
                slow = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                connections.append(slow)
                slow.request('GET', '/slow')
                response = slow.getresponse()
                assert response.status == 200
                assert response.read(5) == b'first'
                start = time.monotonic()
                for _ in range(5):
                    page = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                    connections.append(page)
                    page.request('GET', '/page')
                    assert page.getresponse().read() == b'ok'
                assert time.monotonic() - start < 3
            finally:
                for connection in connections:
                    connection.close()
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.seek(0)
            output = log.read().decode()
            assert 'Using worker: gthread' in output
            assert output.count('Booting worker with pid:') == 2
        print('Gunicorn: 2 gthread workers; pages respond during slow download; no database', flush=True)
