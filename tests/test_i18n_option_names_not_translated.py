from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_option_text_uses_exact_translation():
    """option 文本（人名、站点名等数据）只做整词精确翻译，避免被部分翻译拆坏。

    回归：英文界面人员下拉曾把“高阳”→“High阳”、“廖鸿”→“廖High”、
    “绍剑”→“photos剑”。option 必须排除在部分翻译之外——
    即便 option 被 label/button 等部分翻译容器包裹（closest 沿祖先链命中）。
    """
    source = (ROOT / "static" / "ui-i18n.js").read_text(encoding="utf-8")
    selector_line = next(
        line for line in source.splitlines() if "const partialSelector" in line
    )
    assert "option" not in selector_line
    # 祖先链兜底：closest(partialSelector) 可能因 label 包裹而命中，需显式排除 option
    assert 'closest("option")' in source


def test_explicit_master_data_boundaries_are_never_translated():
    """动态主数据边界同时保护首屏扫描、属性和 MutationObserver。"""
    source = (ROOT / "static" / "ui-i18n.js").read_text(encoding="utf-8")
    assert 'const dataSelector = "[data-i18n-skip], [translate=\'no\'], .notranslate"' in source
    assert source.count("closest(dataSelector)") >= 6


def test_daily_report_people_and_master_data_headers_are_marked_as_data():
    form = (ROOT / "templates" / "service_report_form.html").read_text(encoding="utf-8")
    view = (ROOT / "templates" / "service_report_view.html").read_text(encoding="utf-8")
    assert form.count('data-i18n-skip translate="no"') >= 3
    assert '{{ user.name }}</option>' in form
    assert 'data-i18n-skip translate="no">{{ company.name }}</span>' in view


def test_other_master_data_detail_headers_are_marked_as_data():
    expected = {
        "client_form.html": "{{ client.client_number }} · {{ client.name }}",
        "user_form.html": "{{ user.name }} · {{ user.email }}",
        "worker_tax_status.html": "{{ worker.name }}",
        "contract_detail.html": "{{ contract.title }} · {{ client.name }}",
    }
    for filename, value in expected.items():
        source = (ROOT / "templates" / filename).read_text(encoding="utf-8")
        assert "data-i18n-skip" in source
        assert value in source
