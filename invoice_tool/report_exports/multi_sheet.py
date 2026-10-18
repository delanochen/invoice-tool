"""Legacy-compatible xlsx_multi helpers; explicit runtime dependencies."""

def build_multi_sheet_xlsx(sheets, *, BytesIO, Decimal, html, re, xlsx_column_name, zipfile):
    """多工作表 XLSX（Phase 6B CPA Workpaper 用）。

    sheets: [(sheet_name, headers, rows), ...]；复用 build_simple_xlsx 的
    手写 OOXML 方案，不引入 openpyxl。sheet 名去非法字符并截 31 字节。
    """
    safe_names = []
    for name, _headers, _rows in sheets:
        safe = re.sub(r"[\\/*?:\[\]]", " ", str(name)).strip()[:31] or "Sheet"
        safe_names.append(safe)
    worksheet_parts = []
    workbook_sheets_xml = []
    content_overrides = []
    sheet_rels = []
    for index, ((_name, headers, rows), safe) in enumerate(zip(sheets, safe_names), start=1):
        all_rows = [headers] + rows
        xml_rows = []
        for row_index, row in enumerate(all_rows, start=1):
            cells = "".join(
                f'<c r="{xlsx_column_name(column_index)}{row_index}">'
                + (
                    f'<v>{float(value):.2f}</v></c>'
                    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
                    else f't="inlineStr"><is><t>{html.escape("" if value is None else str(value))}</t></is></c>'
                )
                for column_index, value in enumerate(row)
            )
            xml_rows.append(f'<row r="{row_index}">{cells}</row>')
        dimension = (
            f"A1:{xlsx_column_name(max(len(headers) - 1, 0))}{max(len(all_rows), 1)}")
        worksheet_parts.append(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="{dimension}"/><sheetData>{"".join(xml_rows)}</sheetData>'
            '</worksheet>')
        workbook_sheets_xml.append(
            f'<sheet name="{html.escape(safe)}" sheetId="{index}" r:id="rId{index}"/>')
        content_overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        sheet_rels.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(content_overrides) + '</Types>')
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets_xml)}</sheets></workbook>')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels",
                         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + "".join(sheet_rels) + '</Relationships>')
        for index, sheet_xml in enumerate(worksheet_parts, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml)
    buffer.seek(0)
    return buffer
