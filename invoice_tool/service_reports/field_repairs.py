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
    r"[m]?ach[i1l]ne[ \t_-]*type|[m]?ach[i1l]ne[ \t_-]*model|product[ \t_-]*(?:model|type)"
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


def _clean_model_piece(raw):
    raw = _TRAILING_LABEL.split(raw or "", maxsplit=1)[0]
    raw = re.sub(r"[ \t]*-[ \t]*", "-", raw)
    raw = raw.strip(" \t:：=#＝.,;·|_—–")
    return re.sub(r"[\s_]+", "", raw)


def _is_model_code(piece):
    if not piece or len(piece) < 4 or len(piece) > 80:
        return False
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-]*", piece):
        return False
    if not re.search(r"[A-Za-z]", piece) or not re.search(r"\d", piece):
        return False
    return re.search(r"(?i)kw|hz|mm$|kg|mpa|heating|supply", piece) is None


def _is_model_line(line):
    if _STOP_LINE.search(line) or _MODEL_LABEL.search(line):
        return False
    if re.search(r"(?i)kw|hz|mm|kg|mpa|heating|supply|power|weight|year|current", line):
        return False
    return _is_model_code(_clean_model_piece(line))


# LCI-400CR-01AZ-3487U: letters, digits+CR, two digits+two letters, four digits+letter.
_TONGFEI_SHAPE = "LLL-DDDLL-DDLL-DDDDL"
_DIGIT_FIX = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "A": "4", "S": "5", "B": "8", "G": "6", "Z": "2"})
_LETTER_FIX = str.maketrans({"0": "O", "1": "I", "4": "A", "5": "S", "8": "B", "|": "I"})


def _tongfei_groups(token):
    """Return LLL, DDDLL, DDLL, DDDDL groups, padding a dropped leading L."""
    parts = [part for part in re.sub(r"[^A-Za-z0-9\-]", "", token or "").upper().strip("-").split("-") if part]
    if len(parts) != 4:
        return None
    prefix, block, mid, tail = parts
    if len(prefix) == 2 and prefix[0] in "CI":
        prefix = "L" + prefix
    if len(tail) == 6 and tail[4] in "0O":
        tail = tail[:4] + "U"
    if (len(prefix), len(block), len(mid), len(tail)) != (3, 5, 4, 5):
        return None
    return prefix, block, mid, tail


def _fit_tongfei_shape(token):
    """Map OCR confusions by position: A/O become digits, 4 becomes A in a letter slot."""
    grouped = _tongfei_groups(token)
    if grouped:
        compact = "".join(grouped)
    else:
        compact = re.sub(r"[^A-Za-z0-9]", "", token or "").upper()
    body = _TONGFEI_SHAPE.replace("-", "")
    if len(compact) != len(body):
        return ""
    groups = []
    cursor = 0
    for part in _TONGFEI_SHAPE.split("-"):
        groups.append(compact[cursor:cursor + len(part)])
        cursor += len(part)
    fixed = []
    for part, kind in zip(groups, _TONGFEI_SHAPE.split("-")):
        table = _DIGIT_FIX if kind[0] == "D" else _LETTER_FIX
        # Mixed groups (DDDLL, DDLL, DDDDL) use the kind string character by character.
        if set(kind) == {"D"} or set(kind) == {"L"}:
            fixed.append(part.translate(table))
        else:
            chars = []
            for ch, slot in zip(part, kind):
                chars.append(ch.translate(_DIGIT_FIX if slot == "D" else _LETTER_FIX))
            fixed.append("".join(chars))
    code = "-".join(fixed)
    if re.match(r"(?i)^lc[1il]", code) and "CR" in code:
        code = re.sub(r"(?i)^lc[1il]", "LCI", code)
    # On this plate the last letter is U, which Tesseract often reads as 0.
    if code.startswith("LCI-400CR-") and code[-1] in "0O":
        code = code[:-1] + "U"
    if re.fullmatch(r"[A-Z]{3}-\d{3}[A-Z]{2}-\d{2}[A-Z]{2}-\d{4}[A-Z]", code):
        return code
    return ""


def _normalize_plate_model(token):
    """Correct the engraving confusions seen on the Tongfei liquid-cooling plate.

    I is read as 1 or L, 0 is read as O, and a final U is read as 0. The suffix
    correction only runs when the code still contains the CR / AZ groups, so a
    short misread such as LCL-4000 is not rewritten into a different model.
    """
    shaped = _fit_tongfei_shape(token)
    if shaped:
        return shaped
    if re.search(r"(?i)CR", token) and re.match(r"(?i)^lc[1il]", token):
        token = re.sub(r"(?i)^lc[1il]", "LCI", token)
    token = re.sub(r"O(?=\d)", "0", token)
    if re.search(r"(?i)AZ-\d{4}[0O]$", token):
        token = token[:-1] + "U"
    return token


def _assemble_model(chunks):
    pieces = []
    for chunk in chunks:
        piece = _clean_model_piece(chunk)
        if _is_model_code(piece):
            pieces.append(piece)
    if not pieces:
        return ""
    assembled = pieces[0]
    for piece in pieces[1:]:
        if assembled.endswith("-"):
            assembled += piece
        elif re.fullmatch(r"[A-Za-z]{2,4}-\d{3}0", assembled) and re.match(r"[A-Za-z]{2,4}-", piece):
            # "LCL-400 0" plus the real "CR-..." suffix: the extra 0 is not part of the code.
            assembled = assembled[:-1] + piece
        elif "-" in piece and "-" not in assembled:
            assembled = piece
        else:
            break
    if "-" not in assembled or len(assembled) < 8:
        return ""
    return _normalize_plate_model(assembled)


def _model_window(lines, index, remainder):
    before = []
    blanks = 0
    for prev in range(index - 1, max(-1, index - 6), -1):
        if not lines[prev].strip():
            blanks += 1
            if blanks > 1:
                break
            continue
        if not _is_model_line(lines[prev]):
            break
        before.append(lines[prev])
        blanks = 0
    before.reverse()
    after = []
    for nxt in range(index + 1, min(len(lines), index + 5)):
        if not lines[nxt].strip():
            continue
        if not _is_model_line(lines[nxt]):
            break
        after.append(lines[nxt])
        if not _clean_model_piece(lines[nxt]).endswith("-"):
            break
    return before + [remainder] + after


def nameplate_model(text):
    """Read Machine Type / Model / 型号, including a value printed before the label.

    Sparse OCR of a two-column plate often emits the model on the lines above
    "Machine Type", and splits it at a hyphen. The whole code is assembled
    before a short prefix is accepted.
    """
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    shaped = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{8,}", text or ""):
        code = _fit_tongfei_shape(token)
        if code:
            shaped.append(code)
    if shaped:
        return max(set(shaped), key=lambda code: (shaped.count(code), len(code)))
    best = ""
    for index, line in enumerate(lines):
        match = _MODEL_LABEL.search(line)
        if not match:
            continue
        candidate = _assemble_model(_model_window(lines, index, line[match.end():]))
        if len(candidate) > len(best):
            best = candidate
    return best


_NUMBER_LABEL = re.compile(
    r"(?:mach[i1l]ne\s*n(?:umber|urnber|o\.?|#)|serial\s*(?:number|no\.?|#)|s\s*/?\s*n|生产编号|设备编号|机器编号)",
    re.I,
)


_LETTER_RUN = re.compile(r"[A-Za-z]{4,}")


def machine_number_token(value):
    """A machine number is exactly 13 letters or digits and contains a digit.

    A run of four or more letters is dictionary noise such as member2924040
    or Refrigeration, not a serial engraved among the digits.
    """
    token = re.sub(r"[^A-Za-z0-9]", "", value or "")
    if len(token) != 13 or not re.search(r"\d", token) or _LETTER_RUN.search(token):
        return ""
    return token


def _line_number_tokens(line):
    found = []
    for match in re.finditer(r"[A-Za-z0-9]{13}", line or ""):
        before = line[match.start() - 1:match.start()]
        after = line[match.end():match.end() + 1]
        if (before and before.isalnum()) or (after and after.isalnum()):
            continue
        token = machine_number_token(match.group())
        if token and token not in found:
            found.append(token)
    compact = machine_number_token(line)
    if compact and compact not in found:
        found.append(compact)
    return found


def _remember_number(value, found):
    for token in _line_number_tokens(value):
        if token not in found:
            found.append(token)


def nameplate_number_groups(text):
    """Split vetted 13-character numbers into label-adjacent and other values."""
    text = re.sub(r"(?<=\d)[ \t](?=\d)", "", text or "")
    labeled = []
    used_lines = set()
    lines = text.splitlines() or [text]
    for index, line in enumerate(lines):
        match = _NUMBER_LABEL.search(line)
        if not match:
            continue
        used_lines.add(index)
        _remember_number(line[match.end():], labeled)
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(lines):
                used_lines.add(neighbor)
                _remember_number(lines[neighbor], labeled)
    others = []
    for index, line in enumerate(lines):
        if index in used_lines:
            continue
        _remember_number(line, others)
    if not labeled and not others:
        _remember_number(text, others)
    return labeled, [token for token in others if token not in labeled]


def nameplate_numbers(text):
    """13-character machine numbers. A letter may sit among the digits.

    The value on the Machine Number line, or on the line before or after it,
    comes first. Other candidates are ranked by how many digits they contain.
    Refrigeration and letter-run junk such as member2924040 are never candidates.
    """
    labeled, others = nameplate_number_groups(text)
    others.sort(key=lambda token: -sum(character.isdigit() for character in token))
    return (labeled + others)[:5]


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
