from pathlib import Path

from invoice_tool.customers.services import (
    localized_state_name,
    state_code_from_address,
    state_names_for_language,
)

ROOT = Path(__file__).resolve().parents[1]


def test_tail_pattern_with_zip():
    assert state_code_from_address("123 Main St, Houston, TX 77001") == "TX"
    assert state_code_from_address("456 Site Rd, Conroe, TX 77301") == "TX"
    assert state_code_from_address("789 Oak Ave, Austin, TX 78701-1234") == "TX"
    assert state_code_from_address("Phoenix, AZ 85001") == "AZ"
    assert state_code_from_address("Kansas City, MO 64106") == "MO"


def test_tail_pattern_without_zip():
    assert state_code_from_address("Suite 400, Austin, TX") == "TX"
    assert state_code_from_address("Santa Ana, CA") == "CA"
    assert state_code_from_address("San Antonio, TX,") == "TX"


def test_state_before_zip_without_comma():
    assert state_code_from_address("123 Main St, Conroe TX 77301") == "TX"
    assert state_code_from_address("Dallas TX 75201") == "TX"


def test_fallback_standalone_code():
    assert state_code_from_address("123 Main St, Dallas TX 75201, United States") == "TX"
    assert state_code_from_address("Irvine CA 92618, USA") == "CA"


def test_district_and_territories():
    assert state_code_from_address("1600 Pennsylvania Ave NW, Washington DC 20001") == "DC"
    assert state_code_from_address("San Juan, PR 00901") == "PR"


def test_street_suffix_not_state():
    assert state_code_from_address("123 Main St") is None
    assert state_code_from_address("500 West Rd") is None
    assert state_code_from_address("12 Elm Ave NW") is None


def test_non_us_address_ignored():
    assert state_code_from_address("Toronto, ON M5V 2T6") is None
    assert state_code_from_address("Vancouver, BC") is None
    assert state_code_from_address("London, UK") is None
    assert state_code_from_address("Montreal, QC H2X 1Y4") is None


def test_mid_word_codes_not_matched():
    assert state_code_from_address("123 Main St, Miami Florida 33101") is None
    assert state_code_from_address("123 Main St, Memphis, TN 38103") == "TN"


def test_empty_and_blank():
    assert state_code_from_address("") is None
    assert state_code_from_address(None) is None
    assert state_code_from_address("   ") is None


def test_lowercase_input():
    assert state_code_from_address("123 Main St, houston, tx 77001") == "TX"


def test_nbsp_in_address():
    # 复制粘贴带入的 U+00A0 不间断空格（曾导致 SQL 回填漏填）
    assert state_code_from_address("9181\u00a0County\u00a0Rd\u00a0196, Liverpool,\u00a0TX 77577") == "TX"
    assert state_code_from_address("6006\u00a0Stage\u00a0Rd,\u00a0Potosi,\u00a0WI 53820") == "WI"


def test_localized_state_name_zh():
    assert localized_state_name("TX", "zh-CN") == "得克萨斯州"
    assert localized_state_name("CA", "zh-CN") == "加利福尼亚州"
    assert localized_state_name("NJ", "zh-CN") == "新泽西州"
    assert localized_state_name("NY", "zh-CN") == "纽约州"


def test_localized_state_name_en_and_es():
    assert localized_state_name("TX", "en") == "Texas"
    assert localized_state_name("NY", "en") == "New York"
    assert localized_state_name("NY", "es") == "Nueva York"
    assert localized_state_name("TX", "es") == "Texas"


def test_localized_state_name_fallback_and_unknown():
    # nl/de 未单独提供州名时回退英文名
    assert localized_state_name("TX", "nl") == "Texas"
    assert localized_state_name("CA", "de") == "California"
    assert localized_state_name("ZZ", "en") == ""
    assert localized_state_name("", "zh-CN") == ""
    assert localized_state_name(None, "zh-CN") == ""


def test_state_names_for_language_shape():
    names = state_names_for_language("en")
    assert names["TX"] == "Texas"
    assert len(names) == len(state_names_for_language("zh-CN"))
    assert all(name for name in names.values())
