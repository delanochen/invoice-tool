"""Bound expensive archive requests without queuing ordinary page requests."""

from functools import wraps
from threading import BoundedSemaphore

from flask import make_response


# One archive per worker, including transmission to a slow client. With eight
# request threads this leaves seven threads for normal pages and API calls.
_archive_slots = BoundedSemaphore(1)


def _releasing(iterable, release):
    """把名额释放绑在响应体迭代结束/关闭上。

    不能只依赖 response.call_on_close：send_file 走 direct_passthrough，
    WSGI 侧不一定回调 close，满一次下载就会把该 worker 的打包名额永久占住
    （实测下一次请求直接 503）。
    """
    def generate():
        try:
            for item in iterable:
                yield item
        finally:
            release()
    return generate()


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
            response.response = _releasing(response.response, _archive_slots.release)
            return response
        except BaseException:
            _archive_slots.release()
            raise

    return wrapped
