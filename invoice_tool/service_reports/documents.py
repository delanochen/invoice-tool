"""Service-report DOCX rendering, naming and Word attachment packaging."""

def build_service_report_docx(report, order, *, BytesIO, Document, Image, ImageOps, Inches, OxmlElement, Pt, RGBColor, WD_ALIGN_PARAGRAPH, WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT, get_company_profile, get_report_attachments, hours, os, qn, report_attachment_path, report_parts, service_report_workers):
    document = Document()
    section = document.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.55)
    section.right_margin = Inches(0.65)
    section.bottom_margin = Inches(0.5)
    section.left_margin = Inches(0.65)

    normal_style = document.styles["Normal"]
    normal_style.font.name = "Microsoft YaHei"
    normal_style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal_style.font.size = Pt(9)
    normal_style.paragraph_format.space_after = Pt(2)

    def docx_text(value):
        text = "" if value is None else str(value)
        return "".join(
            char
            for char in text
            if char in "\t\n\r" or ord(char) >= 0x20
        )

    def format_run(run, size=9, bold=False, color=None):
        run.font.name = "Microsoft YaHei"
        run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        run.font.size = Pt(size)
        run.bold = bold
        if color:
            run.font.color.rgb = RGBColor(*color)

    def set_cell_shading(cell, fill):
        tc_pr = cell._tc.get_or_add_tcPr()
        shading = tc_pr.find(qn("w:shd"))
        if shading is None:
            shading = OxmlElement("w:shd")
            tc_pr.append(shading)
        shading.set(qn("w:fill"), fill)

    def set_cell_text(cell, text, bold=False, align=WD_ALIGN_PARAGRAPH.LEFT, size=8.5):
        cell.text = ""
        paragraph = cell.paragraphs[0]
        paragraph.alignment = align
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.space_before = Pt(0)
        run = paragraph.add_run(docx_text(text))
        format_run(run, size=size, bold=bold)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    def style_table(table, header_rows=0, column_widths=None):
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        if column_widths:
            for row in table.rows:
                for index, width in enumerate(column_widths):
                    row.cells[index].width = Inches(width)
        for row_index, row in enumerate(table.rows):
            for cell in row.cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                if row_index < header_rows:
                    set_cell_shading(cell, "D9EAD3")
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.bold = True

    def add_section_title(title):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(5)
        paragraph.paragraph_format.space_after = Pt(3)
        run = paragraph.add_run(title)
        format_run(run, size=10, bold=True, color=(15, 118, 110))

    def docx_photo_stream(path):
        buffer = BytesIO()
        with Image.open(path) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image.convert("RGB"))
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((960, 960), Image.Resampling.LANCZOS)
            image.save(buffer, format="JPEG", quality=68, optimize=True)
        buffer.seek(0)
        return buffer

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(8)
    format_run(title.add_run("现场服务日报"), size=18, bold=True)

    for label, value in (
        ("站点名称", order["client_name"]),
        ("业主", order["buyer_owner"]),
        ("服务现场地址", order["site_address"]),
        ("服务订单号码", order["client_order_number"]),
    ):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(2)
        format_run(paragraph.add_run(f"{label}："), bold=True)
        format_run(paragraph.add_run(docx_text(value)))

    workers = service_report_workers(report["id"])
    add_section_title("现场服务人员")
    worker_rows = max(len(workers), 2)
    worker_table = document.add_table(rows=worker_rows + 1, cols=2)
    set_cell_text(worker_table.rows[0].cells[0], "姓名", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell_text(worker_table.rows[0].cells[1], "公司", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    for index in range(worker_rows):
        worker = workers[index] if index < len(workers) else None
        set_cell_text(worker_table.rows[index + 1].cells[0], worker["name"] if worker else "")
        set_cell_text(worker_table.rows[index + 1].cells[1], get_company_profile()["name"] if worker else "")
    style_table(worker_table, header_rows=1, column_widths=[3.5, 3.5])

    summary = document.add_table(rows=2, cols=3)
    labels = ["报告日期", "总计服务工时（小时）", "交通时长（小时）"]
    values = [report["report_date"], report["total_service_hours"], report["travel_hours"]]
    for index, label in enumerate(labels):
        set_cell_text(summary.rows[0].cells[index], label, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell_text(summary.rows[1].cells[index], values[index], align=WD_ALIGN_PARAGRAPH.CENTER)
    style_table(summary, header_rows=1, column_widths=[2.35, 2.35, 2.3])

    add_section_title("现场服务描述")
    description_table = document.add_table(rows=2, cols=1)
    worker_descriptions = [
        f"{worker['name']}：{worker['work_description']}"
        for worker in workers if worker["work_description"]
    ]
    description_text = report["service_description"] or ""
    if worker_descriptions:
        description_text = "\n".join(([description_text] if description_text else []) + worker_descriptions)
    set_cell_text(description_table.rows[0].cells[0], description_text)
    description_table.rows[0].cells[0].paragraphs[0].paragraph_format.space_after = Pt(20)
    set_cell_text(description_table.rows[1].cells[0], f"机柜编号：{report['cabinet_number'] or ''}", bold=True)
    style_table(description_table, column_widths=[7.0])

    add_section_title("公共交通时长及自驾车里程细节")
    travel_table = document.add_table(rows=3, cols=2)
    travel_table.rows[0].cells[0].merge(travel_table.rows[0].cells[1])
    set_cell_text(
        travel_table.rows[0].cells[0],
        f"公共交通时长：{hours(report['public_transport_hours'])} 小时    自驾里程总计：{report['driving_miles'] or 0} 英里",
        bold=True,
    )
    set_cell_text(travel_table.rows[1].cells[0], f"出发地址：{report['departure_address'] or ''}")
    set_cell_text(travel_table.rows[1].cells[1], f"场地地址：{report['site_address'] or order['site_address'] or ''}")
    travel_table.rows[2].cells[0].merge(travel_table.rows[2].cells[1])
    set_cell_text(travel_table.rows[2].cells[0], f"合计用时：{report['total_time'] or ''}")
    style_table(travel_table, column_widths=[3.5, 3.5])

    def add_parts_table(title_text, headers, keys, rows, minimum_rows=4):
        add_section_title(title_text)
        table_rows = list(rows)
        while len(table_rows) < minimum_rows:
            table_rows.append(None)
        table = document.add_table(rows=len(table_rows) + 2, cols=len(headers))
        title_cell = table.rows[0].cells[0]
        for index in range(1, len(headers)):
            title_cell = title_cell.merge(table.rows[0].cells[index])
        set_cell_text(table.rows[0].cells[0], title_text, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell_shading(table.rows[0].cells[0], "B6D7A8")
        for index, header in enumerate(headers):
            set_cell_text(table.rows[1].cells[index], header, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, size=8)
        for row_index, part in enumerate(table_rows, start=2):
            for column_index, key in enumerate(keys):
                set_cell_text(table.rows[row_index].cells[column_index], part[key] if part else "", align=WD_ALIGN_PARAGRAPH.CENTER, size=8)
        widths = [7.0 / len(headers)] * len(headers)
        style_table(table, header_rows=2, column_widths=widths)

    add_parts_table(
        "保存的配件",
        ["零件号", "零件名称", "数量", "状态（新/报废）"],
        ["part_number", "part_name", "quantity", "status"],
        report_parts("service_report_saved_parts", report["id"]),
    )
    add_parts_table(
        "现场更换的配件",
        ["零件号", "零件名称", "旧配件序列号", "新配件序列号", "数量"],
        ["part_number", "part_name", "old_serial_number", "new_serial_number", "quantity"],
        report_parts("service_report_replaced_parts", report["id"]),
    )

    add_section_title("现场时间")
    time_table = document.add_table(rows=2, cols=2)
    set_cell_text(time_table.rows[0].cells[0], "到达现场时间", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell_text(time_table.rows[0].cells[1], "离开现场时间", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell_text(time_table.rows[1].cells[0], report["arrival_time"] or "", align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell_text(time_table.rows[1].cells[1], report["departure_time"] or "", align=WD_ALIGN_PARAGRAPH.CENTER)
    style_table(time_table, header_rows=1, column_widths=[3.5, 3.5])

    attachment_groups = get_report_attachments(report["id"])
    photo_labels = {
        "arrival": "到达现场时间照片",
        "departure": "离开现场时间照片",
        "self_check": "自检照片",
        "site": "现场服务照片",
    }
    for category, section_title in photo_labels.items():
        photos = attachment_groups.get(category, [])
        if not photos:
            continue
        add_section_title(section_title)
        photo_table = document.add_table(rows=(len(photos) + 1) // 2, cols=2)
        photo_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        photo_table.autofit = False
        for index, attachment in enumerate(photos):
            cell = photo_table.rows[index // 2].cells[index % 2]
            path = report_attachment_path(attachment)
            if not os.path.exists(path):
                continue
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                run = paragraph.add_run()
                run.add_picture(docx_photo_stream(path), width=Inches(3.15))
            except Exception:
                format_run(paragraph.add_run("图片无法嵌入"), size=8)

    document.add_paragraph()
    sign_table = document.add_table(rows=2, cols=2)
    set_cell_text(sign_table.rows[0].cells[0], "报告人签字：")
    set_cell_text(sign_table.rows[0].cells[1], "现场服务人员：")
    set_cell_text(sign_table.rows[1].cells[0], f"报告时间：{report['report_date'] or ''}")
    set_cell_text(sign_table.rows[1].cells[1], f"公司：{get_company_profile()['name']}")
    style_table(sign_table, column_widths=[3.5, 3.5])

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def service_report_docx_filename(order, report, used_filenames=None, *, os, secure_filename):
    filename = secure_filename(
        f"{order['client_order_number']}-{report['report_date']}-report.docx"
    ) or f"service-report-{report['id']}.docx"
    if used_filenames is None or filename not in used_filenames:
        return filename
    stem, extension = os.path.splitext(filename)
    return f"{stem}-{report['id']}{extension or '.docx'}"


def service_order_report_word_attachments(order, *, build_service_report_docx, db, service_report_docx_filename):
    reports = db().execute(
        """
        select * from service_reports
        where service_order_id = ?
        order by report_date, id
        """,
        (order["id"],),
    ).fetchall()
    attachments = []
    used_filenames = set()
    for report in reports:
        try:
            content = build_service_report_docx(report, order)
        except Exception as error:
            raise ValueError(f"工作日报 {report['report_date']} Word 文件生成失败，请检查日报内容或照片。") from error
        filename = service_report_docx_filename(order, report, used_filenames)
        used_filenames.add(filename)
        attachments.append(
            {
                "filename": filename,
                "content": content,
                "maintype": "application",
                "subtype": "vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        )
    return attachments
