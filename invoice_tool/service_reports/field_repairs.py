"""Repair lifecycle and customer-facing workbooks."""
import re
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


def nameplate_model(text):
    match = re.search(r"(?im)^\s*mach[i1l]ne[ \t]+type[ \t]*[:：]?[ \t]*(?:\r?\n[ \t]*)?([A-Z0-9][A-Z0-9._/\-]*)[ \t]*$", text)
    if not match or match.group(1).casefold() in {"cooling", "heating", "power"}:
        return ""
    return match.group(1)


def controller_fault_text(text):
    lines = list(dict.fromkeys(line.strip() for line in text.splitlines()
                              if line.strip() and any(ch.isalnum() for ch in line)))
    keywords = re.compile(r"\b(?:alarms?|faults?|errors?|warnings?|trips?|codes?)\b|报警|故障|告警", re.I)
    codes = re.compile(r"\b(?:[A-Z]{1,3}[- ]?\d{1,5}|\d[A-Z]\d{1,4})\b", re.I)
    selected = []
    for index, line in enumerate(lines):
        if keywords.search(line) or codes.fullmatch(line):
            selected.append(line)
            if keywords.search(line) and index + 1 < len(lines) and codes.search(lines[index + 1]):
                selected.append(lines[index + 1])
    return "\n".join(dict.fromkeys(selected or lines))[:1000]


def build_field_repair_workbook(connection, order):
    sheet_book = Workbook()
    sheet = sheet_book.active
    sheet.title = "现场维修统计表"
    sheet.merge_cells("A1:G1")
    sheet["A1"] = "现场维修统计表"
    sheet["A1"].font = Font(bold=True, size=15)
    sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[1].height = 34
    sheet.append(["日期\nDate", "项目地名称\nProject Name", "机器型号\nMachine Model",
                  "出厂编号\nProduct Serial No", "工作内容\nWork Content",
                  "确认人\nCONFIRMED BY", "备注\nNotes"])
    sheet.row_dimensions[2].height = 34
    site = order["client_name"]
    if order["buyer_id"]:
        row = connection.execute("select name from buyers where id = ?", (order["buyer_id"],)).fetchone()
        if row and row["name"]:
            site = row["name"]
    rows = connection.execute(
        "select * from field_repairs where order_id = ? and completed_date is not null order by completed_date, id",
        (order["id"],),
    ).fetchall()
    entries = []
    for row in rows:
        notes = "\n".join(value for value in (f"维修前：{row['fault']}" if row["fault"] else "", row["result"]) if value)
        entries.append([str(row["completed_date"]), site, row["equipment_model"], row["equipment_number"],
                        row["work_content"], "", notes])
    from field_work import group_repair_photos
    legacy_photos = connection.execute(
        "select p.*, u.name as employee_name from field_photos p "
        "left join users u on u.id = p.user_id where p.order_id = ? "
        "and p.photo_type = 'equipment' and p.repair_phase = '' order by p.captured_at, p.id",
        (order["id"],),
    ).fetchall()
    for photo in group_repair_photos([dict(photo) | {"order_number": order["order_number"]} for photo in legacy_photos]):
        work = f"更换水泵保险丝 {photo['pump_fuse_numbers']}" if photo["pump_fuse_numbers"] else ""
        entries.append([str(photo["date"]), site, photo["equipment_model"], photo["equipment_number"],
                        work, "", photo["note"]])
    for entry in sorted(entries, key=lambda entry: entry[0]):
        sheet.append(entry)
    if not entries:
        sheet.append([""] * 7)
    edge = Side(style="thin", color="000000")
    for cells in sheet.iter_rows(min_row=2, max_col=7):
        for cell in cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.border = Border(left=edge, right=edge, top=edge, bottom=edge)
            cell.alignment = Alignment(horizontal="center" if cell.row == 2 else "left", vertical="center", wrap_text=True)
            if cell.row == 2:
                cell.fill = PatternFill("solid", fgColor="9BC2E6")
                cell.font = Font(bold=True)
    for index, width in enumerate((15, 22, 30, 24, 45, 22, 44), 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    output = BytesIO()
    sheet_book.save(output)
    return output.getvalue()
