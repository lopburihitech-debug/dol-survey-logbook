"""อ่านไฟล์ .xlsx (Excel) แบบ pure-Python ล้วน ไม่พึ่งไลบรารีภายนอก (เช่น openpyxl) — ให้สอดคล้องกับหลักการเดิม
ของระบบเหมือน services/shapefile_reader.py: ไฟล์ .xlsx คือ zip ที่รวมไฟล์ XML ไว้ข้างใน (Office Open XML)
จึงอ่านได้ตรงๆ ด้วย stdlib zipfile + xml.etree.ElementTree โดยไม่ต้องเพิ่มไลบรารีใหม่ในระบบ

รองรับเฉพาะสิ่งที่จำเป็นสำหรับนำเข้าไฟล์รายงานที่ export มาจากระบบเดิม (ดู services/rw71_import.py ผู้ใช้งานโมดูลนี้):
อ่านค่าตัวอักษร/ตัวเลขของทุกเซลล์ในชีทแรกออกมาเป็น grid ของแถว/คอลัมน์ธรรมดา
  - ไม่รองรับสูตรคำนวณ (formula) — อ่านค่าที่ cache ไว้ล่าสุดถ้ามี (แอตทริบิวต์ t="str") เหมือนข้อความทั่วไป
  - ไม่แปลงตัวเลข serial date เป็นวันที่ (คอลัมน์วันที่ในรายงานที่ต้องอ่านเป็นข้อความไทยอยู่แล้ว เช่น "22 ส.ค. 2569")
  - เซลล์ที่ถูก merge (หัวตารางหลายบรรทัด) จะมีค่าเก็บอยู่ที่เซลล์บนซ้ายสุดของกลุ่มเท่านั้นอยู่แล้วในไฟล์ XML ดิบ —
    ตรงกับที่ต้องใช้งานพอดี ไม่ต้องเขียนโค้ดจัดการ merge เพิ่ม
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_COL_LETTERS_RE = re.compile(r"^([A-Z]+)(\d+)$")


class XlsxParseError(Exception):
    """ไฟล์ไม่ใช่ .xlsx ที่ถูกต้อง หรือมีโครงสร้างที่อ่านไม่ได้"""


def _cell_ref_to_rowcol(ref: str):
    """แปลง cell reference เช่น "C7" -> (แถว, คอลัมน์) เริ่มนับที่ 0 ทั้งคู่ (แถว 7 -> index 6, คอลัมน์ C -> index 2)"""
    m = _COL_LETTERS_RE.match(ref)
    if not m:
        raise XlsxParseError(f"cell reference ไม่ถูกต้อง: '{ref}'")
    letters, row_str = m.groups()
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return int(row_str) - 1, col - 1


def _read_shared_strings(zf: zipfile.ZipFile) -> list:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = []
    for si in root.findall(f"{_NS}si"):
        # <si> อาจมี <t> ตรงๆ (ข้อความธรรมดา) หรือมีหลาย <r><t> (ข้อความที่จัดฟอร์แมตหลายแบบในเซลล์เดียว) ต้องต่อรวมกัน
        text_parts = [t.text or "" for t in si.findall(f"{_NS}t")]
        if not text_parts:
            text_parts = [t.text or "" for t in si.findall(f"{_NS}r/{_NS}t")]
        strings.append("".join(text_parts))
    return strings


def _first_sheet_target(zf: zipfile.ZipFile) -> str:
    """หาไฟล์ XML ของ "ชีทแรก" ตามลำดับที่ประกาศไว้จริงใน workbook.xml (ไม่ใช้แค่เดาว่าเป็น sheet1.xml เสมอ
    เพราะลำดับชีทกับชื่อไฟล์ภายในไม่จำเป็นต้องตรงกันเสมอไป)"""
    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    sheets = workbook_root.findall(f"{_NS}sheets/{_NS}sheet")
    if not sheets:
        raise XlsxParseError("ไม่พบชีทข้อมูลในไฟล์ Excel นี้")
    first_rid = sheets[0].get(f"{_R_NS}id")

    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels_root.findall(f"{_REL_NS}Relationship"):
        if rel.get("Id") == first_rid:
            target = rel.get("Target")
            # Target ตาม OOXML spec มีได้ 2 แบบ: ขึ้นต้นด้วย "/" หมายถึง path แบบเต็มนับจากรากของ package (ตัด "/"
            # นำหน้าออกแล้วใช้ตรงๆ — เช่นไฟล์ที่ openpyxl เขียน) หรือไม่มี "/" นำหน้า หมายถึง path สัมพัทธ์กับโฟลเดอร์
            # ของไฟล์ต้นทาง (xl/_rels/ ก็คือสัมพัทธ์กับ "xl/" — เช่นไฟล์ที่ Excel/LibreOffice เขียน)
            if target.startswith("/"):
                return target.lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise XlsxParseError("อ่านโครงสร้างไฟล์ Excel ไม่สำเร็จ (ไม่พบ relationship ของชีทแรก)")


def read_first_sheet_grid(file_bytes: bytes) -> list:
    """คืนค่า grid ของชีทแรกในไฟล์ .xlsx เป็น list[list[str]] — ค่าว่าง/ไม่มีเซลล์ = "" ทุกแถวถูกเติมให้ยาวเท่ากับ
    คอลัมน์สุดท้ายที่มีข้อมูลในทั้งชีท เพื่อให้ index คอลัมน์ตรงกันทุกแถวเมื่อเข้าถึงแบบ grid[row][col]"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise XlsxParseError("ไฟล์นี้ไม่ใช่ไฟล์ .xlsx ที่ถูกต้อง (เปิดเป็นไฟล์ zip ไม่ได้)") from exc

    try:
        sheet_target = _first_sheet_target(zf)
        shared_strings = _read_shared_strings(zf)
        sheet_root = ET.fromstring(zf.read(sheet_target))
    except KeyError as exc:
        raise XlsxParseError(f"ไฟล์ Excel นี้ขาดส่วนประกอบที่จำเป็น ({exc})") from exc
    except ET.ParseError as exc:
        raise XlsxParseError(f"อ่านโครงสร้าง XML ในไฟล์ Excel ไม่สำเร็จ: {exc}") from exc

    cells = {}  # (row, col) -> str
    max_row = -1
    max_col = -1
    for c_el in sheet_root.findall(f".//{_NS}sheetData/{_NS}row/{_NS}c"):
        ref = c_el.get("r")
        if not ref:
            continue
        row, col = _cell_ref_to_rowcol(ref)
        cell_type = c_el.get("t")
        value = ""
        if cell_type == "inlineStr":
            is_el = c_el.find(f"{_NS}is")
            if is_el is not None:
                value = "".join(t.text or "" for t in is_el.findall(f"{_NS}t"))
        else:
            v_el = c_el.find(f"{_NS}v")
            raw = v_el.text if v_el is not None else None
            if raw is None:
                value = ""
            elif cell_type == "s":  # shared string — raw คือ index เข้า sharedStrings.xml
                try:
                    value = shared_strings[int(raw)]
                except (ValueError, IndexError):
                    value = ""
            else:  # ตัวเลข/ข้อความ formula-cache/บูลีน — ใช้ค่าดิบเป็นข้อความตรงๆ
                value = raw
        cells[(row, col)] = value
        max_row = max(max_row, row)
        max_col = max(max_col, col)

    if max_row < 0:
        return []

    grid = []
    for r in range(max_row + 1):
        grid.append([cells.get((r, c), "") for c in range(max_col + 1)])
    return grid
