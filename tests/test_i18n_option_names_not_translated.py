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
