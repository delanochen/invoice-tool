"""Bound expensive archive requests without queuing ordinary page requests."""

from functools import wraps
from threading import BoundedSemaphore

from flask import make_response
from werkzeug.wsgi import ClosingIterator


# One archive per worker, including transmission to a slow client. With eight
# request threads this leaves seven threads for normal pages and API calls.
_archive_slots = BoundedSemaphore(1)


def limited_archive_download(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _archive_slots.acquire(blocking=False):
            response = make_response("附件正在打包或下载，请稍后重试。", 503)
            response.headers["Retry-After"] = "10"
            response.headers["Cache-Control"] = "no-store"
            return response
        try:
            response = make_response(view(*args, **kwargs))
            # send_file uses direct_passthrough: response.call_on_close alone
            # does not reliably run under WSGI. Wrap the actual transmitted body.
            response.response = ClosingIterator(response.response, _archive_slots.release)
            return response
        except BaseException:
            _archive_slots.release()
            raise

    return wrapped
