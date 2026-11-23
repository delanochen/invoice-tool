"""Language packs for quotation PDFs.

English and Chinese documents keep static wording in one language. Proper
names, quote numbers and currency units are allowed to stay as written.
"""

from __future__ import annotations

from contextvars import ContextVar

_LANG = ContextVar("quotation_pdf_lang", default="en")

RATE_LABEL_ZH = {
    "regular_hours": "正常工时",
    "overtime_hours": "加班工时",
    "holiday_hours": "节假日工时",
    "travel_hours": "交通工时",
    "public_transport_hours": "公共交通工时",
    "waiting_standby_hours": "等待与待命工时",
    "technical_support_hours": "技术支持",
    "mileage": "里程",
    "lodging_cap": "住宿",
    "per_diem": "每日津贴",
    "other": "其他",
    "regular_labor": "正常工时",
    "overtime_labor": "加班工时",
}

RATE_UNIT_ZH = {
    "Per Hour": "每小时",
    "Per Mile": "每英里",
    "Person / Night": "每人每晚",
    "Person / Day": "每人每天",
    "hour": "每小时",
    "mile": "每英里",
    "person_night": "每人每晚",
    "day": "每人每天",
}

TRAVEL_LABEL_ZH = {
    "airfare": "机票",
    "rental_vehicle": "租车",
    "rental_vehicle_fuel": "租车燃油",
    "hotel_lodging": "酒店住宿",
    "tolls_parking": "过路费与停车费",
    "rideshare_public_transport": "网约车、出租车与公共交通",
    "baggage_shipping_freight": "行李、运输与运费",
    "project_materials": "项目材料",
}

TRAVEL_METHOD_ZH = {
    "not_applicable": "不适用",
    "actual_cost": "实报实销",
    "actual_or_agreed_rate": "实报实销或约定单价",
    "actual_or_quoted_price": "实报实销或报价",
    "fixed_amount": "固定金额",
}

PROVIDES_ZH = {
    "access": "进场",
    "escort": "陪同",
    "loto": "上锁挂牌",
    "permits": "许可",
}

KNOWN_VALUE_ZH = {
    "30 calendar days": "30 个日历日",
    "Net 15": "净 15 天",
    "Net 30": "净 30 天",
    "Net 45": "净 45 天",
    "Other": "其他",
    "Weekly": "每周",
    "Biweekly": "每两周",
    "Monthly": "每月",
    "Upon Completion": "完工后",
    "Fixed Price": "固定总价",
    "Estimated / Time & Expense": "预估（工时与费用）",
    "Excluded unless stated": "除非另有说明，不含税",
    "As required": "按需",
}


def normalize_pdf_language(language, default="en"):
    if language is None or str(language).strip() == "":
        return default
    return "zh" if str(language).strip().lower().startswith("zh") else "en"


def use_pdf_language(language, default="en"):
    return _LANG.set(normalize_pdf_language(language, default=default))


def reset_pdf_language(token):
    _LANG.reset(token)


def pdf_language():
    return _LANG.get()


def L(key):
    return LABELS[pdf_language()][key]


def translate_known(value):
    text = str(value or "")
    if pdf_language() != "zh":
        return text
    return KNOWN_VALUE_ZH.get(text, text)


def catalog_name(item, language, fallback=""):
    """Pick a catalog display name. Prefer the requested language, then name."""
    if not item:
        return fallback or ""
    language = normalize_pdf_language(language, default="en")
    name = str(item.get("name") or "").strip()
    zh = str(item.get("name_zh") or "").strip()
    en = str(item.get("name_en") or "").strip()
    if language == "zh":
        return zh or name or en or fallback or ""
    return en or name or zh or fallback or ""


def localized_text(language, primary, zh, en):
    """Use the requested language, then the legacy field, then the other language."""
    language = normalize_pdf_language(language, default="en")
    primary = str(primary or "").strip()
    zh = str(zh or "").strip()
    en = str(en or "").strip()
    if language == "zh":
        return zh or primary or en
    return en or primary or zh


def section_label(line, language):
    fallback = str(line.get("section_name") or "").strip()
    catalog = catalog_name(
        {
            "name": line.get("catalog_name"),
            "name_zh": line.get("catalog_name_zh"),
            "name_en": line.get("catalog_name_en"),
        },
        language,
        fallback="",
    )
    if line.get("project_id") and catalog:
        return catalog
    return localized_text(
        language,
        fallback,
        line.get("section_name_zh"),
        line.get("section_name_en"),
    ) or fallback


PROJECT_UNITS_EN = {
    "台": "unit",
    "项": "item",
    "米": "m",
    "个": "each",
    "套": "set",
    "小时": "hour",
    "按台": "per unit",
}


def project_unit(value, language):
    unit = str(value or "").strip()
    return PROJECT_UNITS_EN.get(unit, unit) if language == "en" else unit


def project_document_title(quote, language):
    title = localized_text(
        language, quote.get("document_title"), quote.get("document_title_zh"), quote.get("document_title_en"),
    )
    return LABELS[language]["doc_title"] if not title or (language == "en" and title == "报价单") else title


def project_english_pdf_gaps(quote, lines):
    """Identify untranslated content before issuing an English project quotation."""
    gaps = []

    def check(label, value):
        if _looks_chinese(value):
            gaps.append(label)

    for key, label in (
        ("project_description", "项目描述"),
        ("project_terms", "页脚说明"),
    ):
        check(label, localized_text("en", quote.get(key), quote.get(key + "_zh"), quote.get(key + "_en")))
    party_b = "".join(str(quote.get("party_b") or "").lower().split())
    if party_b and "prasinos" not in party_b:
        check("文档标题", project_document_title(quote, "en"))
    for index, line in enumerate(lines, start=1):
        check(f"第 {index} 行项目名称", section_label(line, "en"))
        check(f"第 {index} 行单位", project_unit(line.get("unit"), "en"))
        for key, label in (("scope_of_work", "工作范围"), ("notes", "备注")):
            check(
                f"第 {index} 行{label}",
                localized_text("en", line.get(key), line.get(key + "_zh"), line.get(key + "_en")),
            )
    return gaps


def missing_language_warnings(quote, lines, catalog_by_id=None):
    """Human-readable gaps where one PDF language has no dedicated text."""
    warnings = []
    catalog_by_id = catalog_by_id or {}

    def gap(label, primary, zh, en):
        primary = str(primary or "").strip()
        zh = str(zh or "").strip()
        en = str(en or "").strip()
        if not any((primary, zh, en)):
            return
        if primary and not zh and not en:
            warnings.append(f"{label}没有分开的中英文，两种 PDF 都会使用同一段原文。")
            return
        if not zh and not _looks_chinese(primary):
            warnings.append(f"{label}缺少中文，中文 PDF 会回退到另一语言。")
        if not en and not _looks_latin(primary):
            warnings.append(f"{label}缺少英文，英文 PDF 会回退到另一语言。")

    gap("项目描述", quote.get("project_description"), quote.get("project_description_zh"), quote.get("project_description_en"))
    if (quote.get("layout") or "hourly") == "project":
        gap("文档标题", quote.get("document_title"), quote.get("document_title_zh"), quote.get("document_title_en"))
        gap("页脚说明", quote.get("project_terms"), quote.get("project_terms_zh"), quote.get("project_terms_en"))
        for index, line in enumerate(lines or [], start=1):
            gap(f"第 {index} 行工作范围", line.get("scope_of_work"), line.get("scope_of_work_zh"), line.get("scope_of_work_en"))
            gap(f"第 {index} 行备注", line.get("notes"), line.get("notes_zh"), line.get("notes_en"))
            project_id = line.get("project_id")
            if project_id:
                item = catalog_by_id.get(project_id) or catalog_by_id.get(int(project_id)) if str(project_id).isdigit() else None
                if item is not None:
                    if not str(item.get("name_zh") or "").strip():
                        warnings.append(f"第 {index} 行报价项目「{item.get('name') or ''}」没有中文名称。")
                    if not str(item.get("name_en") or "").strip():
                        warnings.append(f"第 {index} 行报价项目「{item.get('name') or ''}」没有英文名称。")
            elif str(line.get("section_name") or "").strip():
                warnings.append(f"第 {index} 行项目名称仍是未关联目录的原文，两种 PDF 会使用同一段文字。")
    return warnings


def _looks_chinese(text):
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))


def _looks_latin(text):
    letters = [char for char in str(text or "") if char.isalpha() and char.isascii()]
    return len(letters) >= 2


LABELS = {
    "en": {
        "doc_title": "QUOTATION",
        "doc_subtitle": "Professional Services",
        "tagline": "FIELD SERVICES \u2022 RETROFIT \u2022 COMMISSIONING",
        "footer_confidential": "Confidential",
        "page": "Page {n}",
        "quotation_no": "Quotation No.",
        "quotation_date": "Quotation Date",
        "valid_until": "Valid Until",
        "prepared_by": "Prepared By",
        "customer": "Customer",
        "contact": "Contact",
        "project_name": "Project Name",
        "project_no": "Project No.",
        "project_location": "Project Location",
        "po_no": "PO No.",
        "scope": "SCOPE OF WORK",
        "project_description": "Project Description",
        "pricing_summary": "PRICING SUMMARY",
        "description": "Description",
        "qty_unit": "Qty / Unit",
        "rate": "Rate",
        "estimated_amount": "Estimated Amount",
        "fixed_price": "Fixed Price",
        "amount": "Amount",
        "estimated_subtotal": "Estimated Subtotal",
        "tax": "Tax",
        "estimated_total": "Estimated Total (before discount)",
        "discount": "Discount",
        "final_amount": "FINAL QUOTED AMOUNT",
        "pricing_basis": (
            "Pricing Basis: Unless expressly identified as Fixed Price, amounts shown "
            "are estimates. Final invoicing will be based on actual work performed, "
            "actual quantities, applicable rates, and reimbursable expenses."
        ),
        "rate_schedule": "RATE SCHEDULE & PROJECT ASSUMPTIONS",
        "billing_category": "Billing Category",
        "billing_unit": "Billing Unit",
        "minimum_increment": "Minimum billing increment: 15 minutes.",
        "travel": "TRAVEL & REIMBURSABLE EXPENSES",
        "expense": "Expense",
        "billing_method": "Billing Method",
        "no_travel": "No reimbursable travel expenses",
        "not_applicable": "Not Applicable",
        "fixed_amount_prefix": "Fixed Amount",
        "assumptions": "PROJECT ASSUMPTIONS",
        "crew_size": "Estimated Crew Size",
        "workdays": "Estimated Workdays",
        "hours_per_day": "Estimated Hours / Day",
        "expected_start": "Expected Start Date",
        "expected_completion": "Expected Completion",
        "normal_hours": "Normal Working Hours",
        "customer_provides": "Customer Provides",
        "other": "Other",
        "payment": "PAYMENT & INVOICING",
        "payment_terms": "Payment Terms",
        "advance_payment": "Advance Payment",
        "validity": "Quotation Validity",
        "invoice_frequency": "Invoice Frequency",
        "currency": "Currency",
        "pricing_type": "Pricing Type",
        "tax_note": "Tax",
        "other_prefix": "Other",
        "acceptance": "CUSTOMER ACCEPTANCE",
        "acceptance_body": (
            "By signing below, issuing a purchase order referencing this Quotation, "
            "approving it by email or other written electronic communication, "
            "instructing Prasinos Power to mobilize or proceed, permitting work to "
            "begin, or making payment related to the Work, Customer accepts this "
            "Quotation and the attached Standard Quotation Terms and Conditions."
        ),
        "customer_company": "Customer / Company",
        "representative": "Authorized Representative",
        "rep_name": "Yongming Chen",
        "title": "Title",
        "rep_title": "CEO",
        "signature": "Signature",
        "date": "Date",
        "terms_title": "STANDARD QUOTATION TERMS AND CONDITIONS",
        "terms_intro": "These Terms are incorporated into and form part of each quotation issued by Prasinos Power.",
        "acknowledgement_title": "CUSTOMER ACKNOWLEDGEMENT",
        "acknowledgement": (
            "Customer acknowledges that it has reviewed and accepts the Quotation and "
            "these Standard Quotation Terms and Conditions."
        ),
        "party_a": "Party A",
        "party_b": "Party B",
        "total": "Total",
        "grand": "Grand Total",
        "subtotal": "Subtotal",
        "line_no": "No.",
        "line_section": "Section",
        "line_scope": "Scope of Work",
        "line_qty": "Qty",
        "line_unit": "Unit",
        "line_price": "Unit Price",
        "line_amount": "Amount",
        "line_notes": "Notes",
        "lump": "Lump sum",
    },
    "zh": {
        "doc_title": "报价单",
        "doc_subtitle": "专业服务",
        "tagline": "现场服务 \u2022 改造 \u2022 调试",
        "footer_confidential": "保密",
        "page": "第 {n} 页",
        "quotation_no": "报价编号",
        "quotation_date": "报价日期",
        "valid_until": "有效期至",
        "prepared_by": "编制人",
        "customer": "客户",
        "contact": "联系人",
        "project_name": "项目名称",
        "project_no": "项目编号",
        "project_location": "项目地点",
        "po_no": "采购单号",
        "scope": "工作范围",
        "project_description": "项目描述",
        "pricing_summary": "定价汇总",
        "description": "描述",
        "qty_unit": "数量 / 单位",
        "rate": "费率",
        "estimated_amount": "预估金额",
        "fixed_price": "固定总价",
        "amount": "金额",
        "estimated_subtotal": "预估小计",
        "tax": "税",
        "estimated_total": "折扣前预估总额",
        "discount": "折扣",
        "final_amount": "最终报价金额",
        "pricing_basis": "计价说明：除非明确标为固定总价，所列金额均为预估。最终开票以实际完成的工作、实际数量、适用费率及可报销费用为准。",
        "rate_schedule": "费率表与项目假设",
        "billing_category": "计费类别",
        "billing_unit": "计费单位",
        "minimum_increment": "最小计费单位：15 分钟。",
        "travel": "差旅与可报销费用",
        "expense": "费用",
        "billing_method": "计费方式",
        "no_travel": "无可报销差旅",
        "not_applicable": "不适用",
        "fixed_amount_prefix": "固定金额",
        "assumptions": "项目假设",
        "crew_size": "预估人数",
        "workdays": "预估工作日",
        "hours_per_day": "每日预估工时",
        "expected_start": "预计开始日期",
        "expected_completion": "预计完成",
        "normal_hours": "正常工作时段",
        "customer_provides": "客户提供",
        "other": "其他",
        "payment": "付款与开票",
        "payment_terms": "付款条款",
        "advance_payment": "预付款比例",
        "validity": "报价有效期",
        "invoice_frequency": "开票频率",
        "currency": "货币",
        "pricing_type": "定价类型",
        "tax_note": "税",
        "other_prefix": "其他",
        "acceptance": "客户接受",
        "acceptance_body": "客户签署本报价单、发出引用本报价单的采购单、以电子邮件或其他书面电子方式批准、指示 Prasinos Power 进场或开工、允许开始工作，或支付与工作相关的款项，即视为接受本报价单及所附标准报价条款。",
        "customer_company": "客户 / 公司",
        "representative": "授权代表",
        "rep_name": "Yongming Chen",
        "title": "职务",
        "rep_title": "首席执行官",
        "signature": "签字",
        "date": "日期",
        "terms_title": "标准报价条款与条件",
        "terms_intro": "本条款构成 Prasinos Power 所发出的每份报价单的组成部分。",
        "acknowledgement_title": "客户确认",
        "acknowledgement": "客户确认已审阅并接受本报价单及本标准报价条款与条件。",
        "party_a": "甲方",
        "party_b": "乙方",
        "total": "合计",
        "grand": "合计",
        "subtotal": "小计",
        "line_no": "序号",
        "line_section": "项目名称",
        "line_scope": "工作范围",
        "line_qty": "数量",
        "line_unit": "单位",
        "line_price": "单价",
        "line_amount": "金额",
        "line_notes": "备注",
        "lump": "总价包干",
    },
}


TERMS_ZH = [
    ("1. 工作范围", [
        "承包方应提供报价单中明确描述的人工、现场服务、技术服务、维修、改造、安装、调试、检查、故障排查、材料、设备、差旅或其他服务（下称“工作”）。",
        "只有报价单明确列出的工作包含在报价中。",
        "任何额外工作、额外设备、额外到场、数量或范围变化、因承包方无法控制的条件造成的返工，或客户要求的变更，均视为额外工作，并可能产生额外费用。",
    ]),
    ("2. 接受与合同成立", [
        "报价单与本条款共同构成承包方对所述工作的要约。",
        "出现下列任一情形时，客户即视为接受报价单及本条款：签署报价单；发出引用报价单的采购单；以电子邮件、短信、电子通信或其他书面方式批准；指示承包方排期、进场、派遣人员、采购材料或开始工作；允许承包方开始工作；或支付与工作相关的任何款项。",
        "接受后，报价单与本条款构成双方有约束力的协议。",
        "除非承包方另有书面明确同意，客户采购单或其他文件中的冲突或附加条款不改变本条款。",
    ]),
    ("3. 报价有效期", [
        "除非报价单另有说明，价格自发出之日起三十个日历日内有效。",
        "过期后，承包方可调整价格、人工费率、差旅费用、材料费用或可安排的工期。",
    ]),
    ("4. 人工费用", [
        "人工按报价单载明的费率计费。",
        "可计费人工可包括正常工时、加班、节假日、交通时间、公共交通时间、等待或待命时间、技术支持、调试或故障排查时间，以及报价单明确列出的其他类别。",
        "除非另有说明，计费时间按十五分钟为增量计算。",
    ]),
    ("5. 交通时间", [
        "承包方人员前往、离开或往返项目地点所需的交通时间，可按报价单中的交通时间费率计费。",
        "交通时间可包括自指定出发地、临时住宿、机场、租车点、上一项目现场或其他合理的项目相关地点出发的行程。",
        "客户要求的工期变更若导致额外交通，可产生额外交通时间费用。",
    ]),
    ("6. 里程与交通", [
        "承包方人员使用个人或公司车辆时，可按报价单中的里程费率计费。",
        "里程可包括往返项目现场、现场之间、现场与住宿之间、为必要材料或物资，以及其他合理的项目相关交通。",
        "机票、租车、过路费、停车、网约车、公共交通、行李及其他项目相关交通费用，除非报价单明确包含，可另行计费。",
    ]),
    ("7. 等待时间与现场延误", [
        "人员已准备好并可以工作，但因承包方合理控制之外的情况无法进行时，该时间可以计费。",
        "可计费等待包括等待进场、客户代表、陪同、许可或授权；安全培训或现场入场手续；设备停机或送电延误；上锁挂牌延误；等待设备、零件、材料、工具或资料；客户或第三方协调延误；人员须留在现场时的天气暂停；安保限制；网络、软件、通信或系统访问延误；客户、业主、总包、设计施工方、设备厂家或其他方要求的停工；以及非承包方造成的其他延误。",
        "等待时间按报价单费率计费。承包方可用工时记录、照片、定位记录、进场记录、电子邮件、短信、工作报告或其他合理证据予以证明。",
    ]),
    ("8. 进场与撤场", [
        "除非固定项目价格已明确包含，与项目相关的合理进场和撤场费用可向客户收取。",
        "若承包方已按客户授权进场，而项目随后延误、取消、推迟或改期，客户仍应承担已经发生的费用。",
    ]),
    ("9. 住宿", [
        "为完成工作合理需要过夜时，住宿可按报价单计费。",
        "除非另有明确约定，客户应承担承包方人员合理的项目相关住宿费用。适用税费、停车、酒店强制费用及类似费用也可计费。",
    ]),
    ("10. 餐食与每日津贴", [
        "仅在报价单载明、客户批准或项目协议另有要求时，才计收餐食或每日津贴。",
    ]),
    ("11. 材料、零件、耗材与设备", [
        "除非报价单明确写明，零件、材料、耗材、工具、租赁设备、运输、运费、加急费用及其他项目专项采购不包含在报价内。",
        "开工后新增的材料可另行计费。对于厂家、供应商、承运人或客户提供材料短缺且超出承包方合理控制的延误，承包方不承担责任。",
    ]),
    ("12. 客户提供的材料与设备", [
        "对于客户或第三方提供的设备、零件、材料、图纸、软件、信息或指示存在缺陷、故障、不兼容、规格错误、短缺或延误，承包方不承担责任。",
        "因此产生的额外人工可以计费。",
    ]),
    ("13. 变更与额外工作", [
        "客户可以要求变更工作。额外工作可通过书面变更单、修订后的采购单、电子邮件、短信、电子消息、现场书面授权，或客户授权代表的其他书面指示确认。",
        "为避免工期延误、设备损坏、安全风险或额外成本而需要立即处理时，承包方可依据客户书面指示继续工作，并按适用费率计收额外工作。",
    ]),
    ("14. 日报与工作记录", [
        "承包方可保存每日服务报告、技术员报告、照片、工时记录、差旅记录、设备记录、测试结果及其他项目资料。",
        "客户如对服务报告或工时记录有善意异议，应在收到后三个工作日内通知承包方。未及时提出异议，可作为客户收到报告且未指出异议的证据，但不影响法律不得放弃的权利。",
    ]),
    ("15. 现场进入与客户责任", [
        "客户应及时、安全地提供进入项目现场及完成工作合理所需区域的条件。",
        "客户负责协调进场、陪同、证件、现场培训、工作许可、设备可用性、停机计划、上锁挂牌、所需图纸和技术资料、客户提供的材料、其他承包方或分包方，以及业主、设计施工方、设备厂家、公用事业或其他相关方的批准。",
        "因未能提供上述条件造成的延误，可按等待时间计费。",
    ]),
    ("16. 安全", [
        "承包方应遵守适用的安全要求，以及已告知承包方的合理现场安全制度。",
        "当承包方合理认为现场存在即时安全风险时，有权停止工作。客户要求的安全程序、培训、许可、陪同和进场所占用的合理时间可以计费。",
    ]),
    ("17. 工期", [
        "任何预计开始日期、完成日期或工期，均基于报价时已有的信息。",
        "工期可能受进场、天气、设备可用性、客户变更、零件供应、第三方活动、安全要求、交通中断、不可抗力或其他承包方合理控制之外的情况影响。",
        "除非另有明确说明，工期为估计，不是保证完工日期。",
    ]),
    ("18. 取消与改期", [
        "客户在承包方已安排人员、采购材料、预订差旅、预留设备或开始进场后取消、延误、推迟或改期的，应承担已经发生的合理费用。",
        "人员已经出发或到达项目地点的，相应交通时间、里程、机票、住宿、等待时间、人工及其他已发生费用仍可计费。",
    ]),
    ("19. 开票", [
        "承包方可在项目完成时、按里程碑、按周、按两周、按月、在可报销费用发生时，或按报价单载明的其他安排开票。",
        "发票可附承包方合理可提供的支持资料，包括服务报告、工时记录、收据、照片或其他项目资料。",
    ]),
    ("20. 付款条款", [
        "除非报价单另有说明，款项应在发票日期起三十个日历日内支付。",
        "客户应及时告知具体的善意发票争议，并说明争议金额和原因。无争议部分应按期支付。",
    ]),
    ("21. 逾期付款", [
        "在适用法律允许的范围内，逾期金额可按每月百分之一点五，或法律允许的最高利率，两者中较低者计息。",
        "在法律和合同允许的范围内，客户还应承担承包方收取欠款所发生的合理费用。",
    ]),
    ("22. 税", [
        "除非另有明确说明，报价不含销售税、使用税、消费税或类似税费。除对承包方净收入征收的税外，适用税费由客户承担。",
        "客户主张免税的，应提供有效的免税证明。",
    ]),
    ("23. 质保", [
        "承包方保证服务将以专业、符合一般行业惯例的方式完成。",
        "除非报价单另有说明，承包方不对第三方制造的设备、零件或材料提供单独质保。厂家质保（如有）按其自身条款执行。",
        "因客户误用、擅自改动、操作不当、既有缺陷、客户提供设备的缺陷、厂家缺陷、他人施工，或承包方合理控制之外的条件造成的故障，承包方不承担责任。",
    ]),
    ("24. 责任限制", [
        "在适用法律允许的最大范围内，任何一方均不对另一方承担间接、附带、特殊、惩戒性或后果性损害，包括利润损失、收入损失、产量损失、使用损失或业务中断，但法律禁止限制或双方另有书面约定的除外。",
        "在适用法律允许的最大范围内，承包方因工作产生的累计责任不超过客户就引起索赔的具体工作已经支付或应当支付的金额，但适用法律禁止该限制的除外。",
    ]),
    ("25. 不可抗力", [
        "任何一方对超出其合理控制的情形造成的迟延或不履行不承担责任，包括恶劣天气、自然灾害、火灾、洪水、疫情、政府行为、公用事业中断、劳工中断、交通中断、供应链中断、战争、内乱或其他类似事件。",
        "受影响一方应尽商业上合理的努力恢复履行。",
    ]),
    ("26. 独立承包方", [
        "承包方是独立承包方，不是客户的雇员、代理人、合伙人或合营方。在遵守现场安全规则和客户协调要求的前提下，承包方负责监督和指挥其人员。",
    ]),
    ("27. 适用法律", [
        "除非另有书面明确约定，本协议受得克萨斯州法律管辖，但不适用其法律冲突规则。",
    ]),
    ("28. 争议地", [
        "除非另有书面明确约定，因报价单或工作引起或与之相关的法律程序，应向得克萨斯州有管辖权的法院提起。双方可在报价单或另行书面协议中指定具体县。",
    ]),
    ("29. 完整协议", [
        "已接受的报价单、本条款、已批准的变更单，以及明确纳入的文件，构成双方关于该工作的协议。",
        "对于与最终接受的协议相冲突的既往讨论或沟通，以上文件取代之。任何修改须经双方授权代表书面同意。",
    ]),
    ("30. 电子通信与签字", [
        "双方同意，电子记录、电子批准、电子签名、电子邮件批准以及其他双方接受的电子通信，可用于报价单和工作。",
        "在适用法律允许的范围内，已接受报价单的电子副本可视为原件。",
    ]),
]
