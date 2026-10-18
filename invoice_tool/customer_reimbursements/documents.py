"""Legacy-compatible documents helpers; explicit runtime dependencies."""

def ensure_customer_reimbursement_pdf_record(reimbursement, order, *, db, secrets):
    if reimbursement["file_name"].lower().endswith(".pdf") and reimbursement["stored_filename"].lower().endswith(".pdf"):
        return reimbursement
    db().execute(
        """
        update customer_reimbursements
        set file_name = ?, stored_filename = ?
        where id = ?
        """,
        (
            f"费用报销单{order['client_order_number']}.pdf",
            f"{secrets.token_hex(12)}.pdf",
            reimbursement["id"],
        ),
    )
    return db().execute("select * from customer_reimbursements where id = ?", (reimbursement["id"],)).fetchone()


def build_customer_reimbursement_pdf(reimbursement, order, rows, *, A4, Decimal, LongTable, Paragraph, ParagraphStyle, SimpleDocTemplate, Spacer, TA_CENTER, TA_LEFT, TableStyle, UnicodeCIDFont, colors, customer_reimbursement_file_path, customer_reimbursement_item_expense_amount, customer_reimbursement_totals, get_company_profile, landscape, mm, money, money_decimal, os, pdfmetrics, reimbursement_number, reimbursement_paragraph):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    path = customer_reimbursement_file_path(reimbursement)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    title_style = ParagraphStyle(
        "CustomerReimbursementTitle",
        fontName="STSong-Light",
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#10233F"),
    )
    meta_style = ParagraphStyle(
        "CustomerReimbursementMeta",
        fontName="STSong-Light",
        fontSize=8,
        leading=11,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#475467"),
    )
    header_style = ParagraphStyle(
        "CustomerReimbursementHeader",
        fontName="STSong-Light",
        fontSize=6,
        leading=7,
        alignment=TA_CENTER,
        textColor=colors.white,
    )
    cell_style = ParagraphStyle(
        "CustomerReimbursementCell",
        fontName="STSong-Light",
        fontSize=6,
        leading=7,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#101828"),
    )
    total_style = ParagraphStyle(
        "CustomerReimbursementTotal",
        parent=cell_style,
        fontSize=6.5,
    )

    document = SimpleDocTemplate(
        path,
        pagesize=landscape(A4),
        leftMargin=5 * mm,
        rightMargin=5 * mm,
        topMargin=7 * mm,
        bottomMargin=7 * mm,
        title=reimbursement["file_name"],
        author=get_company_profile()["name"],
    )

    header_group = [
        "姓名", "项目时间", "工时", "", "", "", "", "", "", "", "", "", "", "住宿",
        "交通", "", "", "", "", "", "里程", "", "", "其他", "合计",
    ]
    header_detail = [
        "", "", "标准\n工时", "交通\n工时", "公共交通\n工时", "加班\n工时", "节假日\n工时",
        "标准工时费/小时", "交通工时费/小时", "公共交通费/小时", "加班工时费/小时", "节假日工时费/小时",
        "工时费\n合计", "", "机票", "行李", "租车", "加油", "停车", "打车",
        "英里数", "里程费/英里", "里程费", "", "",
    ]
    table_data = [
        [reimbursement_paragraph(value, header_style) for value in header_group],
        [reimbursement_paragraph(value, header_style) for value in header_detail],
    ]

    for item in rows:
        values = [
            item["worker_name"],
            item["project_date"],
            reimbursement_number(item["standard_hours"]),
            reimbursement_number(item["transport_hours"]),
            reimbursement_number(item["public_transport_hours"]),
            reimbursement_number(item["overtime_hours"]),
            reimbursement_number(item["holiday_hours"]),
            reimbursement_number(item["standard_rate"]),
            reimbursement_number(item["transport_rate"]),
            reimbursement_number(item["public_transport_rate"]),
            reimbursement_number(item["overtime_rate"]),
            reimbursement_number(item["holiday_rate"]),
            reimbursement_number(item["labor_total"]),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "lodging")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "airfare")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "baggage")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "rental_car")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "fuel")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "parking")),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "taxi")),
            reimbursement_number(item["miles"]),
            reimbursement_number(item["mileage_rate"]),
            reimbursement_number(item["mileage_total"]),
            reimbursement_number(customer_reimbursement_item_expense_amount(item, "other")),
            reimbursement_number(item["total"]),
        ]
        table_data.append([reimbursement_paragraph(value, cell_style) for value in values])

    sum_fields = {
        2: "standard_hours", 3: "transport_hours", 4: "public_transport_hours", 5: "overtime_hours", 6: "holiday_hours",
        12: "labor_total", 13: "lodging", 14: "airfare", 15: "baggage", 16: "rental_car",
        17: "fuel", 18: "parking", 19: "taxi", 20: "miles", 22: "mileage_total",
        23: "other", 24: "total",
    }
    total_values = ["Total", ""] + [""] * 23
    for column_index, field_name in sum_fields.items():
        if field_name in {"lodging", "airfare", "baggage", "rental_car", "fuel", "parking", "taxi", "other"}:
            field_total = sum((customer_reimbursement_item_expense_amount(item, field_name) for item in rows), Decimal("0"))
        else:
            field_total = sum((money_decimal(item[field_name]) for item in rows), Decimal("0"))
        total_values[column_index] = reimbursement_number(field_total)
    table_data.append([reimbursement_paragraph(value, total_style) for value in total_values])

    column_widths = [
        width * 0.8 * mm
        for width in (
            20, 19, 10, 10, 11, 10, 11, 15, 15, 15, 15, 15, 15, 12,
            12, 12, 12, 12, 12, 12, 12, 14, 12, 12, 14,
        )
    ]
    table = LongTable(table_data, colWidths=column_widths, repeatRows=2, hAlign="CENTER")
    last_row = len(table_data) - 1
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (0, 1)),
                ("SPAN", (1, 0), (1, 1)),
                ("SPAN", (2, 0), (12, 0)),
                ("SPAN", (13, 0), (13, 1)),
                ("SPAN", (14, 0), (19, 0)),
                ("SPAN", (20, 0), (22, 0)),
                ("SPAN", (23, 0), (23, 1)),
                ("SPAN", (24, 0), (24, 1)),
                ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#0F766E")),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#166F68")),
                ("BACKGROUND", (0, last_row), (-1, last_row), colors.HexColor("#E7F6F2")),
                ("TEXTCOLOR", (0, last_row), (-1, last_row), colors.HexColor("#10233F")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#98A2B3")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#475467")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    pdf_totals = customer_reimbursement_totals(
        rows,
        reimbursement["mro_supplies_total"],
        reimbursement["rental_fuel_total"],
    )
    story = [
        Paragraph("费用报销单", title_style),
        Spacer(1, 3 * mm),
        Paragraph(
            f"工单编号：{order['order_number']}　　服务订单号码：{order['client_order_number']}　　站点：{order['client_name']}",
            meta_style,
        ),
        Spacer(1, 3 * mm),
        table,
        Spacer(1, 3 * mm),
        Paragraph(
            f"其他：{money(pdf_totals['other_total'])}　　"
            f"结算总计：{money(reimbursement['total_amount'])}",
            meta_style,
        ),
        Spacer(1, 4 * mm),
        Paragraph("备注：", meta_style),
    ]

    def draw_page_number(page_canvas, doc):
        page_canvas.saveState()
        page_canvas.setFont("STSong-Light", 7)
        page_canvas.setFillColor(colors.HexColor("#667085"))
        page_canvas.drawRightString(landscape(A4)[0] - 5 * mm, 3 * mm, f"第 {doc.page} 页")
        page_canvas.restoreState()

    document.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)
    return path


def ensure_customer_reimbursement_pdf_file(reimbursement, order, *, build_customer_reimbursement_pdf, customer_reimbursement_file_path, customer_reimbursement_items, ensure_customer_reimbursement_pdf_record, os):
    """Return the current settlement PDF path, generating it when absent.

    Settlement rows are created before their PDF is materialized, and saving or
    submitting a settlement deliberately removes the previous PDF so stale
    content cannot be reused. Every download path therefore needs the same
    lazy-generation behavior.
    """
    reimbursement = ensure_customer_reimbursement_pdf_record(reimbursement, order)
    path = customer_reimbursement_file_path(reimbursement)
    if not os.path.isfile(path):
        path = build_customer_reimbursement_pdf(
            reimbursement,
            order,
            customer_reimbursement_items(reimbursement["id"]),
        )
    return reimbursement, path


def customer_reimbursement_outgoing_attachments(reimbursement, order, *, build_customer_reimbursement_pdf, customer_reimbursement_attachment_path, customer_reimbursement_items, ensure_customer_reimbursement_pdf_record, get_customer_reimbursement_attachments, os, service_order_report_word_attachments):
    reimbursement = ensure_customer_reimbursement_pdf_record(reimbursement, order)
    pdf_path = build_customer_reimbursement_pdf(
        reimbursement,
        order,
        customer_reimbursement_items(reimbursement["id"]),
    )
    attachments = []
    with open(pdf_path, "rb") as file:
        attachments.append(
            {
                "filename": reimbursement["file_name"],
                "content": file.read(),
                "maintype": "application",
                "subtype": "pdf",
            }
        )
    for attachment in get_customer_reimbursement_attachments(reimbursement["id"]):
        path = customer_reimbursement_attachment_path(attachment)
        if not os.path.exists(path):
            continue
        maintype, _, subtype = (attachment["content_type"] or "application/octet-stream").partition("/")
        with open(path, "rb") as file:
            attachments.append(
                {
                    "filename": attachment["original_filename"],
                    "content": file.read(),
                    "maintype": maintype or "application",
                    "subtype": subtype or "octet-stream",
                }
            )
    attachments.extend(service_order_report_word_attachments(order))
    return reimbursement, attachments


def deliver_customer_reimbursement_email(reimbursement, order, *, customer_reimbursement_outgoing_attachments, db, html, log_action, re, record_email_delivery, send_email):
    client = db().execute("select * from clients where id = ?", (order["client_id"],)).fetchone() if order["client_id"] else None
    if not client:
        raise ValueError("这个工单还没有关联客户，请先编辑工单选择客户。")
    recipient = (client["email"] or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient):
        raise ValueError(f"客户 {client['name']} 没有有效邮箱，请先维护客户主数据。")
    subject = f"Customer Reimbursement Statement {order['client_order_number']}"
    body = (
        f"Dear {client['contact_name'] or client['name']},\n\n"
        "Please find the customer reimbursement statement and supporting documents attached."
    )
    reimbursement, attachments = customer_reimbursement_outgoing_attachments(reimbursement, order)
    send_email(
        to=recipient,
        subject=subject,
        html=f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>",
        attachments=attachments,
    )
    record_email_delivery("customer_reimbursement", reimbursement["id"], recipient, subject)
    log_action(
        "send",
        "customer_reimbursement",
        reimbursement["id"],
        reimbursement["file_name"],
        f"发送至：{recipient}；附件：{len(attachments)} 个",
    )
    return recipient
