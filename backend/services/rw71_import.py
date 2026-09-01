"""แกะโครงสร้างไฟล์รายงานงานค้าง (เช่น "รว.71") ที่ export มาจากระบบเดิมของสำนักงานเป็น .xlsx ให้กลับมาเป็นรายการ
เรื่องที่นำเข้าระบบนี้ได้ โดยผู้ใช้ "ไม่ต้องจัดระเบียบไฟล์ใหม่" ก่อน — ต่างจากการนำเข้าผ่าน CSV เทมเพลต (ดู
IMPORT_REQUIRED_HEADERS ใน blueprints/survey_cases.py) ที่ต้องมีหัวตารางบรรทัดเดียวตรงเป๊ะ

ไฟล์รายงานประเภทนี้ (พิมพ์ออกมาเป็นรายงานแล้ว export เป็น Excel ต่อ) มีลักษณะเฉพาะที่ทำให้อ่านเป็นตารางตรงๆ ไม่ได้:
  1) หัวตารางมี 2 แถวซ้อนกัน (แถวหัวข้อหลัก + แถวหัวข้อย่อย "เลขลำดับ/ฉบับที่/ลงวันที่/ครั้งที่...")
  2) หัวตารางแบบนี้ถูกพิมพ์ซ้ำทุกครั้งที่รายงานขึ้นหน้าใหม่ (แบ่งเป็นหลายบล็อกข้อมูลคั่นด้วยหัวตารางซ้ำ)
  3) คอลัมน์ "ประเภท" เป็นข้อความอิสระที่พิมพ์ไว้ในระบบเดิม อาจสะกด/เขียนไม่ตรงกับชื่อประเภทงานในระบบนี้เป๊ะๆ
     (เช่น เขียนรวมหมายเหตุไว้ในคอลัมน์เดียวกัน) — โมดูลนี้จึงแค่ "สกัด" ข้อความประเภทงานดิบออกมาเฉยๆ ไม่เดา/แก้ไขให้
     ผู้เรียก (endpoint นำเข้า) เป็นผู้ให้ผู้ใช้จับคู่ข้อความเหล่านี้กับประเภทงานจริงในระบบเองอีกที (ดู blueprints/survey_cases.py)

ฟังก์ชันในไฟล์นี้จึงสแกนทั้งชีทหาแถวหัวตาราง (อาจเจอมากกว่า 1 บล็อก) แล้วดึงเฉพาะแถวข้อมูลจริงออกมาเป็น list of dict
"""
import re

from services.xlsx_reader import XlsxParseError, read_first_sheet_grid

# ข้อความหัวคอลัมน์ที่ต้องเจอในแถวหัวตาราง (normalize ตัดช่องว่าง/บรรทัดใหม่ทั้งหมดออกก่อนเทียบเสมอ กันปัญหาบรรทัด
# หัวคอลัมน์ที่ยาวถูกตัดขึ้นบรรทัดใหม่ในไฟล์จริง เช่น "นัดรังวัด\nวัน เดือน ปี")
_CASE_CODE_HEADER = "เลขที่"
_REQUESTER_HEADER = "ชื่อผู้ขอรังวัด"
_APPOINTMENT_HEADER = "นัดรังวัด"
_TYPE_HEADER = "ประเภท"
_SUBHEADER_MARKER = "เลขลำดับ"  # ข้อความที่อยู่ในแถวหัวตารางย่อย (แถวถัดจากหัวตารางหลัก) ช่องคอลัมน์เดียวกับ "เลขที่"


def _norm(text: str) -> str:
    """ตัดบรรทัดใหม่/ช่องว่างซ้ำทั้งหมดออก แล้ว strip — ใช้เทียบข้อความหัวคอลัมน์ที่อาจตัดขึ้นบรรทัดใหม่ในไฟล์จริง"""
    return re.sub(r"\s+", "", text or "")


def _detect_header_columns(row: list):
    """ถ้าแถวนี้เป็นแถวหัวตารางหลัก (มีครบทั้ง 4 คอลัมน์ที่จำเป็น) คืนค่า dict คอลัมน์ที่ต้องใช้ ({case_code: 0, ...})
    ถ้าไม่ใช่ คืนค่า None"""
    cols = {}
    for idx, raw in enumerate(row):
        norm = _norm(raw)
        if norm == _norm(_CASE_CODE_HEADER) and "case_code" not in cols:
            cols["case_code"] = idx
        elif _norm(_REQUESTER_HEADER) in norm and "requester_name" not in cols:
            cols["requester_name"] = idx
        elif _norm(_APPOINTMENT_HEADER) in norm and "appointment_date" not in cols:
            cols["appointment_date"] = idx
        elif norm == _norm(_TYPE_HEADER) and "type_text" not in cols:
            cols["type_text"] = idx
    required = {"case_code", "requester_name", "appointment_date", "type_text"}
    return cols if required.issubset(cols) else None


def parse_rw71_rows(file_bytes: bytes) -> list:
    """คืนค่า list of dict: [{row_no, case_code, requester_name, type_text, appointment_date_raw}, ...]
    row_no = เลขแถวในไฟล์ Excel จริง (นับจาก 1 ตามที่เห็นเวลาเปิดไฟล์) ไว้ใช้อ้างอิงตอนรายงานแถวที่ข้าม
    โยน XlsxParseError ถ้าอ่านไฟล์ไม่ได้ หรือไม่พบแถวหัวตารางที่จำเป็นเลยทั้งไฟล์ (ไม่ใช่ไฟล์รูปแบบนี้)"""
    grid = read_first_sheet_grid(file_bytes)
    if not grid:
        raise XlsxParseError("ไฟล์ Excel นี้ไม่มีข้อมูลในชีทแรก")

    rows = []
    current_cols = None
    header_found = False
    i = 0
    while i < len(grid):
        row = grid[i]
        cols = _detect_header_columns(row)
        if cols:
            header_found = True
            current_cols = cols
            i += 1
            # แถวถัดจากหัวตารางหลักมักเป็นแถวหัวตารางย่อย ("เลขลำดับ/ฉบับที่/ลงวันที่/ครั้งที่...") — ข้ามไปด้วยถ้าใช่
            if i < len(grid) and _SUBHEADER_MARKER in (grid[i][current_cols["case_code"]] or ""):
                i += 1
            continue

        if current_cols is not None:
            case_code = (row[current_cols["case_code"]] if current_cols["case_code"] < len(row) else "").strip()
            requester_name = (row[current_cols["requester_name"]] if current_cols["requester_name"] < len(row) else "").strip()
            if case_code or requester_name:  # ข้ามแถวว่างเปล่าทั้งแถว (เช่น แถวเว้นบรรทัดท้ายรายงาน) แบบเงียบๆ
                type_text = (row[current_cols["type_text"]] if current_cols["type_text"] < len(row) else "").strip()
                appt_raw = (row[current_cols["appointment_date"]] if current_cols["appointment_date"] < len(row) else "").strip()
                rows.append(
                    {
                        "row_no": i + 1,
                        "case_code": case_code,
                        "requester_name": requester_name,
                        "type_text": type_text,
                        "appointment_date_raw": appt_raw,
                    }
                )
        i += 1

    if not header_found:
        raise XlsxParseError(
            "ไม่พบรูปแบบหัวตารางที่รู้จักในไฟล์นี้ (ต้องมีคอลัมน์ 'เลขที่', 'ชื่อผู้ขอรังวัด', 'นัดรังวัด', 'ประเภท') "
            "กรุณาตรวจสอบว่าเป็นไฟล์รายงานงานค้างที่ export จากระบบเดิมจริงหรือไม่"
        )
    return rows
