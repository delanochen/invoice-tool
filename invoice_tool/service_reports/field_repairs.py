"""Repair lifecycle and customer-facing workbooks."""
import re
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


# field_repairs.fault is PostgreSQL text, so the column itself has no length cap.
# 4000 Unicode characters covers several handheld-controller screens plus a note.
FAULT_TEXT_LIMIT = 4000

_MODEL_LABEL = re.compile(
    r"(?i)(?:(?<![A-Za-z0-9])(?:"
    r"mach[i1l]ne[ \t_-]*type|mach[i1l]ne[ \t_-]*model|product[ \t_-]*(?:model|type)"
    r"|model(?:[ \t_-]*(?:no\.?|number|type))?"
    r")(?![A-Za-z0-9])|设备[ \t]*型号|机器[ \t]*型号|型[ \t]*号)"
)
_TRAILING_LABEL = re.compile(
    r"(?i)\s+(?:mach[i1l]ne[ \t]*(?:number|no\.?)|serial(?:[ \t]*(?:number|no\.?))?|"
    r"manufacture|refrigerant|power(?:[ \t]*supply)?|voltage|frequency|weight|current|"
    r"生产编号|设备编号|出厂编号|制造|制冷剂)\b"
)
_STOP_LINE = re.compile(
    r"(?i)^(?:mach[i1l]ne[ \t]*(?:number|no)|serial|manufacture|refrigerant|power|voltage|frequency|"
    r"生产编号|设备编号|出厂编号|制造年份|制冷剂)"
)
_FAULT_KEYWORDS = re.compile(
    r"报警|告警|故障|干磨|当前|\b(?:alarms?|faults?|errors?|warnings?|trips?|codes?)\b",
    re.I,
)
_PAGE_LINE = re.compile(r"(?i)^(?:page[ \t]*)?\d+[ \t]*/[ \t]*\d+$")


def decode_ocr_output(raw):
    """Decode Tesseract stdout without turning Simplified Chinese into Latin garbage.

    Tesseract 4+ writes UTF-8. A buffer that is not UTF-8 is treated as GB18030,
    which is the other encoding used by older Chinese OCR builds. Dropping invalid
    bytes (UTF-8 with errors='ignore') is intentionally not used.
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw.replace("\ufeff", "")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def _model_value(raw):
    raw = (raw or "").strip().lstrip(":：=#＝ ")
    raw = _TRAILING_LABEL.split(raw, maxsplit=1)[0].strip(" \t:：=#＝")
    raw = re.sub(r"[ \t]*-[ \t]*", "-", raw)
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/\-]*", raw):
        if re.search(r"[A-Za-z]", token) and re.search(r"\d", token) and 4 <= len(token) <= 80:
            return token
    return ""


def nameplate_model(text):
    """Read Machine Type / Model / 型号 even when the next label shares the line."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    for index, line in enumerate(lines):
        match = _MODEL_LABEL.search(line)
        if not match:
            continue
        candidate = _model_value(line[match.end():])
        if candidate:
            return candidate
        if index + 1 < len(lines):
            nxt = lines[index + 1]
            if nxt and not _MODEL_LABEL.search(nxt) and not _STOP_LINE.search(nxt):
                candidate = _model_value(nxt)
                if candidate:
                    return candidate
    return ""


def _join_sparse_cjk(line):
    """Join Tesseract's per-character gaps without gluing real Chinese words together."""
    chars = re.findall(r"[\u4e00-\u9fff]", line)
    if len(chars) < 2:
        return line
    isolated = re.findall(r"(?<![\u4e00-\u9fff])[\u4e00-\u9fff](?![\u4e00-\u9fff])", line)
    if len(isolated) / len(chars) < 0.6:
        return line
    line = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", line)
    line = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=\d)", "", line)
    return re.sub(r"(?<![A-Za-z0-9])(\d)[ \t]+(?=[\u4e00-\u9fff])", r"\1", line)


def _normalize_fault_ocr(text):
    text = decode_ocr_output(text).replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_join_sparse_cjk(line) for line in text.split("\n"))


def controller_fault_text(text):
    lines = []
    for raw in _normalize_fault_ocr(text).splitlines():
        line = raw.strip()
        if not line or _PAGE_LINE.fullmatch(line) or not any(ch.isalnum() for ch in line):
            continue
        if line not in lines:
            lines.append(line)
    selected = []
    for index, line in enumerate(lines):
        if not _FAULT_KEYWORDS.search(line):
            continue
        if line not in selected:
            selected.append(line)
        if index + 1 < len(lines) and lines[index + 1] not in selected:
            nxt = lines[index + 1]
            if _FAULT_KEYWORDS.search(nxt) or re.search(r"[\u4e00-\u9fff]", nxt):
                selected.append(nxt)
    return "\n".join(selected or lines)[:FAULT_TEXT_LIMIT]


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
