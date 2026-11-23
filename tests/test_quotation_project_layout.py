"""Project-layout quotation math and PDF. No database required."""

import re
import unittest
from decimal import Decimal
from io import BytesIO

from flask import Flask
from pypdf import PdfReader
from werkzeug.datastructures import MultiDict

from invoice_tool.quotations.documents import build_quotation_pdf
from invoice_tool.quotations.project_layout import (
    cjk_font_path,
    parse_project_lines,
    project_amounts,
    project_form_values,
)
from invoice_tool.quotations.routes import quotation_form_values

LONG_NOTE = (
    "Includes coolant filling, leak check, and a 24-hour observation period. "
    "系统调试与冷却液加注，含泄漏检查及24小时观察。"
)


def _example_lists(**overrides):
    payload = {
        "sections": [
            "Machine deployment, machine cooling pipe installation",
            "Cardboard box recycling",
            "System commissioning, coolant injection",
        ],
        "scopes": [
            "Deploy machines and install cooling pipes",
            "Recycle cardboard boxes",
            "Commission the system and inject coolant",
        ],
        "qtys": ["2000", "2000", ""],
        "units": ["台", "项", ""],
        "customs": ["", "", ""],
        "prices": ["14", "3", ""],
        "totals": ["1", "1", "16000"],
        "notes": ["Completed in one week", "", LONG_NOTE],
    }
    payload.update(overrides)
    return payload


def _example_lines():
    payload = _example_lists()
    lines, subtotal = parse_project_lines(
        payload["sections"], payload["scopes"], payload["qtys"], payload["units"],
        payload["customs"], payload["prices"], payload["totals"], payload["notes"],
    )
    return lines, subtotal


def _pdf_text(payload):
    reader = PdfReader(BytesIO(payload))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _compact(text):
    return "".join(text.split())


def _has_embedded_font(payload):
    reader = PdfReader(BytesIO(payload))
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        fonts = resources.get("/Font") or {}
        if hasattr(fonts, "get_object"):
            fonts = fonts.get_object()
        for font in fonts.values():
            font = font.get_object() if hasattr(font, "get_object") else font
            descriptor = font.get("/FontDescriptor") if hasattr(font, "get") else None
            if descriptor is None:
                continue
            descriptor = descriptor.get_object() if hasattr(descriptor, "get_object") else descriptor
            if any(key in descriptor for key in ("/FontFile2", "/FontFile3", "/FontFile")):
                return True
    return False


def _project_quote(lines, **overrides):
    amounts = project_amounts(sum(line["line_total"] for line in lines), overrides.pop("tax", 0), overrides.pop("discount", 0))
    quote = {
        "layout": "project",
        "quotation_number": "QT2611001",
        "quotation_date": "2026-11-22",
        "document_title": "Appendix I: Installation Checklist",
        "party_a": "bitdeer",
        "party_b": "WAN MEP LLC",
        "currency": "USD",
        "project_terms": "Prices exclude tax unless stated. 本报价不含税，除非另有说明。",
        "include_standard_clauses": 0,
        "project_lines": lines,
    }
    quote.update(amounts)
    quote.update(overrides)
    return quote


def _render_pdf(quote, language=None):
    buffer = BytesIO()
    build_quotation_pdf(quote, buffer, language=language)
    return buffer.getvalue()


class ProjectLayoutMathTests(unittest.TestCase):
    def test_example_mixes_itemized_and_lump_sum_to_50000(self):
        lines, subtotal = _example_lines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0]["line_total"], Decimal("28000.00"))
        self.assertFalse(lines[0]["lump_sum"])
        self.assertEqual(lines[0]["unit"], "台")
        self.assertEqual(lines[1]["line_total"], Decimal("6000.00"))
        self.assertEqual(lines[2]["line_total"], Decimal("16000.00"))
        self.assertTrue(lines[2]["lump_sum"])
        self.assertIsNone(lines[2]["qty"])
        self.assertEqual(lines[2]["notes"], LONG_NOTE)
        self.assertEqual(subtotal, Decimal("50000.00"))
        amounts = project_amounts(subtotal, "0", "0")
        self.assertEqual(amounts["final_amount"], Decimal("50000.00"))

    def test_posted_line_total_is_ignored_when_qty_and_price_are_set(self):
        lines, _subtotal = _example_lines()
        self.assertEqual(lines[0]["line_total"], Decimal("28000.00"))

    def test_qty_without_price_stays_lump_sum(self):
        lines, subtotal = parse_project_lines(
            ["Only quantity"], [""], ["5"], ["台"], [""], [""], ["800"], [""],
        )
        self.assertTrue(lines[0]["lump_sum"])
        self.assertEqual(lines[0]["line_total"], Decimal("800.00"))
        self.assertEqual(subtotal, Decimal("800.00"))

    def test_custom_unit_text(self):
        lines, _subtotal = parse_project_lines(
            ["Custom"], [""], ["2"], ["__custom__"], ["套件"], ["10"], [""], [""],
        )
        self.assertEqual(lines[0]["unit"], "套件")
        self.assertEqual(lines[0]["line_total"], Decimal("20.00"))

    def test_tax_and_discount_follow_existing_quote_rules(self):
        amounts = project_amounts(Decimal("50000.00"), "100", "50")
        self.assertEqual(amounts["total"], Decimal("50100.00"))
        self.assertEqual(amounts["final_amount"], Decimal("50050.00"))

    def test_empty_rows_are_dropped(self):
        lines, subtotal = parse_project_lines(
            ["", "Keep"], ["", ""], ["", ""], ["", ""], ["", ""], ["", ""], ["", "15"], ["", ""],
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(subtotal, Decimal("15.00"))


class ProjectLayoutFormTests(unittest.TestCase):
    def test_project_payment_options_and_defaults(self):
        defaults = project_form_values(MultiDict())
        self.assertEqual(defaults["payment_terms"], "net_30")
        self.assertEqual(defaults["advance_payment_percent"], 0)
        for percentage in ("0", "30", "50"):
            values = project_form_values(MultiDict({
                "payment_terms": "net_45", "advance_payment_percent": percentage,
                "invoice_frequency": "monthly",
            }))
            self.assertEqual(values["payment_terms"], "net_45")
            self.assertEqual(values["advance_payment_percent"], int(percentage))
            self.assertEqual(values["invoice_frequency"], "monthly")
        invalid = project_form_values(MultiDict({
            "payment_terms": "net_99", "advance_payment_percent": "25",
        }))
        self.assertEqual(invalid["payment_terms"], "net_30")
        self.assertEqual(invalid["advance_payment_percent"], 0)

    def setUp(self):
        self.app = Flask(__name__)

    def _post(self, fields):
        with self.app.test_request_context("/quotations/new", method="POST", data=MultiDict(fields)):
            return quotation_form_values(None)

    def test_selector_post_without_layout_stays_hourly(self):
        values = self._post([
            ("quotation_date", "2026-10-01"),
            ("pricing_type", "estimated"),
            ("pricing_qty_regular_hours", "40"),
            ("pricing_qty_mileage", "650"),
            ("tax", "0"),
            ("rate_regular_hours", "120"),
            ("rate_mileage", "0.67"),
        ])
        self.assertEqual(values["layout"], "hourly")
        self.assertNotIn("_project_lines", values)
        self.assertEqual(values["subtotal"], Decimal("5235.50"))
        self.assertEqual(values["document_title"], "")
        self.assertEqual(values["party_b"], "")

    def test_existing_hourly_quote_cannot_be_switched_by_posting_project(self):
        with self.app.test_request_context("/quotations/1/edit", method="POST", data=MultiDict([
            ("layout", "project"),
            ("quotation_date", "2026-10-01"),
            ("quotation_number", "QT2610001"),
            ("pricing_type", "estimated"),
            ("pricing_qty_regular_hours", "40"),
            ("rate_regular_hours", "120"),
            ("tax", "0"),
            ("project_section", "Should be ignored"),
            ("project_line_total", "50000"),
        ])):
            values = quotation_form_values(None, {"layout": "hourly"})
        self.assertEqual(values["layout"], "hourly")
        self.assertNotIn("_project_lines", values)
        self.assertEqual(values["subtotal"], Decimal("4800.00"))

    def test_project_form_stores_the_example_total(self):
        payload = _example_lists()
        fields = [
            ("layout", "project"),
            ("quotation_date", "2026-11-22"),
            ("document_title", "Appendix I: Installation Checklist"),
            ("party_a", "bitdeer"),
            ("party_b", "WAN MEP LLC"),
            ("currency", "USD"),
            ("project_terms", "Footer disclaimer"),
            ("project_description", "Install cooling pipes"),
            ("project_name", "Cooling Install"),
            ("project_location", "Fort Worth, TX"),
            ("contact", "Ada"),
            ("po_no", "PO-9"),
            ("prepared_by", "QA Tester"),
            ("tax", "0"),
            ("discount_amount", "0"),
        ]
        names = (
            ("project_section", "sections"),
            ("project_scope", "scopes"),
            ("project_qty", "qtys"),
            ("project_unit", "units"),
            ("project_unit_custom", "customs"),
            ("project_unit_price", "prices"),
            ("project_line_total", "totals"),
            ("project_notes", "notes"),
        )
        for form_name, key in names:
            for value in payload[key]:
                fields.append((form_name, value))
        values = self._post(fields)
        self.assertEqual(values["layout"], "project")
        self.assertEqual(values["subtotal"], Decimal("50000.00"))
        self.assertEqual(values["final_amount"], Decimal("50000.00"))
        self.assertEqual(values["pricing_lines"], "[]")
        self.assertEqual(values["party_a"], "bitdeer")
        self.assertEqual(values["party_b"], "WAN MEP LLC")
        self.assertEqual(values["document_title"], "Appendix I: Installation Checklist")
        self.assertEqual(values["project_description"], "Install cooling pipes")
        self.assertEqual(values["project_name"], "Cooling Install")
        self.assertEqual(values["project_location"], "Fort Worth, TX")
        self.assertEqual(values["contact"], "Ada")
        self.assertEqual(values["po_no"], "PO-9")
        self.assertEqual(values["prepared_by"], "QA Tester")
        self.assertEqual(values["settlement_mode"], "fixed")
        self.assertEqual(len(values["_project_lines"]), 3)
        self.assertEqual(project_form_values(MultiDict(fields))["customer"], "bitdeer")


class ProjectLayoutPdfTests(unittest.TestCase):
    def test_cjk_font_is_available_for_embedding(self):
        self.assertTrue(cjk_font_path())

    def test_both_languages_and_neutral_header(self):
        lines, _subtotal = _example_lines()
        payload_zh = _render_pdf(_project_quote(lines), "zh")
        payload_en = _render_pdf(_project_quote(lines), "en")
        self.assertTrue(payload_zh.startswith(b"%PDF"))
        self.assertTrue(payload_en.startswith(b"%PDF"))
        self.assertTrue(_has_embedded_font(payload_zh))
        self.assertTrue(_has_embedded_font(payload_en))
        chinese = _compact(_pdf_text(payload_zh))
        english = _compact(_pdf_text(payload_en))
        for text in (chinese, english):
            self.assertIn("bitdeer", text)
            self.assertIn("WANMEPLLC", text)
            self.assertIn("AppendixI:InstallationChecklist", text)
            self.assertIn("Completedinoneweek", text)
            self.assertIn("50000.00", text.replace(",", ""))
            self.assertIn("28000.00", text.replace(",", ""))
            self.assertIn("16000.00", text.replace(",", ""))
            self.assertNotIn("PRASINOSPOWER", text)
            self.assertNotIn("FIELDSERVICES", text)
            self.assertNotIn("STANDARDQUOTATIONTERMS", text)
        self.assertIn("甲方", chinese)
        self.assertIn("乙方", chinese)
        self.assertIn("项目名称", chinese)
        self.assertIn("总价包干", chinese)
        self.assertIn("本报价不含税", chinese)
        self.assertIn("PartyA", english)
        self.assertIn("PartyB", english)
        self.assertIn("ScopeofWork", english)
        self.assertIn("Lumpsum", english)
        self.assertIn("GrandTotal", english)
        self.assertNotIn("ProfessionalServices", english)
        self.assertNotIn("SCOPEOFWORK", english)
        self.assertNotIn("QuotationNo.", english)

    def test_prasinos_front_matches_hourly_template(self):
        lines, _subtotal = _example_lines()
        quote = _project_quote(
            lines,
            party_b="Prasinos Power",
            customer="bitdeer",
            contact="Ada Lovelace",
            prepared_by="QA Tester",
            project_name="Cooling Install",
            project_no="P-14",
            project_location="Fort Worth, TX",
            po_no="PO-9",
            project_description="Install cooling pipes for 2000 machines",
            valid_until="2026-12-22",
            include_standard_clauses=1,
        )
        english = _compact(_pdf_text(_render_pdf(quote, "en")))
        chinese = _compact(_pdf_text(_render_pdf(quote, "zh")))
        for text in (english, chinese):
            self.assertIn("Installcoolingpipesfor2000machines", text)
            self.assertIn("QT2611001", text)
            self.assertIn("bitdeer", text)
            self.assertIn("YongmingChen", text)
            self.assertIn("PRASINOSPOWER", text)
            self.assertNotIn("PartyA", text)
            self.assertIn("50000.00", text.replace(",", ""))
        self.assertIn("ProfessionalServices", english)
        self.assertIn("QuotationNo.", english)
        self.assertIn("SCOPEOFWORK", english)
        self.assertIn("PRICINGSUMMARY", english)
        self.assertNotRegex(english, r"FIELDSERVICES\W*RETROFIT\W*COMMISSIONING")
        self.assertIn("STANDARDQUOTATIONTERMSANDCONDITIONS", english)
        self.assertIn("UnitPrice", english)
        self.assertNotIn("项目名称", english)
        self.assertIn("专业服务", chinese)
        self.assertIn("报价编号", chinese)
        self.assertIn("工作范围", chinese)
        self.assertIn("定价汇总", chinese)
        self.assertIn("项目名称", chinese)
        self.assertIn("标准报价条款与条件", chinese)
        self.assertNotIn("ProfessionalServices", chinese)
        self.assertNotIn("FIELDSERVICES", chinese)
        self.assertNotIn("Unitprice", chinese)

    def test_hourly_pdf_is_unchanged_by_language_or_missing_layout(self):
        quote = {
            "quotation_number": "QT-HOURLY",
            "pricing_type": "estimated",
            "currency": "USD",
            "customer": "Sunfield Solar LLC",
            "pricing_lines": "[]",
            "rate_schedule": "[]",
            "subtotal": 0,
            "tax": 0,
            "total": 0,
        }
        for language in (None, "en"):
            text = _pdf_text(_render_pdf(quote, language))
            self.assertIn("QUOTATION", text)
            self.assertIn("Professional Services", text)
            self.assertIn("PRASINOS POWER", text)
            self.assertNotIn("甲方", text)
            self.assertNotIn("报价单", text)
        chinese = _pdf_text(_render_pdf(quote, "zh"))
        self.assertIn("报价单", chinese)
        self.assertIn("专业服务", chinese)
        self.assertNotIn("Professional Services", chinese)
        self.assertNotIn("QUOTATION", chinese)


def _strip_allowed(text, names):
    for token in names:
        text = text.replace(token, " ")
    for char in "台项米个套小时按":
        text = text.replace(char, " ")
    return text


class PdfLanguagePurityTests(unittest.TestCase):
    def test_project_pdf_languages_do_not_mix(self):
        line = {
            "project_id": 7,
            "section_name": "Machine deployment",
            "catalog_name": "Machine deployment",
            "catalog_name_en": "Machine deployment",
            "catalog_name_zh": "设备部署",
            "scope_of_work": "Install pipes",
            "scope_of_work_en": "Install pipes",
            "scope_of_work_zh": "安装管道",
            "qty": 2,
            "unit": "台",
            "unit_price": 10,
            "line_total": 20,
            "notes": "Done in a week",
            "notes_en": "Done in a week",
            "notes_zh": "一周完成",
            "lump_sum": False,
        }
        quote = _project_quote(
            [line],
            party_b="Prasinos Power",
            customer="Bitdeer",
            party_a="Bitdeer",
            prepared_by="Yongming Chen",
            contact="Ada Lovelace",
            project_name="North site",
            project_location="Fort Worth",
            document_title_en="Installation checklist",
            document_title_zh="安装清单",
            project_description_en="Cooling pipe work",
            project_description_zh="冷却管道工程",
            project_terms_en="Tax excluded",
            project_terms_zh="不含税",
            include_standard_clauses=1,
        )
        names = [
            "Prasinos Power", "PRASINOS POWER", "Yongming Chen", "Bitdeer", "USD",
            "QT2611001", "Ada Lovelace", "Fort Worth", "North site",
            "Installation checklist", "Machine deployment", "Install pipes",
            "Done in a week", "Cooling pipe work", "Tax excluded",
        ]
        english = _strip_allowed(_pdf_text(_render_pdf(quote, "en")), names)
        chinese = _strip_allowed(_pdf_text(_render_pdf(quote, "zh")), names)
        self.assertEqual(re.findall(r"[\u4e00-\u9fff]", english), [])
        self.assertEqual(re.findall(r"[A-Za-z]{2,}", chinese), [])

    def test_hourly_pdf_languages_do_not_mix(self):
        quote = {
            "layout": "hourly",
            "quotation_number": "QT2611001",
            "quotation_date": "2026-11-23",
            "currency": "USD",
            "pricing_type": "estimated",
            "pricing_lines": "[]",
            "rate_schedule": "[]",
            "customer": "Bitdeer",
            "prepared_by": "Yongming Chen",
            "subtotal": 0,
            "tax": 0,
            "total": 0,
        }
        names = ["Prasinos Power", "PRASINOS POWER", "Yongming Chen", "Bitdeer", "USD", "QT2611001"]
        english = _strip_allowed(_pdf_text(_render_pdf(quote, "en")), names)
        chinese = _strip_allowed(_pdf_text(_render_pdf(quote, "zh")), names)
        self.assertEqual(re.findall(r"[\u4e00-\u9fff]", english), [])
        self.assertEqual(re.findall(r"[A-Za-z]{2,}", chinese), [])


class LayoutSelectorTemplateTests(unittest.TestCase):
    def test_new_page_offers_both_layouts(self):
        from pathlib import Path
        html = Path("templates/quotation_layout_select.html").read_text(encoding="utf-8")
        self.assertIn("按工时报价", html)
        self.assertIn("按项目/安装清单", html)
        self.assertIn("layout='hourly'", html)
        self.assertIn("layout='project'", html)


if __name__ == "__main__":
    unittest.main()
