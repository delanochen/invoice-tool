"""Legacy-compatible invoice helpers with explicit runtime dependencies."""

def render_invoice_pdf(invoice, client, items, *, A4, BytesIO, UnicodeCIDFont, canvas, colors, get_company_profile, get_invoice_terms, get_payment_instructions, invoice_service_order, invoice_totals, money, pdf_text, pdfmetrics):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left = 42
    top = height - 42
    line = 15
    company = get_company_profile()
    payment = get_payment_instructions()
    terms = get_invoice_terms()
    totals = invoice_totals(invoice["id"])
    service_order = invoice_service_order(invoice)

    def ensure_space(required):
        nonlocal y
        if y - required < 42:
            page.showPage()
            y = height - 42

    def wrapped_lines(text, max_width, font_name="STSong-Light", font_size=8):
        lines = []
        for source_line in pdf_text(text).splitlines() or [""]:
            words = source_line.split(" ")
            current = ""
            for word in words:
                candidate = word if not current else f"{current} {word}"
                if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                if pdfmetrics.stringWidth(word, font_name, font_size) <= max_width:
                    current = word
                    continue
                current = ""
                chunk = ""
                for char in word:
                    candidate = chunk + char
                    if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
                        chunk = candidate
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = char
                current = chunk
            lines.append(current)
        return [line for line in lines if line]

    def draw_section(title, body):
        nonlocal y
        if not pdf_text(body).strip():
            return
        body_lines = wrapped_lines(body, width - left * 2, font_size=8)
        ensure_space(16 + len(body_lines) * 11)
        page.setFont("STSong-Light", 9)
        page.drawString(left, y, title)
        y -= 12
        page.setFont("STSong-Light", 8)
        page.setFillColor(colors.HexColor("#344054"))
        for line_text in body_lines:
            ensure_space(11)
            page.drawString(left, y, line_text)
            y -= 11
        page.setFillColor(colors.black)
        y -= 6

    page.setFont("STSong-Light", 18)
    page.drawString(left, top, pdf_text(company["name"]))
    page.setFont("STSong-Light", 9)
    y = top - 22
    for part in pdf_text(company["address"]).splitlines():
        page.drawString(left, y, part)
        y -= line

    page.setFont("STSong-Light", 26)
    page.drawRightString(width - left, top, "INVOICE")
    page.setFont("STSong-Light", 10)
    meta_y = top - 40
    metadata = [("Invoice No.", invoice["invoice_number"])]
    if service_order and service_order["client_order_number"]:
        metadata.append(("Service Order", service_order["client_order_number"]))
    metadata.extend(
        (
            ("Issue Date", invoice["issue_date"]),
            ("Due Date", invoice["due_date"]),
            ("Currency", invoice["currency"]),
        )
    )
    for label, value in metadata:
        page.drawRightString(width - left - 125, meta_y, label)
        page.drawRightString(width - left, meta_y, pdf_text(value))
        meta_y -= line

    y -= 18
    page.setFont("STSong-Light", 11)
    page.drawString(left, y, "Bill To")
    y -= line
    page.setFont("STSong-Light", 10)
    for value in (client["name"], client["address"], client["country"]):
        for part in pdf_text(value).splitlines():
            if part:
                page.drawString(left, y, part)
                y -= line

    y -= 10
    page.setStrokeColor(colors.HexColor("#d9dee7"))
    page.line(left, y, width - left, y)
    y -= 18
    page.setFont("STSong-Light", 9)
    headers = [("Description", left), ("Amount", 285), ("Tax Rate", 360), ("Tax", 430), ("Line Total", 500)]
    for text, x in headers:
        page.drawString(x, y, text)
    y -= 8
    page.line(left, y, width - left, y)
    y -= 16
    for item in items:
        amount = float(item["amount"] or 0)
        tax = amount * float(item["tax_rate"] or 0) / 100
        page.drawString(left, y, pdf_text(item["description"])[:46])
        page.drawRightString(340, y, money(amount, invoice["currency"]))
        page.drawRightString(410, y, f"{float(item['tax_rate']):.2f}%")
        page.drawRightString(480, y, money(tax, invoice["currency"]))
        page.drawRightString(width - left, y, money(amount + tax, invoice["currency"]))
        y -= line

    y -= 12
    page.line(360, y, width - left, y)
    y -= 16
    for label, value in (("Subtotal", totals["subtotal"]), ("Tax", totals["tax"]), ("Amount Due", totals["total"])):
        page.drawString(365, y, label)
        page.drawRightString(width - left, y, money(value, invoice["currency"]))
        y -= line

    y -= 10
    draw_section("Notes", invoice["notes"])
    draw_section("Terms", terms)
    draw_section(
        "Payment Instructions",
        "\n".join(
            (
                f"Method: {payment['method']}",
                f"Beneficiary Name: {payment['beneficiary']}",
                f"Bank Name: {payment['bank_name']}",
                f"Account Number: {payment['account_number']}",
                f"Routing Number: {payment['routing_number']}",
                f"SWIFT/BIC: {payment['swift_bic']}",
            )
        ),
    )
    draw_section("Tax Note", company["tax_note"])
    page.showPage()
    page.save()
    buffer.seek(0)
    return buffer.getvalue()
