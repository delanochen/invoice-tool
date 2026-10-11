import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_daily_report.static_maps import GoogleStaticMapsService, MAX_URL_LENGTH
from travel_tools.polyline import decode_polyline, encode_polyline


def _make_long_polyline() -> str:
    """构造一条点数很多、编码很长的 polyline（模拟 ~600 英里路线）。"""
    points = []
    for i in range(3000):
        points.append((29.7604 + i * 0.002, -95.3698 + i * 0.001))
    return encode_polyline(points)


def test_short_polyline_kept_as_is():
    svc = GoogleStaticMapsService("fake-key")
    short = encode_polyline([(29.76, -95.36), (30.0, -94.0)])
    budget = 4000
    assert svc._fit_path(short, budget) == short


def test_long_polyline_is_simplified_to_budget():
    svc = GoogleStaticMapsService("fake-key")
    long_poly = _make_long_polyline()
    assert len(long_poly) > 10000
    budget = 2000
    fitted = svc._fit_path(long_poly, budget)
    assert fitted is not None
    assert len(urllib.parse.quote(fitted)) <= budget


def test_simplification_keeps_first_and_last_points():
    svc = GoogleStaticMapsService("fake-key")
    long_poly = _make_long_polyline()
    original = decode_polyline(long_poly)
    fitted = svc._fit_path(long_poly, 2000)
    simplified = decode_polyline(fitted)
    assert simplified[0] == original[0]
    assert simplified[-1] == original[-1]
    # 抽稀后点数显著减少但仍保留形状
    assert len(simplified) < len(original)


def test_path_budget_accounts_overhead():
    svc = GoogleStaticMapsService("fake-key")
    query_string = "size=640x400&maptype=roadmap&key=k"
    parts = ["markers=color:green%7Clabel:A%7Caddr1", "markers=color:red%7Clabel:B%7Caddr2"]
    budget = svc._path_budget(query_string, parts)
    assert budget > 0
    assert budget < MAX_URL_LENGTH
