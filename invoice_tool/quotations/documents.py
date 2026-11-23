"""Quotation document definitions and PDF generation.

Layout follows Prasinos_Power_Quote_Template_v2.docx:

  QUOTATION | PROFESSIONAL SERVICES
  header info grid (number / date / validity / prepared by / customer ...)
  SCOPE OF WORK
  PRICING SUMMARY (8 fixed rows + subtotal / tax / total)
  pricing basis paragraph
  RATE SCHEDULE & PROJECT ASSUMPTIONS (9 fixed rows)
  TRAVEL & REIMBURSABLE EXPENSES (8 fixed rows)
  PROJECT ASSUMPTIONS
  PAYMENT & INVOICING
  CUSTOMER ACCEPTANCE + signature table
  STANDARD QUOTATION TERMS AND CONDITIONS (30 clauses + acknowledgement)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from invoice_tool.commercial_billing import (
    COMMERCIAL_RATE_FORM_LINES,
    COMMERCIAL_RATE_DISPLAY_UNITS,
    COMMERCIAL_RATE_TYPES,
    canonical_commercial_rate_key,
)
from invoice_tool.quotations.pdf_i18n import (
    LABELS,
    PROVIDES_ZH,
    RATE_LABEL_ZH,
    RATE_UNIT_ZH,
    TERMS_ZH,
    TRAVEL_LABEL_ZH,
    TRAVEL_METHOD_ZH,
    L,
    pdf_language,
    reset_pdf_language,
    translate_known,
    use_pdf_language,
)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
SIGNATURE_IMAGE = os.path.join(_STATIC_DIR, "signature-yongming.png")

# ---------------------------------------------------------------------------
# Fixed template rows (labels and billing units come from the Word template).
# ---------------------------------------------------------------------------

PRICING_LINES = [
    (key, label, COMMERCIAL_RATE_DISPLAY_UNITS[unit])
    for key, label, unit in COMMERCIAL_RATE_TYPES
] + [("other", "Other", "")]


RATE_SCHEDULE_LINES = COMMERCIAL_RATE_FORM_LINES

TRAVEL_EXPENSES = [
    ("airfare", "Airfare", "actual_cost"),
    ("rental_vehicle", "Rental Vehicle", "actual_cost"),
    ("rental_vehicle_fuel", "Rental Vehicle Fuel", "actual_cost"),
    ("hotel_lodging", "Hotel / Lodging", "actual_or_agreed_rate"),
    ("tolls_parking", "Tolls & Parking", "actual_cost"),
    ("rideshare_public_transport", "Rideshare / Taxi / Public Transportation", "actual_cost"),
    ("baggage_shipping_freight", "Baggage / Shipping / Freight", "actual_cost"),
    ("project_materials", "Project Materials", "actual_or_quoted_price"),
]
TRAVEL_EXPENSE_METHODS = {
    "not_applicable": "Not Applicable",
    "actual_cost": "Actual Cost",
    "actual_or_agreed_rate": "Actual Cost or Agreed Rate",
    "actual_or_quoted_price": "Actual Cost or Quoted Price",
    "fixed_amount": "Fixed Amount",
}


MINIMUM_BILLING_INCREMENT = "Minimum billing increment: 15 minutes."

PRICING_BASIS = (
    "Pricing Basis: Unless expressly identified as Fixed Price, amounts shown "
    "are estimates. Final invoicing will be based on actual work performed, "
    "actual quantities, applicable rates, and reimbursable expenses."
)

CUSTOMER_ACCEPTANCE_TEXT = (
    "By signing below, issuing a purchase order referencing this Quotation, "
    "approving it by email or other written electronic communication, "
    "instructing Prasinos Power to mobilize or proceed, permitting work to "
    "begin, or making payment related to the Work, Customer accepts this "
    "Quotation and the attached Standard Quotation Terms and Conditions."
)

TERMS_AND_CONDITIONS = [
    ("1. Scope of Work", [
        "Contractor shall provide the labor, field services, technical services, repair, retrofit, installation, commissioning, inspection, troubleshooting, materials, equipment, travel, or other services specifically described in the Quotation (the \u201cWork\u201d).",
        "Only the Work expressly identified in the Quotation is included in the quoted price.",
        "Any additional work, additional equipment, additional site visits, changes in quantity, changes in scope, rework caused by conditions outside Contractor\u2019s control, or Customer-requested modifications shall be considered additional work and may result in additional charges.",
    ]),
    ("2. Acceptance and Formation of Agreement", [
        "The Quotation, together with these Terms, constitutes Contractor\u2019s offer to perform the Work described in the Quotation.",
        "Customer shall be deemed to have accepted the Quotation and these Terms upon the earliest occurrence of any of the following: (1) Customer signs the Quotation; (2) Customer issues a purchase order referencing the Quotation; (3) Customer approves the Quotation by email, text message, electronic communication, or other written communication; (4) Customer instructs Contractor to schedule, mobilize, dispatch personnel, purchase materials, or proceed with the Work; (5) Customer permits Contractor to begin performing the Work; or (6) Customer makes any payment relating to the Work.",
        "Upon acceptance, the Quotation and these Terms shall constitute a binding agreement between Customer and Contractor.",
        "Unless Contractor expressly agrees otherwise in writing, any conflicting or additional terms contained in Customer\u2019s purchase order or other Customer documentation shall not modify these Terms.",
    ]),
    ("3. Quotation Validity", [
        "Unless otherwise stated in the Quotation, pricing is valid for thirty (30) calendar days from the date of issuance.",
        "After expiration, Contractor may revise pricing, labor rates, travel costs, material costs, or scheduling availability.",
    ]),
    ("4. Labor Charges", [
        "Labor shall be billed according to the rates stated in the Quotation.",
        "Depending on the applicable Quotation, billable labor may include Regular Labor, Overtime Labor, Holiday Labor, Travel Time, Public Transportation Time, Waiting or Standby Time, Technical Support, Commissioning or Troubleshooting Time, and other specifically stated labor categories.",
        "Unless otherwise stated, billable time shall be calculated in fifteen (15) minute increments.",
    ]),
    ("5. Travel Time", [
        "Travel time required for Contractor personnel to travel to, from, or between project locations may be billable at the Travel Time rate stated in the Quotation.",
        "Travel time may include travel from Contractor\u2019s designated departure location, temporary lodging, airport, rental vehicle location, previous project site, or other reasonable project-related location.",
        "Customer-requested schedule changes that require additional travel may result in additional Travel Time charges.",
    ]),
    ("6. Mileage and Transportation", [
        "When Contractor personnel use a personal or company vehicle for the project, mileage may be billed at the mileage rate stated in the Quotation.",
        "Mileage may include travel to and from the project site, between project sites, between the project site and lodging, for necessary project materials or supplies, and for other reasonable project-related transportation.",
        "Airfare, rental vehicles, tolls, parking, rideshare, public transportation, baggage charges, and other project-related transportation costs may be billed separately unless expressly included in the Quotation.",
    ]),
    ("7. Waiting Time and Site Delays", [
        "Contractor personnel\u2019s time shall be billable when personnel are ready, willing, and available to perform the Work but cannot proceed due to circumstances outside Contractor\u2019s reasonable control.",
        "Billable Waiting Time may include waiting for site access, Customer representatives, escorts, permits or authorization; safety orientation or site-required onboarding; equipment shutdown or energization delays; lockout/tagout delays; waiting for equipment, parts, materials, tools, or documentation; Customer or third-party coordination delays; weather-related site holds when Contractor personnel are required to remain available; security restrictions; network, software, communication, or system access delays; work stoppages requested by Customer, owner, general contractor, EPC, OEM, or another party; and any other delay not caused by Contractor.",
        "Waiting Time shall be billed at the rate specified in the Quotation. Contractor may document Waiting Time using time records, photographs, GPS records, site access records, emails, text messages, work reports, or other reasonable evidence.",
    ]),
    ("8. Mobilization and Demobilization", [
        "Unless expressly included in a fixed project price, reasonable mobilization and demobilization costs associated with the project may be billed to Customer.",
        "If Contractor mobilizes personnel based on Customer authorization and the project is subsequently delayed, canceled, postponed, or rescheduled, Customer remains responsible for costs already incurred.",
    ]),
    ("9. Lodging", [
        "When overnight lodging is reasonably required for performance of the Work, lodging may be billed as specified in the Quotation.",
        "Unless otherwise expressly agreed, Customer shall be responsible for reasonable project-related lodging expenses for Contractor personnel. Applicable taxes, parking, mandatory hotel fees, and similar charges may also be billed.",
    ]),
    ("10. Meals and Per Diem", [
        "Meals or per diem shall be billed only when stated in the Quotation, approved by Customer, or otherwise required by the applicable project agreement.",
    ]),
    ("11. Materials, Parts, Consumables and Equipment", [
        "Parts, materials, consumables, tools, rental equipment, shipping, freight, expedited delivery charges, and other project-specific purchases are not included unless expressly stated in the Quotation.",
        "Additional materials required after work begins may be billed separately. Contractor is not responsible for delays resulting from manufacturer, supplier, carrier, or Customer-provided material shortages outside Contractor\u2019s reasonable control.",
    ]),
    ("12. Customer-Supplied Materials and Equipment", [
        "Contractor is not responsible for defects, failures, incompatibility, incorrect specifications, shortages, or delays involving equipment, parts, materials, drawings, software, information, or instructions supplied by Customer or third parties.",
        "Additional labor required as a result of such issues shall be billable.",
    ]),
    ("13. Change Orders and Additional Work", [
        "Customer may request changes to the Work. Additional Work may be authorized by written change order, revised purchase order, email, text message, electronic message, written field authorization, or other written direction from an authorized Customer representative.",
        "Where immediate action is reasonably necessary to avoid project delay, equipment damage, safety risk, or additional cost, Contractor may proceed based on Customer\u2019s written direction and bill the additional Work at the applicable rates.",
    ]),
    ("14. Daily Reports and Work Records", [
        "Contractor may maintain daily service reports, technician reports, photographs, time records, travel records, equipment records, test results, and other project documentation.",
        "Customer should notify Contractor of any good-faith objection to a service report or time record within three (3) business days after receipt. Failure to raise a timely objection may be considered evidence that Customer received the report without identified objection, but shall not override any non-waivable legal rights.",
    ]),
    ("15. Site Access and Customer Responsibilities", [
        "Customer shall provide Contractor with timely and safe access to the project site and all areas reasonably required to perform the Work.",
        "Customer is responsible for coordinating, as applicable, site access, escorts, required badges, site-specific training, work permits, equipment availability, shutdown schedules, lockout/tagout coordination, required drawings and technical documentation, Customer-provided materials, other contractors or subcontractors, and required approvals from the owner, EPC, OEM, utility, or other project stakeholders.",
        "Delays caused by failure to provide these items may be billable as Waiting Time.",
    ]),
    ("16. Safety", [
        "Contractor shall comply with applicable safety requirements and reasonable site safety policies communicated to Contractor.",
        "Contractor reserves the right to stop Work when Contractor reasonably determines that site conditions present an immediate safety concern. Reasonable time associated with Customer-required safety procedures, orientations, permits, escorts, and access requirements may be billable.",
    ]),
    ("17. Schedule", [
        "Any estimated start date, completion date, or project duration is based on information available at the time of quotation.",
        "Project schedules may be affected by site access, weather, equipment availability, Customer changes, parts availability, third-party activities, safety requirements, travel disruptions, force majeure events, or other circumstances outside Contractor\u2019s reasonable control.",
        "Unless expressly stated otherwise, project schedules are estimates and not guaranteed completion dates.",
    ]),
    ("18. Cancellation and Rescheduling", [
        "If Customer cancels, delays, postpones, or reschedules Work after Contractor has committed personnel, purchased materials, booked travel, reserved equipment, or begun mobilization, Customer shall be responsible for reasonable costs already incurred.",
        "If personnel have already traveled or arrived at the project location, applicable Travel Time, mileage, airfare, lodging, Waiting Time, labor, and other incurred costs may remain billable.",
    ]),
    ("19. Invoicing", [
        "Contractor may invoice upon project completion, at project milestones, weekly, biweekly, monthly, for reimbursable expenses as incurred, or according to another schedule stated in the Quotation.",
        "Invoices may include supporting documentation reasonably available to Contractor, including service reports, time records, receipts, photographs, or other project documentation.",
    ]),
    ("20. Payment Terms", [
        "Unless otherwise stated in the Quotation, payment is due within thirty (30) calendar days from the invoice date (\u201cNet 30\u201d).",
        "Customer shall notify Contractor promptly of any specific good-faith invoice dispute and shall identify the disputed amount and reason for the dispute. Customer shall timely pay all undisputed amounts.",
    ]),
    ("21. Late Payments", [
        "To the extent permitted by applicable law, overdue amounts may accrue interest at the lesser of: (1) one and one-half percent (1.5%) per month; or (2) the maximum lawful rate.",
        "Customer shall also be responsible, to the extent permitted by law and contract, for reasonable costs incurred by Contractor in collecting unpaid amounts.",
    ]),
    ("22. Taxes", [
        "Quoted prices exclude sales, use, excise, or similar taxes unless expressly stated otherwise. Customer shall be responsible for applicable taxes, except taxes imposed on Contractor\u2019s net income.",
        "If Customer claims a tax exemption, Customer must provide valid exemption documentation.",
    ]),
    ("23. Warranty", [
        "Contractor warrants that services will be performed in a professional and workmanlike manner consistent with generally accepted industry practices.",
        "Unless otherwise stated in the Quotation, Contractor does not provide any separate warranty for equipment, parts, or materials manufactured by third parties. Manufacturer warranties, if any, shall apply according to their terms.",
        "Contractor is not responsible for failures caused by Customer misuse, unauthorized modification, improper operation, pre-existing defects, defective Customer-supplied equipment, manufacturer defects, Work performed by others, or conditions outside Contractor\u2019s reasonable control.",
    ]),
    ("24. Limitation of Liability", [
        "TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, NEITHER PARTY SHALL BE LIABLE TO THE OTHER FOR INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, PUNITIVE, OR CONSEQUENTIAL DAMAGES, INCLUDING LOST PROFITS, LOST REVENUE, LOSS OF PRODUCTION, LOSS OF USE, OR BUSINESS INTERRUPTION, ARISING FROM THE WORK, EXCEPT TO THE EXTENT SUCH LIMITATION IS PROHIBITED BY LAW OR EXPRESSLY MODIFIED BY A SEPARATE WRITTEN AGREEMENT.",
        "TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, CONTRACTOR\u2019S AGGREGATE LIABILITY ARISING OUT OF THE WORK SHALL NOT EXCEED THE AMOUNT ACTUALLY PAID OR PAYABLE TO CONTRACTOR FOR THE SPECIFIC WORK GIVING RISE TO THE CLAIM, EXCEPT WHERE SUCH LIMITATION IS PROHIBITED BY APPLICABLE LAW.",
    ]),
    ("25. Force Majeure", [
        "Neither party shall be responsible for delay or failure to perform caused by circumstances beyond its reasonable control, including severe weather, natural disasters, fire, flood, epidemic, pandemic, acts of government, utility interruption, labor disruption, transportation disruption, supply-chain interruption, war, civil unrest, or other similar events.",
        "The affected party shall make commercially reasonable efforts to resume performance.",
    ]),
    ("26. Independent Contractor", [
        "Contractor is an independent contractor and not an employee, agent, partner, or joint venturer of Customer. Contractor retains responsibility for supervision and direction of its personnel, subject to applicable site safety rules and Customer coordination requirements.",
    ]),
    ("27. Governing Law", [
        "Unless otherwise expressly agreed in writing, this Agreement shall be governed by the laws of the State of Texas, without regard to conflict-of-law principles.",
    ]),
    ("28. Venue", [
        "Unless otherwise expressly agreed in writing, any legal proceeding arising out of or relating to the Quotation or Work shall be brought in a court of competent jurisdiction in the State of Texas. The parties may specify a particular Texas county in the Quotation or a separate written agreement.",
    ]),
    ("29. Entire Agreement", [
        "The accepted Quotation, these Terms, approved Change Orders, and any documents expressly incorporated by reference constitute the agreement between the parties concerning the Work.",
        "They supersede prior discussions or communications concerning the same Work to the extent those communications conflict with the final accepted agreement. Any amendment must be agreed to in writing by authorized representatives of the parties.",
    ]),
    ("30. Electronic Communications and Signatures", [
        "The parties agree that electronic records, electronic approvals, electronic signatures, emailed approvals, and other mutually accepted electronic communications may be used in connection with the Quotation and the Work.",
        "Electronic copies of an accepted Quotation may be treated as originals to the extent permitted by applicable law.",
    ]),
]

CUSTOMER_ACKNOWLEDGEMENT = (
    "Customer acknowledges that it has reviewed and accepts the Quotation and "
    "these Standard Quotation Terms and Conditions."
)

PAYMENT_TERMS_LABELS = {
    "net_15": "Net 15",
    "net_30": "Net 30",
    "net_45": "Net 45",
    "other": "Other",
}

INVOICE_FREQUENCY_LABELS = {
    "weekly": "Weekly",
    "biweekly": "Biweekly",
    "monthly": "Monthly",
    "upon_completion": "Upon Completion",
}

PRICING_TYPE_LABELS = {
    "fixed": "Fixed Price",
    "estimated": "Estimated / Time & Expense",
}

QUOTATION_STATUS_LABELS = {
    "draft": "草稿",
    "sent": "已发送",
    "accepted": "已接受",
    "expired": "已过期",
    "cancelled": "已作废",
}

CUSTOMER_PROVIDES_OPTIONS = [
    ("access", "Access"),
    ("escort", "Escort"),
    ("loto", "LOTO"),
]


def _money(value, currency="USD"):
    """Format a numeric value as a plain amount string (e.g. 1,234.56)."""
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    sign = "-" if amount < 0 else ""
    text = f"{sign}{abs(amount):,.2f}"
    return text if text != "0.00" else "0.00"


def _qty_text(line, quote):
    qty = str(line.get("qty") or "").strip()
    unit = str(line.get("unit") or "").strip()
    if not qty:
        return ""
    if unit in {"As required", ""}:
        return qty
    return f"{qty} {unit}"


def _rate_text(line, quote):
    rate = str(line.get("rate") or "").strip()
    unit = str(line.get("unit") or "").strip()
    if not rate:
        return ""
    symbol = quote.get("currency") or "USD"
    currency_symbol = "$" if symbol in {"USD", "CAD", "AUD", "NZD", "HKD", "SGD"} else symbol + " "
    if unit in {"As required", ""}:
        return f"{currency_symbol}{_money(rate)}"
    return f"{currency_symbol}{_money(rate)} / {unit}".replace(" / As required", "")


def _amount_text(line, quote):
    amount = line.get("amount")
    if amount in (None, ""):
        return ""
    return f"$ {_money(amount)}"


# ---------------------------------------------------------------------------
# Brand palette (from Prasinos_Power_Quote_Template_v2.docx)
# ---------------------------------------------------------------------------

DARK_GREEN = colors.HexColor("#163E36")   # heading1 / pricing header / total band
MID_GREEN = colors.HexColor("#1E6B52")    # label band / heading2 / travel header
VALUE_BG = colors.HexColor("#F4F7F5")     # info-grid value fill
VALUE_BG2 = colors.HexColor("#F7F9F8")    # assumptions / payment value fill
ZEBRA_BG = colors.HexColor("#F8FAF9")     # pricing zebra rows
BORDER = colors.HexColor("#D9E2DE")       # hairline grid
TITLE_GRAY = colors.HexColor("#66736D")   # "| PROFESSIONAL SERVICES"
HEADER_GRAY = colors.HexColor("#5E6B66")  # header tagline
TEXT_DARK = colors.HexColor("#1F2937")
WHITE = colors.white

DOC_MARGIN = 11 * mm
PAGE_W, PAGE_H = A4
PAGE_W_MM = 188.0  # usable width in mm (210 - 2*11)


def _esc(text):
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _styles():
    base = dict(fontName="Helvetica")
    return {
        "title": ParagraphStyle("QTitle", **base, fontSize=24, leading=28, alignment=TA_CENTER, textColor=DARK_GREEN),
        "subtitle": ParagraphStyle("QSubtitle", **base, fontSize=10, leading=13, alignment=TA_CENTER, textColor=TITLE_GRAY),
        "section": ParagraphStyle("QSection", **base, fontSize=14, leading=17, textColor=DARK_GREEN, spaceBefore=12, spaceAfter=5),
        "section2": ParagraphStyle("QSection2", **base, fontSize=11, leading=14, textColor=MID_GREEN, spaceBefore=10, spaceAfter=5),
        "label": ParagraphStyle("QLabel", **base, fontSize=8.5, leading=11, textColor=WHITE),
        "value": ParagraphStyle("QValue", **base, fontSize=8.5, leading=11, textColor=TEXT_DARK),
        "cell": ParagraphStyle("QCell", **base, fontSize=8, leading=10.5),
        "cell_center": ParagraphStyle("QCellCenter", **base, fontSize=8, leading=10.5, alignment=TA_CENTER),
        "cell_right": ParagraphStyle("QCellRight", **base, fontSize=8, leading=10.5, alignment=TA_RIGHT),
        "head": ParagraphStyle("QHead", **base, fontSize=8, leading=10.5, textColor=WHITE, alignment=TA_CENTER),
        "body": ParagraphStyle("QBody", **base, fontSize=8, leading=11, spaceAfter=2),
        "clause": ParagraphStyle("QClause", **base, fontSize=8, leading=11, alignment=TA_JUSTIFY, spaceAfter=3),
        "clause_title": ParagraphStyle("QClauseTitle", **base, fontSize=9, leading=12, textColor=DARK_GREEN, spaceBefore=5, spaceAfter=1),
        "small": ParagraphStyle("QSmall", **base, fontSize=7.5, leading=10, textColor=colors.HexColor("#5E6B66")),
        "total": ParagraphStyle("QTotal", **base, fontSize=9, leading=12, textColor=WHITE),
        "total_value": ParagraphStyle("QTotalValue", **base, fontSize=11, leading=14, textColor=WHITE, alignment=TA_RIGHT),
    }


def _band_style(fill):
    """Table style for a labelled band: header row solid fill, thin border."""
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (-1, 0), fill)
    return style


def _table_style(borders=True):
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if borders:
        cmds += [
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ]
    return TableStyle(cmds)


def _label_value_row(label_a, value_a, label_b, value_b, styles, label_fill=MID_GREEN, value_fill=VALUE_BG):
    """One 4-column label/value row; labels solid green, values light green."""
    return [
        Paragraph(_esc(label_a), styles["label"]),
        Paragraph(_esc(value_a) or "&nbsp;", styles["value"]),
        Paragraph(_esc(label_b), styles["label"]),
        Paragraph(_esc(value_b) or "&nbsp;", styles["value"]),
    ]


def _info_grid(quote, styles):
    """10 template fields laid out as 4 columns (label/value/label/value)."""
    rows = [
        (L("quotation_no"), quote.get("quotation_number") or "", L("quotation_date"), quote.get("quotation_date") or ""),
        (L("valid_until"), quote.get("valid_until") or "", L("prepared_by"), quote.get("prepared_by") or ""),
        (L("customer"), quote.get("customer") or "", L("contact"), quote.get("contact") or ""),
        (L("project_name"), quote.get("project_name") or "", L("project_no"), quote.get("project_no") or ""),
        (L("project_location"), quote.get("project_location") or "", L("po_no"), quote.get("po_no") or ""),
    ]
    data = [_label_value_row(a, b, c, d, styles) for a, b, c, d in rows]
    table = Table(data, colWidths=[27 * mm, 63 * mm, 27 * mm, 69 * mm], repeatRows=0)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (0, -1), MID_GREEN)
    style.add("BACKGROUND", (2, 0), (2, -1), MID_GREEN)
    style.add("BACKGROUND", (1, 0), (1, -1), VALUE_BG)
    style.add("BACKGROUND", (3, 0), (3, -1), VALUE_BG)
    table.setStyle(style)
    return table


def _section(title, styles, level=1):
    if level == 2:
        return Paragraph(_esc(title), styles["section2"])
    return Paragraph(_esc(title), styles["section"])


def _pdf_styles():
    styles = _styles()
    if pdf_language() != "zh":
        return styles
    from invoice_tool.quotations.project_layout import cjk_font_name

    font = cjk_font_name()
    return {
        key: ParagraphStyle(f"Zh{style.name}", parent=style, fontName=font)
        for key, style in styles.items()
    }


def _localized_description(quote):
    from invoice_tool.quotations.pdf_i18n import localized_text

    return localized_text(
        pdf_language(),
        quote.get("project_description"),
        quote.get("project_description_zh"),
        quote.get("project_description_en"),
    )


def quotation_cover_story(quote, styles):
    """Letterhead title, info grid, scope and the pricing-summary heading.

    The hourly Time & Expense PDF and a Prasinos-issued project quote share
    this block. Wording follows the active PDF language.
    """
    story = [
        Paragraph(
            f"{_esc(L('doc_title'))}<br/><font color='#66736D' size='9'>{_esc(L('doc_subtitle'))}</font>",
            styles["title"],
        ),
        Spacer(1, 7),
        _info_grid(quote, styles),
        _section(L("scope"), styles),
    ]
    description = _localized_description(quote)
    scope_rows = [("Project Description", description)]
    scope_rows = [pair for pair in scope_rows if str(pair[1]).strip()]
    if not scope_rows:
        scope_rows = [("Project Description", "")]
    scope_data = [
        [Paragraph(_esc(L("project_description") if label == "Project Description" else label), styles["label"]),
         Paragraph(_esc(value) or "&nbsp;", styles["value"])]
        for label, value in scope_rows
    ]
    scope_table = Table(scope_data, colWidths=[35 * mm, 153 * mm], repeatRows=0)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (0, -1), MID_GREEN)
    style.add("BACKGROUND", (1, 0), (1, -1), VALUE_BG)
    scope_table.setStyle(style)
    story.append(scope_table)
    story.append(_section(L("pricing_summary"), styles))
    return story


def _pricing_table(quote, lines, styles):
    header = [
        Paragraph(_esc(L("description")), styles["head"]),
        Paragraph(_esc(L("qty_unit")), styles["head"]),
        Paragraph(_esc(L("rate")), styles["head"]),
        Paragraph(_esc(L("estimated_amount")), styles["head"]),
    ]
    data = [header]
    for idx, line in enumerate(lines):
        label = RATE_LABEL_ZH.get(line.get("key"), line.get("label") or "") if pdf_language() == "zh" else (line.get("label") or "")
        data.append([
            Paragraph(_esc(label), styles["cell"]),
            Paragraph(_esc(_qty_text(line, quote)), styles["cell_center"]),
            Paragraph(_esc(_rate_text(line, quote)), styles["cell_center"]),
            Paragraph(_esc(_amount_text(line, quote)), styles["cell_right"]),
        ])
    table = Table(data, colWidths=[47 * mm, 47 * mm, 47 * mm, 47 * mm], repeatRows=1)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (-1, 0), DARK_GREEN)
    # zebra striping: odd data rows (index 1,3,5,7) get light fill
    for idx in range(1, len(data)):
        if idx % 2 == 1:
            style.add("BACKGROUND", (0, idx), (-1, idx), ZEBRA_BG)
    table.setStyle(style)
    return table


def _fixed_price_table(quote, total, styles):
    """Fixed-price quotations render a single total row instead of line items."""
    header = [
        Paragraph(_esc(L("description")), styles["head"]),
        Paragraph(_esc(L("qty_unit")), styles["head"]),
        Paragraph(_esc(L("rate")), styles["head"]),
        Paragraph(_esc(L("amount")), styles["head"]),
    ]
    data = [header, [
        Paragraph(_esc(L("fixed_price")), styles["cell"]),
        Paragraph("—", styles["cell_center"]),
        Paragraph("—", styles["cell_center"]),
        Paragraph(_esc(f"$ {_money(total)}"), styles["cell_right"]),
    ]]
    table = Table(data, colWidths=[47 * mm, 47 * mm, 47 * mm, 47 * mm], repeatRows=1)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (-1, 0), DARK_GREEN)
    style.add("BACKGROUND", (0, 1), (-1, 1), ZEBRA_BG)
    table.setStyle(style)
    return table


def _totals_table(quote, subtotal, tax, total, styles):
    def row(label, value, text_style, value_style):
        return [
            Paragraph(_esc(label), text_style),
            Paragraph(_esc(f"$ {_money(value)}"), value_style),
        ]

    discount_amount = quote.get("discount_amount") or 0
    final_amount = quote.get("final_amount")
    final_value = final_amount if final_amount is not None else total - discount_amount
    discount_row = row(L("discount"), discount_amount, styles["cell"], styles["cell_right"])
    final_row = row(L("final_amount"), final_value, styles["total"], styles["total_value"])
    data = [
        row(L("estimated_subtotal"), subtotal, styles["cell"], styles["cell_right"]),
        row(L("tax"), tax, styles["cell"], styles["cell_right"]),
        row(L("estimated_total"), total, styles["cell"], styles["cell_right"]),
        discount_row,
        final_row,
    ]
    table = Table(data, colWidths=[118 * mm, 68 * mm], repeatRows=0)
    style = _table_style(borders=False)
    style.add("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER)
    style.add("BACKGROUND", (0, 4), (-1, 4), DARK_GREEN)
    table.setStyle(style)
    return table


def _rate_table(quote, lines, styles):
    header = [
        Paragraph(_esc(L("billing_category")), styles["head"]),
        Paragraph(_esc(L("rate")), styles["head"]),
        Paragraph(_esc(L("billing_unit")), styles["head"]),
    ]
    rate_style = ParagraphStyle("QRateCell", parent=styles["cell"], fontSize=8.5, leading=11)
    rate_center = ParagraphStyle("QRateCenter", parent=styles["cell_center"], fontSize=8.5, leading=11)
    data = [header]
    for line in lines:
        label = RATE_LABEL_ZH.get(line.get("key"), line.get("label") or "") if pdf_language() == "zh" else (line.get("label") or "")
        unit = RATE_UNIT_ZH.get(line.get("unit") or "", line.get("unit") or "") if pdf_language() == "zh" else (line.get("unit") or "")
        data.append([
            Paragraph(_esc(label), rate_style),
            Paragraph(_esc(f"$ {_money(line.get('rate') or 0)}"), rate_center),
            Paragraph(_esc(unit), rate_center),
        ])
    table = Table(data, colWidths=[63 * mm, 62.5 * mm, 62.5 * mm], repeatRows=1)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (-1, 0), DARK_GREEN)
    table.setStyle(style)
    return table


def _travel_table(quote, styles):
    header = [
        Paragraph(_esc(L("expense")), styles["head"]),
        Paragraph(_esc(L("billing_method")), styles["head"]),
    ]
    data = [header]
    try:
        import json
        raw = quote.get("travel_expenses") or "[]"
        configured = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or [])
        by_key = {str(item.get("key")): item for item in configured if isinstance(item, dict)}
    except (ValueError, TypeError):
        by_key = {}
    for key, label, default_method in TRAVEL_EXPENSES:
        item = by_key.get(key) or {}
        method = item.get("method") or default_method
        if method == "not_applicable":
            continue
        if pdf_language() == "zh":
            label = TRAVEL_LABEL_ZH.get(key, label)
            method_text = TRAVEL_METHOD_ZH.get(method, method)
        else:
            method_text = TRAVEL_EXPENSE_METHODS.get(method, method)
        if method == "fixed_amount":
            method_text = f"{L('fixed_amount_prefix')} - $ {_money(item.get('amount') or 0)}"
        data.append([Paragraph(_esc(label), styles["cell"]), Paragraph(_esc(method_text), styles["cell"])])
    if len(data) == 1:
        data.append([Paragraph(_esc(L("no_travel")), styles["cell"]), Paragraph(_esc(L("not_applicable")), styles["cell"])])
    table = Table(data, colWidths=[93 * mm, 93 * mm], repeatRows=1)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (-1, 0), MID_GREEN)
    table.setStyle(style)
    return table


def _assumptions_grid(quote, styles):
    provides = quote.get("customer_provides") or ""
    provided = set(p.strip() for p in str(provides).split(",") if p.strip())
    provided_text = "  ".join(
        ("\u2611" if key in provided else "\u2610") + " " + (PROVIDES_ZH.get(key, label) if pdf_language() == "zh" else label)
        for key, label in CUSTOMER_PROVIDES_OPTIONS
    )
    other = translate_known(quote.get("assumptions_other") or "") if pdf_language() == "zh" else (quote.get("assumptions_other") or "")
    rows = [
        (L("crew_size"), quote.get("crew_size") or "", L("workdays"), quote.get("workdays") or ""),
        (L("hours_per_day"), quote.get("hours_per_day") or "", L("expected_start"), quote.get("expected_start_date") or ""),
        (L("expected_completion"), quote.get("expected_completion") or "", L("normal_hours"), quote.get("normal_working_hours") or ""),
        (L("customer_provides"), provided_text, L("other"), other),
    ]
    data = [_label_value_row(a, b, c, d, styles, label_fill=MID_GREEN, value_fill=VALUE_BG2)
            for a, b, c, d in rows]
    table = Table(data, colWidths=[47 * mm, 47 * mm, 47 * mm, 47 * mm], repeatRows=0)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (0, -1), MID_GREEN)
    style.add("BACKGROUND", (2, 0), (2, -1), MID_GREEN)
    style.add("BACKGROUND", (1, 0), (1, -1), VALUE_BG2)
    style.add("BACKGROUND", (3, 0), (3, -1), VALUE_BG2)
    table.setStyle(style)
    return table


def _payment_grid(quote, styles):
    payment_terms = quote.get("payment_terms") or "net_30"
    other_text = str(quote.get("payment_terms_other") or "").strip()
    terms_text = translate_known(PAYMENT_TERMS_LABELS.get(payment_terms, "Net 30"))
    if payment_terms == "other":
        terms_text = f"{L('other_prefix')}: {other_text}" if other_text else f"{L('other_prefix')}: ____"

    frequency = quote.get("invoice_frequency") or "upon_completion"
    pricing_type = quote.get("pricing_type") or "estimated"
    validity = translate_known(quote.get("quotation_validity") or "30 calendar days")
    tax_note = translate_known(quote.get("tax_note") or "Excluded unless stated")
    rows = [
        (L("payment_terms"), terms_text, L("validity"), validity),
        (L("invoice_frequency"), translate_known(INVOICE_FREQUENCY_LABELS.get(frequency, frequency)), L("currency"), quote.get("currency") or "USD"),
        (L("pricing_type"), translate_known(PRICING_TYPE_LABELS.get(pricing_type, pricing_type)), L("tax_note"), tax_note),
    ]
    data = [_label_value_row(a, b, c, d, styles, label_fill=DARK_GREEN, value_fill=VALUE_BG2)
            for a, b, c, d in rows]
    table = Table(data, colWidths=[47 * mm, 47 * mm, 47 * mm, 47 * mm], repeatRows=0)
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (0, -1), DARK_GREEN)
    style.add("BACKGROUND", (2, 0), (2, -1), DARK_GREEN)
    style.add("BACKGROUND", (1, 0), (1, -1), VALUE_BG2)
    style.add("BACKGROUND", (3, 0), (3, -1), VALUE_BG2)
    table.setStyle(style)
    return table


def _signature_table(quote, styles):
    header = [Paragraph(_esc(L("customer_company")), styles["head"]), Paragraph("Prasinos Power", styles["head"])]

    sig_img = None
    if os.path.isfile(SIGNATURE_IMAGE):
        sig_img = Image(SIGNATURE_IMAGE, width=55 * mm, height=15 * mm)

    rep_value = L("rep_name")
    title_value = L("rep_title")
    date_value = quote.get("quotation_date") or ""

    left_label_style = ParagraphStyle("SigLeft", parent=styles["label"], textColor=colors.HexColor("#66736D"))
    right_value_style = ParagraphStyle("SigRight", parent=styles["body"], fontSize=10, leading=13)

    data = [header]
    # Authorized Representative
    data.append([
        Paragraph(_esc(L("representative")), left_label_style),
        Paragraph(_esc(rep_value), right_value_style),
    ])
    # Title
    data.append([
        Paragraph(_esc(L("title")), left_label_style),
        Paragraph(_esc(title_value), right_value_style),
    ])
    # Signature
    sig_cell = sig_img if sig_img else Paragraph(" ", right_value_style)
    data.append([
        Paragraph(_esc(L("signature")), left_label_style),
        sig_cell,
    ])
    # Date
    data.append([
        Paragraph(_esc(L("date")), left_label_style),
        Paragraph(_esc(date_value), right_value_style),
    ])

    table = Table(data, colWidths=[93 * mm, 93 * mm], repeatRows=0, rowHeights=[None, None, None, 20 * mm, None])
    style = _table_style()
    style.add("BACKGROUND", (0, 0), (-1, 0), MID_GREEN)
    style.add("BACKGROUND", (0, 1), (-1, -1), colors.white)
    style.add("VALIGN", (0, 0), (-1, -1), "TOP")
    style.add("VALIGN", (1, 3), (1, 3), "MIDDLE")
    style.add("TOPPADDING", (0, 1), (-1, -1), 8)
    style.add("BOTTOMPADDING", (0, 1), (-1, -1), 8)
    table.setStyle(style)
    return table


def _terms_block(styles=None):
    font = "Helvetica"
    if pdf_language() == "zh":
        from invoice_tool.quotations.project_layout import cjk_font_name
        font = cjk_font_name()
    title_style = ParagraphStyle("QTermsTitle", fontName=font, fontSize=15, leading=19, alignment=TA_CENTER, textColor=DARK_GREEN)
    sub_style = ParagraphStyle("QTermsSub", fontName=font, fontSize=10, leading=13, alignment=TA_CENTER, textColor=MID_GREEN)
    clause_title = ParagraphStyle("QClauseTitleLang", fontName=font, fontSize=9, leading=12, textColor=DARK_GREEN, spaceBefore=5, spaceAfter=1)
    clause = ParagraphStyle("QClauseLang", fontName=font, fontSize=8, leading=11, alignment=TA_LEFT, spaceAfter=3)
    section = ParagraphStyle("QSectionLang", fontName=font, fontSize=14, leading=17, textColor=DARK_GREEN, spaceBefore=12, spaceAfter=5)
    clauses = TERMS_ZH if pdf_language() == "zh" else TERMS_AND_CONDITIONS
    story = [Paragraph("PRASINOS POWER", title_style)]
    story.append(Spacer(1, 2))
    story.append(Paragraph(_esc(L("terms_title")), sub_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(_esc(L("terms_intro")), clause))
    for title, paragraphs in clauses:
        story.append(Paragraph(_esc(title), clause_title))
        for paragraph in paragraphs:
            story.append(Paragraph(_esc(paragraph), clause))
    story.append(Spacer(1, 6))
    story.append(Paragraph(_esc(L("acknowledgement_title")), section))
    story.append(Paragraph(_esc(L("acknowledgement")), clause))
    return story


def _add_quotation_page_templates(document, draw, *, include_terms):
    quotation_frame = Frame(
        document.leftMargin, document.bottomMargin, document.width, document.height,
        id="Quotation",
    )
    document.addPageTemplates(PageTemplate(id="Quotation", frames=[quotation_frame], onPage=draw))
    if not include_terms:
        return
    frame = Frame(
        document.leftMargin, document.bottomMargin, document.width,
        document.pagesize[1] - document.bottomMargin - 14 * mm,
        id="StandardTerms",
    )
    document.addPageTemplates(PageTemplate(
        id="StandardTerms",
        frames=[frame],
        onPage=lambda canvas, doc: draw(canvas, doc, show_header=False),
    ))


def _draw_prasinos_quotation_header(canvas):
    logo_size = 12 * mm
    header_center = PAGE_H - 16 * mm
    logo_path = os.path.join(_STATIC_DIR, "invoice-logo.png")
    text_left = DOC_MARGIN
    if os.path.isfile(logo_path):
        canvas.drawImage(logo_path, DOC_MARGIN, header_center - logo_size / 2,
                         width=logo_size, height=logo_size, mask="auto")
        text_left += logo_size + 3 * mm
    canvas.setFillColor(DARK_GREEN)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(text_left, header_center - 5, "PRASINOS POWER")


def _draw_header_footer(canvas, doc, *, show_header=True):
    """Draw the quotation brand header and the page footer."""
    canvas.saveState()
    if show_header:
        _draw_prasinos_quotation_header(canvas)
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(DOC_MARGIN, PAGE_H - 24.5 * mm, PAGE_W - DOC_MARGIN, PAGE_H - 24.5 * mm)
    # --- footer ---
    canvas.setFillColor(HEADER_GRAY)
    if pdf_language() == "zh":
        from invoice_tool.quotations.project_layout import cjk_font_name
        canvas.setFont(cjk_font_name(), 8)
    else:
        canvas.setFont("Helvetica", 8)
    canvas.drawString(DOC_MARGIN, 7 * mm, f"Prasinos Power  |  {L('footer_confidential')}")
    canvas.drawRightString(PAGE_W - DOC_MARGIN, 7 * mm, L("page").format(n=doc.page))
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(DOC_MARGIN, 10 * mm, PAGE_W - DOC_MARGIN, 10 * mm)
    canvas.restoreState()


def build_quotation_pdf(quote, path, language=None):
    """Render a quotation row (dict) to a PDF file at ``path``.

    ``language`` is only used by the project / installation-checklist layout.
    Hourly quotes ignore it and keep the Time & Expense template.
    """
    layout = "hourly"
    if hasattr(quote, "get"):
        layout = str(quote.get("layout") or "hourly")
    if layout == "project":
        from invoice_tool.quotations.project_layout import build_project_quotation_pdf
        return build_project_quotation_pdf(quote, path, language=language)
    token = use_pdf_language(language, default="en")
    styles = _pdf_styles()

    def parse_pricing_lines():
        lines = []
        try:
            import json

            raw = quote.get("pricing_lines") or "[]"
            if isinstance(raw, str):
                raw = json.loads(raw) if raw.strip() else []
            by_key = {str(item.get("key")): item for item in raw if isinstance(item, dict)}
        except (ValueError, TypeError):
            by_key = {}
        for key, label, unit in PRICING_LINES:
            item = by_key.get(key) or {}
            lines.append({
                "key": key,
                "label": label,
                "unit": unit,
                "qty": item.get("qty") or "",
                "rate": item.get("rate") or "",
                "amount": item.get("amount") or "",
            })
        return lines

    def parse_rate_schedule():
        lines = []
        try:
            import json

            raw = quote.get("rate_schedule") or "[]"
            if isinstance(raw, str):
                raw = json.loads(raw) if raw.strip() else []
            by_key = {
                canonical_commercial_rate_key(item.get("key")): item
                for item in raw if isinstance(item, dict)
            }
        except (ValueError, TypeError):
            by_key = {}
        for key, label, unit in RATE_SCHEDULE_LINES:
            item = by_key.get(key) or {}
            lines.append({
                "key": key,
                "label": label,
                "unit": unit,
                "rate": item.get("rate") or "",
            })
        return lines

    pricing_lines = parse_pricing_lines()
    rate_lines = parse_rate_schedule()

    subtotal = quote.get("subtotal")
    tax = quote.get("tax")
    total = quote.get("total")

    document = BaseDocTemplate(
        path,
        pagesize=A4,
        leftMargin=DOC_MARGIN,
        rightMargin=DOC_MARGIN,
        topMargin=28 * mm,
        bottomMargin=14 * mm,
        title=f"Quotation {quote.get('quotation_number') or ''}",
        author="Prasinos Power",
    )
    story = quotation_cover_story(quote, styles)
    pricing_type = quote.get("pricing_type") or "estimated"
    if pricing_type == "fixed":
        story.append(_fixed_price_table(quote, total, styles))
    else:
        story.append(_pricing_table(quote, pricing_lines, styles))
    story.append(Spacer(1, 2))
    story.append(_totals_table(quote, subtotal, tax, total, styles))
    story.append(Spacer(1, 2))
    story.append(Paragraph(_esc(L("pricing_basis")), styles["small"]))

    if pricing_type != "fixed":
        story.append(_section(L("rate_schedule"), styles))
        story.append(_rate_table(quote, rate_lines, styles))
        story.append(Spacer(1, 2))
        story.append(Paragraph(_esc(L("minimum_increment")), styles["small"]))

    story.append(KeepTogether([
        _section(L("travel"), styles, level=2),
        _travel_table(quote, styles),
    ]))

    story.append(KeepTogether([
        _section(L("assumptions"), styles, level=2),
        _assumptions_grid(quote, styles),
    ]))

    story.append(KeepTogether([
        _section(L("payment"), styles, level=2),
        _payment_grid(quote, styles),
    ]))

    story.append(_section(L("acceptance"), styles))
    story.append(Paragraph(_esc(L("acceptance_body")), styles["body"]))
    story.append(Spacer(1, 4))
    story.append(_signature_table(quote, styles))

    story.extend([NextPageTemplate("StandardTerms"), PageBreak()])
    story.extend(_terms_block(styles))
    _add_quotation_page_templates(document, _draw_header_footer, include_terms=True)

    try:
        document.build(story)
    finally:
        reset_pdf_language(token)
    return path
