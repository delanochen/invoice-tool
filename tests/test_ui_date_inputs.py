from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_non_chinese_date_inputs_stay_locale_independent():
    source = (ROOT / "static" / "ui-i18n.js").read_text(encoding="utf-8")

    assert 'document.querySelectorAll("input[type=\'date\']")' in source
    assert 'input.type = "text"' in source
    assert 'input.placeholder ||= "YYYY-MM-DD"' in source
    assert 'input.type = "date"' not in source
    assert "showPicker" not in source


def test_date_input_enhancement_covers_dynamic_rows_and_iso_validation():
    source = (ROOT / "static" / "ui-i18n.js").read_text(encoding="utf-8")

    assert "new MutationObserver" in source
    assert "isValidIsoDate" in source
    assert 'input.pattern = "[0-9]{4}-[0-9]{2}-[0-9]{2}"' in source
    assert "input.min && input.value < input.min" in source
    assert "input.max && input.value > input.max" in source


def test_reimbursement_rows_preserve_enhanced_date_values():
    source = (ROOT / "static" / "customer-reimbursement.js").read_text(encoding="utf-8")

    assert 'input.dataset.uiDateInput === "true"' in source
