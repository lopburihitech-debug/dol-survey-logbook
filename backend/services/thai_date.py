"""แปลงค่าวันที่จากไฟล์ที่ผู้ใช้เตรียมเอง (เช่น export จาก Google Sheets) ให้เป็น ISO date string (YYYY-MM-DD)
รองรับหลายรูปแบบที่พบได้บ่อยในชีทงานราชการ เพื่อให้ผู้ใช้ไม่ต้องแก้ไฟล์เดิมมากก่อนนำเข้าระบบ:
  - ISO อยู่แล้ว: 2026-08-22 (หรือ ISO datetime เต็ม)
  - วัน/เดือน/ปี: 22/08/2569 หรือ 22-08-2569 (รับทั้ง พ.ศ. และ ค.ศ. — ปี > 2400 ถือว่าเป็น พ.ศ. แล้วแปลงลบ 543 ให้)
  - วันที่ไทยแบบมีชื่อเดือน: "22 สิงหาคม 2569" หรือ "22 ส.ค. 2569" หรือ "22 ส.ค.2569" (ไม่มีช่องว่างก็รับได้)
ถ้าแปลงไม่ได้จะโยน ValueError พร้อมข้อความอธิบาย เพื่อให้ผู้เรียก (endpoint นำเข้างาน) ใช้รายงานเป็นรายแถวได้
"""
import re
from datetime import date, datetime

_THAI_MONTHS_FULL = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}
_THAI_MONTHS_ABBR = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}
# เก็บ key แบบไม่มีจุดล้วนๆ (ทั้งชื่อเต็มและตัวย่อ) แล้ว normalize ค่าที่รับมาโดยตัดจุดออกก่อนเทียบเสมอ
# กันปัญหา regex ตัดจุดท้ายออกไปเป็นอีกกลุ่มแยกตอน match (เช่น "ธ.ค. 2568" อาจ capture มาเป็น "ธ.ค" ไม่มีจุดท้าย)
THAI_MONTHS = {**_THAI_MONTHS_FULL, **{k.replace(".", ""): v for k, v in _THAI_MONTHS_ABBR.items()}}


def _month_lookup(month_word: str):
    return THAI_MONTHS.get(month_word.replace(".", "").strip())

_DMY_RE = re.compile(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$")
_THAI_WORD_RE = re.compile(r"^(\d{1,2})\s+([ก-๙.]+?)\.?\s*(\d{4})$")


def _to_ce_year(y: int) -> int:
    """ปี > 2400 ถือว่าเป็น พ.ศ. (ปีนี้ พ.ศ. 2569 / ค.ศ. 2026) แปลงเป็น ค.ศ. ให้อัตโนมัติ"""
    return y - 543 if y > 2400 else y


def parse_flexible_date(raw) -> str | None:
    """คืนค่า ISO date string ("YYYY-MM-DD") หรือ None ถ้าไม่มีค่า — โยน ValueError ถ้ามีค่าแต่แปลงไม่ได้"""
    text = (raw or "").strip()
    if not text:
        return None

    # 1) ISO อยู่แล้ว (รองรับ ISO datetime เต็มด้วย)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass

    # 2) วัน/เดือน/ปี ตัวเลขล้วน
    m = _DMY_RE.match(text)
    if m:
        d, mo, y = (int(v) for v in m.groups())
        try:
            return date(_to_ce_year(y), mo, d).isoformat()
        except ValueError as exc:
            raise ValueError(f"วันที่ไม่ถูกต้อง: '{text}' ({exc})") from exc

    # 3) วัน + ชื่อเดือนไทย (เต็ม/ย่อ) + ปี
    m = _THAI_WORD_RE.match(text)
    if m:
        d_str, month_word, y_str = m.groups()
        month_no = _month_lookup(month_word)
        if month_no is None:
            raise ValueError(f"ไม่รู้จักชื่อเดือน '{month_word}' ในค่า '{text}'")
        try:
            return date(_to_ce_year(int(y_str)), month_no, int(d_str)).isoformat()
        except ValueError as exc:
            raise ValueError(f"วันที่ไม่ถูกต้อง: '{text}' ({exc})") from exc

    raise ValueError(f"ไม่รู้จักรูปแบบวันที่: '{text}' (รองรับ YYYY-MM-DD, DD/MM/YYYY หรือ '22 ส.ค. 2569')")
