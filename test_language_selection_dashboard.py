"""语言选择改版 + 概览待审核报销回归测试（v0.1.251）。

需求（2026-09-19 用户确认）：
1. 注册页/用户弹窗直接勾选交流语言，不再有"首选"下拉；勾选值保存进用户主数据
   users.preferred_communication_language / users.communication_languages。
2. 概览页「待报销金额」只统计已审核（approved）未发放，不含待审核报销；
   新增「待审核报销」卡片（submitted + returned），8 卡片缩小字号一行显示。

不 import app（模块级会执行启动迁移）：用 AST 从 app.py 提取
communication_languages_from_form 真函数与 COMMUNICATION_LANGUAGES 常量，
在 Flask test_request_context 下驱动真实逻辑。
"""

import ast
from pathlib import Path

from flask import Flask, request

ROOT = Path(__file__).resolve().parent


def _extract_namespace():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "COMMUNICATION_LANGUAGES" for target in node.targets
        ):
            wanted.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "communication_languages_from_form":
            wanted.append(node)
    assert len(wanted) == 2, "AST 提取失败：找不到 COMMUNICATION_LANGUAGES 或目标函数"
    namespace = {}
    module = ast.Module(body=wanted, type_ignores=[])
    exec(compile(module, str(ROOT / "app.py"), "exec"), namespace)
    return namespace


def _call_form_parser(data, default="zh-CN"):
    namespace = _extract_namespace()
    flask_app = Flask(__name__)
    namespace["request"] = request
    with flask_app.test_request_context("/", data=data):
        return namespace["communication_languages_from_form"](default)


def test_language_checks_first_selected_becomes_preferred():
    preferred, languages = _call_form_parser({"communication_language": ["en", "es"]})
    assert preferred == "en"
    assert languages == "en,es"


def test_language_checks_empty_falls_back_to_default():
    preferred, languages = _call_form_parser({})
    assert preferred == "zh-CN"
    assert languages == "zh-CN"


def test_language_checks_invalid_values_filtered():
    preferred, languages = _call_form_parser({"communication_language": ["xx", "es"]})
    assert preferred == "es"
    assert languages == "es"


def test_language_checks_legacy_preferred_field_still_honored():
    preferred, languages = _call_form_parser(
        {"preferred_communication_language": "es", "communication_language": ["en"]}
    )
    assert preferred == "es"
    assert languages == "es,en"


def _read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_register_page_direct_language_checks_only():
    html = _read("templates/register.html")
    assert 'name="preferred_communication_language"' not in html, "注册页不应再有首选语言下拉"
    assert "<legend>交流语言</legend>" in html
    assert html.count('name="communication_language"') == 3
    assert "language-checks" in html


def test_users_dialogs_have_language_checks():
    html = _read("templates/users.html")
    # 新建 + 编辑两个弹窗都有交流语言勾选
    assert html.count("<legend>交流语言</legend>") == 2
    assert html.count('name="communication_language"') == 6
    # 编辑弹窗按用户主数据预勾选
    assert "user.communication_languages" in html


def test_user_routes_persist_language_columns():
    source = _read("app.py")
    # 新建用户 insert 与编辑用户 update 都写这两列
    assert "preferred_communication_language, communication_languages" in source
    assert "preferred_communication_language = ?, communication_languages = ?" in source


def test_dashboard_shows_pending_review_expenses():
    html = _read("templates/dashboard.html")
    assert 'class="metric-grid compact"' in html
    assert "<span>待审核报销</span><strong>{{ metrics.pending_expenses|money }}</strong>" in html
    # 发票数量保留（缩小字号方案）
    assert "开票数量" in html


def test_dashboard_compact_css_exists():
    css = _read("static/styles.css")
    assert ".metric-grid.compact" in css
    assert ".language-checks" in css


def test_i18n_covers_new_labels():
    js = _read("static/ui-i18n.js")
    for language, label in [
        ("Communication languages", "en"),
        ("Communicatietalen", "nl"),
        ("Kommunikationssprachen", "de"),
        ("Idiomas de comunicación", "es"),
        ("Expenses pending review", "en"),
        ("Gastos pendientes de revisión", "es"),
    ]:
        assert label in js, f"缺少翻译: {label}"
