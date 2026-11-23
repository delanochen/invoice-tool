"""Project / installation-checklist quotation layout.

Hourly quotes keep using documents.build_quotation_pdf. This module owns the
free-form line items, header parties, and the bilingual project PDF.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from invoice_tool.commercial_billing import quote_amounts
from invoice_tool.quotations.documents import (
    BORDER,
    DARK_GREEN,
    DOC_MARGIN,
    HEADER_GRAY,
    MID_GREEN,
    PAGE_H,
    PAGE_W,
    TEXT_DARK,
    VALUE_BG,
    WHITE,
    _esc,
    _money,
    _signature_table as _hourly_signature_table,
    _styles as _hourly_styles,
    _terms_block,
    quotation_cover_story,
)

LAYOUT_HOURLY = "hourly"
LAYOUT_PROJECT = "project"
LAYOUTS = (LAYOUT_HOURLY, LAYOUT_PROJECT)

PROJECT_UNITS = ("台", "项", "米", "个", "套", "小时", "按台")
CUSTOM_UNIT = "__custom__"
DEFAULT_DOCUMENT_TITLE = "报价单"
DEFAULT_PARTY_B = "Prasinos Power"
PRASINOS_LINE_HEADERS = (
    "No.",
    "项目名称",
    "工作范围",
    "Qty",
    "Unit",
    "Unit price",
    "Amount",
    "Notes",
)

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_CJK_CANDIDATES = (
    os.path.join(_FONT_DIR, "NotoSansCJKsc-Regular.otf"),
    os.path.join(_FONT_DIR, "NotoSansCJK-Regular.ttc"),
    os.path.join(_FONT_DIR, "NotoSansSC-Regular.otf"),
    os.path.join(_FONT_DIR, "wqy-microhei.ttc"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    os.path.join(_FONT_DIR, "DroidSansFallbackFull.ttf"),
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
)
_registered_font = None

LABELS = {
    "zh": {
        "party_a": "甲方",
        "party_b": "乙方",
        "total": "合计",
        "no": "序号",
        "section": "项目名称",
        "scope": "工作范围",
        "qty": "数量",
        "unit": "单位",
        "price": "单价",
        "line_total": "金额",
        "notes": "备注",
        "grand": "合计",
        "subtotal": "小计",
        "tax": "税",
        "discount": "折扣",
        "lump": "总价包干",
        "date": "日期",
        "signature": "签字",
        "page": "第 {n} 页",
    },
    "en": {
        "party_a": "Party A",
        "party_b": "Party B",
        "total": "Total",
        "no": "No.",
        "section": "Section",
        "scope": "Scope of Work",
        "qty": "Qty",
        "unit": "Unit",
        "price": "Unit Price",
        "line_total": "Line Total",
        "notes": "Notes",
        "grand": "Grand Total",
        "subtotal": "Subtotal",
        "tax": "Tax",
        "discount": "Discount",
        "lump": "Lump sum",
        "date": "Date",
        "signature": "Signature",
        "page": "Page {n}",
    },
}


def normalize_layout(value, default=LAYOUT_HOURLY):
    text = str(value or "").strip().lower()
    if text in LAYOUTS:
        return text
    return default


def quotation_layout_value(quotation):
    """Read layout from a row or dict. Missing column means the hourly quote."""
    if quotation is None:
        return LAYOUT_HOURLY
    try:
        if "layout" not in quotation.keys():
            return LAYOUT_HOURLY
        value = quotation["layout"]
    except (KeyError, TypeError, AttributeError):
        return LAYOUT_HOURLY
    return normalize_layout(value)


def normalize_language(language):
    text = str(language or "zh").strip().lower()
    if text.startswith("en"):
        return "en"
    return "zh"


def is_prasinos_party(name):
    compact = "".join(str(name or "").lower().split())
    return "prasinos" in compact


def find_cjk_font():
    for path in _CJK_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return None


def cjk_font_name():
    """Register and return an embedded CJK face, or STSong-Light if none exists."""
    global _registered_font
    if _registered_font:
        return _registered_font
    path = find_cjk_font()
    if path:
        kwargs = {"subfontIndex": 0} if path.lower().endswith(".ttc") else {}
        pdfmetrics.registerFont(TTFont("QuotationCJK", path, **kwargs))
        _registered_font = "QuotationCJK"
        return _registered_font
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _registered_font = "STSong-Light"
    return _registered_font


def cjk_font_path():
    return find_cjk_font()


def _decimal_or_none(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _at(values, index):
    if index < len(values):
        return values[index]
    return ""


def resolve_unit(unit, custom):
    selected = str(unit or "").strip()
    written = str(custom or "").strip()
    if selected == CUSTOM_UNIT:
        return written
    return selected


def compute_line_total(qty, unit_price, manual_total):
    """Itemized lines are qty x price. Anything else is a manual lump sum."""
    if qty is not None and unit_price is not None:
        return (qty * unit_price).quantize(Decimal("0.01"))
    manual = manual_total if manual_total is not None else Decimal("0")
    return manual.quantize(Decimal("0.01"))


def parse_project_lines(sections, scopes, qtys, units, customs, prices, totals, notes):
    """Parse parallel form lists into stored line dicts and their subtotal.

    A posted line total is ignored when both quantity and unit price are set.
    Completely empty rows are dropped.
    """
    sequences = (sections, scopes, qtys, units, customs, prices, totals, notes)
    count = max((len(item) for item in sequences), default=0)
    lines = []
    for index in range(count):
        section = str(_at(sections, index) or "").strip()
        scope = str(_at(scopes, index) or "").strip()
        note = str(_at(notes, index) or "").strip()
        unit = resolve_unit(_at(units, index), _at(customs, index))
        qty = _decimal_or_none(_at(qtys, index))
        price = _decimal_or_none(_at(prices, index))
        manual = _decimal_or_none(_at(totals, index))
        if not any((section, scope, note, unit)) and qty is None and price is None and manual is None:
            continue
        total = compute_line_total(qty, price, manual)
        lines.append({
            "section_name": section,
            "scope_of_work": scope,
            "qty": qty,
            "unit": unit,
            "unit_price": price,
            "line_total": total,
            "notes": note,
            "lump_sum": qty is None or price is None,
        })
    subtotal = sum((line["line_total"] for line in lines), Decimal("0")).quantize(Decimal("0.01"))
    return lines, subtotal


def project_amounts(subtotal, tax, discount):
    """Reuse the hourly tax / discount rules: final = (subtotal + tax) - discount."""
    subtotal = (_decimal_or_none(subtotal) or Decimal("0")).quantize(Decimal("0.01"))
    tax_amount = (_decimal_or_none(tax) or Decimal("0")).quantize(Decimal("0.01"))
    total = (subtotal + tax_amount).quantize(Decimal("0.01"))
    amounts = quote_amounts(total, discount)
    return {
        "subtotal": subtotal,
        "tax": tax_amount,
        "total": total,
        "gross_total": amounts["gross_total"],
        "discount_amount": amounts["discount_amount"],
        "final_amount": amounts["final_amount"],
    }


def _form_get(form, name, default=""):
    if form is None:
        return default
    value = form.get(name, default)
    if value is None:
        return default
    return str(value).strip()


def _form_list(form, name):
    if form is None or not hasattr(form, "getlist"):
        return []
    return list(form.getlist(name))


def clauses_enabled(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def project_form_values(form):
    """Build the quotations column dict for a project-layout post.

    Hourly pricing rows, the rate card and the 30 standard clauses are not
    filled from this form. Tax and discount still follow quote_amounts.
    """
    lines, subtotal = parse_project_lines(
        _form_list(form, "project_section"),
        _form_list(form, "project_scope"),
        _form_list(form, "project_qty"),
        _form_list(form, "project_unit"),
        _form_list(form, "project_unit_custom"),
        _form_list(form, "project_unit_price"),
        _form_list(form, "project_line_total"),
        _form_list(form, "project_notes"),
    )
    amounts = project_amounts(subtotal, _form_get(form, "tax", "0"), _form_get(form, "discount_amount", "0"))
    party_a = _form_get(form, "party_a") or _form_get(form, "customer")
    customer = _form_get(form, "customer") or party_a
    client_id = _form_get(form, "client_id")
    provides = []
    if hasattr(form, "getlist"):
        provides = [
            value for value in form.getlist("customer_provides")
            if value in {"access", "escort", "loto", "permits"}
        ]
    values = {
        "quotation_number": _form_get(form, "quotation_number"),
        "quotation_date": _form_get(form, "quotation_date"),
        "valid_until": _form_get(form, "valid_until"),
        "prepared_by": _form_get(form, "prepared_by"),
        "customer": customer,
        "contact": _form_get(form, "contact"),
        "project_name": _form_get(form, "project_name"),
        "project_no": _form_get(form, "project_no"),
        "project_location": _form_get(form, "project_location"),
        "po_no": _form_get(form, "po_no"),
        "status": _form_get(form, "status", "draft") or "draft",
        "currency": _form_get(form, "currency", "USD") or "USD",
        "client_id": int(client_id) if client_id.isdigit() else None,
        "project_description": _form_get(form, "project_description"),
        "scope_1": _form_get(form, "scope_1"),
        "scope_2": _form_get(form, "scope_2"),
        "scope_3": _form_get(form, "scope_3"),
        "pricing_lines": "[]",
        "subtotal": amounts["subtotal"],
        "tax": amounts["tax"],
        "total": amounts["total"],
        "gross_total": amounts["gross_total"],
        "discount_amount": amounts["discount_amount"],
        "final_amount": amounts["final_amount"],
        "driving_billing_mode": "mileage_only",
        "settlement_mode": "actual",
        "rate_schedule": "[]",
        "crew_size": _form_get(form, "crew_size"),
        "workdays": _form_get(form, "workdays"),
        "hours_per_day": _form_get(form, "hours_per_day"),
        "expected_start_date": _form_get(form, "expected_start_date"),
        "expected_completion": _form_get(form, "expected_completion"),
        "normal_working_hours": _form_get(form, "normal_working_hours"),
        "customer_provides": ",".join(provides),
        "assumptions_other": _form_get(form, "assumptions_other"),
        "payment_terms": _form_get(form, "payment_terms", "net_30") or "net_30",
        "payment_terms_other": _form_get(form, "payment_terms_other"),
        "quotation_validity": _form_get(form, "quotation_validity", "30 calendar days") or "30 calendar days",
        "invoice_frequency": _form_get(form, "invoice_frequency", "upon_completion") or "upon_completion",
        "pricing_type": "estimated",
        "tax_note": _form_get(form, "tax_note", "Excluded unless stated") or "Excluded unless stated",
        "notes": _form_get(form, "notes"),
        "travel_expenses": "[]",
        "layout": LAYOUT_PROJECT,
        "document_title": _form_get(form, "document_title") or DEFAULT_DOCUMENT_TITLE,
        "party_a": party_a,
        "party_b": _form_get(form, "party_b") or DEFAULT_PARTY_B,
        "project_terms": _form_get(form, "project_terms"),
        "include_standard_clauses": 1 if clauses_enabled(form.get("include_standard_clauses") if form else "") else 0,
        "_project_lines": lines,
    }
    return values


def format_quantity(value):
    if value in (None, ""):
        return ""
    number = Decimal(str(value))
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def line_form_state(line):
    """Shape a stored line for the editor, including the custom-unit split."""
    unit = str(line.get("unit") or "")
    custom = ""
    selected = unit
    if unit and unit not in PROJECT_UNITS:
        selected = CUSTOM_UNIT
        custom = unit
    qty = line.get("qty")
    price = line.get("unit_price")
    return {
        "section_name": line.get("section_name") or "",
        "scope_of_work": line.get("scope_of_work") or "",
        "qty": "" if qty in (None, "") else format_quantity(qty),
        "unit": selected,
        "unit_custom": custom,
        "unit_price": "" if price in (None, "") else format_quantity(price),
        "line_total": f"{Decimal(str(line.get('line_total') or 0)):.2f}",
        "notes": line.get("notes") or "",
        "lump_sum": bool(line.get("lump_sum")) if "lump_sum" in line else (qty in (None, "") or price in (None, "")),
        "display_unit": custom if selected == CUSTOM_UNIT else unit,
    }


def _styles(font_name):
    def style(name, **kwargs):
        kwargs.setdefault("fontName", font_name)
        kwargs.setdefault("textColor", TEXT_DARK)
        return ParagraphStyle(name, **kwargs)
    return {
        "title": style("PTitle", fontSize=16, leading=20, alignment=TA_CENTER, textColor=DARK_GREEN),
        "meta": style("PMeta", fontSize=8, leading=11, alignment=TA_CENTER, textColor=HEADER_GRAY),
        "head": style("PHead", fontSize=7.5, leading=9.5, textColor=WHITE, alignment=TA_CENTER),
        "cell": style("PCell", fontSize=8, leading=10.5, alignment=TA_LEFT),
        "cell_center": style("PCellCenter", fontSize=8, leading=10.5, alignment=TA_CENTER),
        "cell_right": style("PCellRight", fontSize=8, leading=10.5, alignment=TA_RIGHT),
        "party": style("PParty", fontSize=9, leading=12, textColor=WHITE),
        "total_label": style("PTotalLabel", fontSize=9, leading=12, textColor=WHITE, alignment=TA_RIGHT),
        "total_value": style("PTotalValue", fontSize=10, leading=13, textColor=WHITE, alignment=TA_RIGHT),
        "disclaimer": style("PDisclaimer", fontSize=8, leading=11, spaceBefore=8),
        "sig_head": style("PSigHead", fontSize=9, leading=12, textColor=WHITE, alignment=TA_CENTER),
        "sig": style("PSig", fontSize=9, leading=12),
    }


def _money_with_currency(value, currency):
    return f"$ {_money(value)} {currency or 'USD'}".strip()


def _quote_lines(quote):
    raw = quote.get("project_lines") or []
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw) if raw.strip() else []
        except (ValueError, TypeError):
            raw = []
    lines = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        qty = item.get("qty")
        price = item.get("unit_price") if "unit_price" in item else item.get("price")
        total = item.get("line_total")
        if total in (None, ""):
            total = item.get("total") or 0
        lump = item.get("lump_sum")
        if lump is None:
            lump = qty in (None, "") or price in (None, "")
        lines.append({
            "section_name": item.get("section_name") or item.get("section") or "",
            "scope_of_work": item.get("scope_of_work") or item.get("scope") or "",
            "qty": qty,
            "unit": item.get("unit") or "",
            "unit_price": price,
            "line_total": total,
            "notes": item.get("notes") or "",
            "lump_sum": bool(lump),
        })
    return lines


def _page_decorator(font_name, party_b, prasinos, number, language):
    labels = LABELS[language]

    def draw(canvas, doc):
        canvas.saveState()
        if prasinos:
            canvas.setFillColor(DARK_GREEN)
            canvas.setFont("Helvetica-Bold", 16)
            canvas.drawCentredString(PAGE_W / 2.0, PAGE_H - 16 * mm, "PRASINOS POWER")
            canvas.setFillColor(HEADER_GRAY)
            canvas.setFont("Helvetica", 7)
            canvas.drawCentredString(
                PAGE_W / 2.0, PAGE_H - 21 * mm,
                "FIELD SERVICES \u2022 RETROFIT \u2022 COMMISSIONING",
            )
            footer_font = "Helvetica"
            footer_left = "Prasinos Power  |  Confidential"
        else:
            canvas.setFillColor(DARK_GREEN)
            canvas.setFont(font_name, 13)
            canvas.drawCentredString(PAGE_W / 2.0, PAGE_H - 18 * mm, party_b or "QUOTATION")
            footer_font = font_name
            footer_left = number or ""
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(DOC_MARGIN, PAGE_H - 24.5 * mm, PAGE_W - DOC_MARGIN, PAGE_H - 24.5 * mm)
        canvas.setFillColor(HEADER_GRAY)
        canvas.setFont(footer_font, 8)
        canvas.drawString(DOC_MARGIN, 7 * mm, footer_left)
        canvas.drawRightString(PAGE_W - DOC_MARGIN, 7 * mm, labels["page"].format(n=doc.page))
        canvas.line(DOC_MARGIN, 10 * mm, PAGE_W - DOC_MARGIN, 10 * mm)
        canvas.restoreState()

    return draw


def _signature_table(party_a, party_b, styles, labels, prasinos, quotation_date):
    from reportlab.platypus import Image

    from invoice_tool.quotations.documents import SIGNATURE_IMAGE

    right_signature = Paragraph(_esc(labels["signature"]), styles["sig"])
    if prasinos and os.path.isfile(SIGNATURE_IMAGE):
        right_signature = Image(SIGNATURE_IMAGE, width=55 * mm, height=15 * mm)
    data = [
        [
            Paragraph(_esc(f"{labels['party_a']} {party_a}".strip()), styles["sig_head"]),
            Paragraph(_esc(f"{labels['party_b']} {party_b}".strip()), styles["sig_head"]),
        ],
        [Paragraph(_esc(labels["signature"]), styles["sig"]), right_signature],
        [
            Paragraph(_esc(f"{labels['date']}  {quotation_date or ''}"), styles["sig"]),
            Paragraph(_esc(f"{labels['date']}  {quotation_date or ''}"), styles["sig"]),
        ],
    ]
    table = Table(data, colWidths=[94 * mm, 94 * mm], rowHeights=[None, 22 * mm, None])
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), MID_GREEN),
        ("BACKGROUND", (0, 1), (-1, -1), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])
    table.setStyle(style)
    return table


def build_project_quotation_pdf(quote, path, language=None):
    """Render a project-layout quotation. ``path`` may be a file path or a buffer."""
    language = normalize_language(language)
    labels = LABELS[language]
    font_name = cjk_font_name()
    styles = _styles(font_name)
    party_a = str(quote.get("party_a") or quote.get("customer") or "").strip()
    party_b = str(quote.get("party_b") or "").strip() or DEFAULT_PARTY_B
    title = str(quote.get("document_title") or "").strip() or DEFAULT_DOCUMENT_TITLE
    currency = quote.get("currency") or "USD"
    prasinos = is_prasinos_party(party_b)
    lines = _quote_lines(quote)
    final_amount = quote.get("final_amount")
    if final_amount in (None, ""):
        final_amount = quote.get("total") or 0
    tax = quote.get("tax") or 0
    discount = quote.get("discount_amount") or 0
    subtotal = quote.get("subtotal")
    if subtotal in (None, ""):
        subtotal = sum((Decimal(str(line["line_total"] or 0)) for line in lines), Decimal("0"))

    if prasinos:
        header_labels = PRASINOS_LINE_HEADERS
    else:
        header_labels = [
            labels[key]
            for key in ("no", "section", "scope", "qty", "unit", "price", "line_total", "notes")
        ]
    header = [Paragraph(_esc(text), styles["head"]) for text in header_labels]
    data = [header]
    for index, line in enumerate(lines, start=1):
        price_text = labels["lump"] if line["lump_sum"] else _money_with_currency(line["unit_price"], currency)
        data.append([
            Paragraph(_esc(str(index)), styles["cell_center"]),
            Paragraph(_esc(line["section_name"]) or "&nbsp;", styles["cell"]),
            Paragraph(_esc(line["scope_of_work"]) or "&nbsp;", styles["cell"]),
            Paragraph(_esc(format_quantity(line["qty"])) or "&nbsp;", styles["cell_center"]),
            Paragraph(_esc(line["unit"]) or "&nbsp;", styles["cell_center"]),
            Paragraph(_esc(price_text), styles["cell_right"]),
            Paragraph(_esc(_money_with_currency(line["line_total"], currency)), styles["cell_right"]),
            Paragraph(_esc(line["notes"]) or "&nbsp;", styles["cell"]),
        ])
    if len(data) == 1:
        data.append([Paragraph("&nbsp;", styles["cell"]) for _ in range(8)])

    show_breakdown = Decimal(str(tax or 0)) != 0 or Decimal(str(discount or 0)) != 0
    total_rows = []
    if show_breakdown:
        total_rows.append((labels["subtotal"], subtotal))
        if Decimal(str(tax or 0)) != 0:
            total_rows.append((labels["tax"], tax))
        if Decimal(str(discount or 0)) != 0:
            total_rows.append((labels["discount"], discount))
    total_rows.append((labels["grand"], final_amount))
    first_total = len(data)
    for label, amount in total_rows:
        data.append([
            Paragraph(_esc(label), styles["total_label"]),
            "", "", "", "", "",
            Paragraph(_esc(_money_with_currency(amount, currency)), styles["total_value"]),
            "",
        ])

    col_widths = [12 * mm, 32 * mm, 36 * mm, 14 * mm, 16 * mm, 24 * mm, 28 * mm, 26 * mm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DARK_GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("BACKGROUND", (0, 1), (-1, first_total - 1), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND", (0, first_total), (-1, -1), DARK_GREEN),
        ("SPAN", (0, first_total), (5, first_total)),
    ])
    for offset in range(1, len(total_rows)):
        style.add("SPAN", (0, first_total + offset), (5, first_total + offset))
    if len(lines) > 1:
        for row_index in range(1, first_total):
            if row_index % 2 == 0:
                style.add("BACKGROUND", (0, row_index), (-1, row_index), VALUE_BG)
    table.setStyle(style)

    party_header = Table(
        [[
            Paragraph(_esc(f"{labels['party_a']}：{party_a}" if language == "zh" else f"{labels['party_a']}: {party_a}"), styles["party"]),
            Paragraph(_esc(f"{labels['total']}：{_money_with_currency(final_amount, currency)}" if language == "zh" else f"{labels['total']}: {_money_with_currency(final_amount, currency)}"), styles["party"]),
            Paragraph(_esc(f"{labels['party_b']}：{party_b}" if language == "zh" else f"{labels['party_b']}: {party_b}"), styles["party"]),
        ]],
        colWidths=[62 * mm, 64 * mm, 62 * mm],
    )
    party_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), MID_GREEN),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    terms = str(quote.get("project_terms") or "").strip()
    if prasinos:
        cover_styles = _hourly_styles()
        cover_styles["value"] = ParagraphStyle(
            "PrasinosCoverValue",
            parent=cover_styles["value"],
            fontName=font_name,
        )
        story = quotation_cover_story(quote, cover_styles)
        story.append(table)
        if terms:
            story.append(Paragraph(_esc(terms), styles["disclaimer"]))
        story.append(Spacer(1, 8))
        story.append(_hourly_signature_table(quote, _hourly_styles()))
    else:
        story = [
            party_header,
            Spacer(1, 8),
            Paragraph(_esc(title), styles["title"]),
            Paragraph(_esc(
                " ".join(part for part in (str(quote.get("quotation_number") or ""), str(quote.get("quotation_date") or "")) if part)
            ), styles["meta"]),
            Spacer(1, 6),
            table,
        ]
        if terms:
            story.append(Paragraph(_esc(terms), styles["disclaimer"]))
        story.append(Spacer(1, 10))
        story.append(_signature_table(
            party_a, party_b, styles, labels, prasinos, quote.get("quotation_date") or "",
        ))
    if clauses_enabled(quote.get("include_standard_clauses")):
        story.append(PageBreak())
        story.extend(_terms_block({
            "subtitle": ParagraphStyle("QSubtitle", fontName="Helvetica", fontSize=10, leading=13, alignment=TA_CENTER),
            "clause_title": ParagraphStyle("QClauseTitle", fontName="Helvetica", fontSize=9, leading=12, textColor=DARK_GREEN),
            "clause": ParagraphStyle("QClause", fontName="Helvetica", fontSize=8, leading=11, alignment=TA_LEFT),
            "section": ParagraphStyle("QSection", fontName="Helvetica", fontSize=14, leading=17, textColor=DARK_GREEN),
        }))

    document = SimpleDocTemplate(
        path,
        pagesize=(PAGE_W, PAGE_H),
        leftMargin=DOC_MARGIN,
        rightMargin=DOC_MARGIN,
        topMargin=28 * mm,
        bottomMargin=14 * mm,
        title=title,
        author=party_b,
    )
    draw = _page_decorator(font_name, party_b, prasinos, str(quote.get("quotation_number") or ""), language)
    document.build(story, onFirstPage=draw, onLaterPages=draw)
    return path
