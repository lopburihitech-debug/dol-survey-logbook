"""
คำนวณวันทำการ (business days) สำหรับ SLA ตามกฎข้อมูลสำคัญในหัวข้อ 1 ของ Blueprint:
"จำนวนวันค้างคำนวณจากวันที่รับเรื่องหรือวันครบกำหนด โดยนับเฉพาะวันทำการ" — ไม่นับเสาร์-อาทิตย์และวันหยุดราชการ
"""
from datetime import datetime, timedelta


def _get_holiday_dates(conn) -> set:
    rows = conn.execute("SELECT date FROM public_holidays").fetchall()
    return {r["date"] for r in rows}


def add_business_days(conn, start: datetime, days: int) -> datetime:
    """บวกจำนวนวันทำการเข้ากับวันที่เริ่มต้น ข้ามเสาร์-อาทิตย์และวันหยุดราชการ"""
    holidays = _get_holiday_dates(conn)
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() >= 5:  # เสาร์=5, อาทิตย์=6
            continue
        if current.date().isoformat() in holidays:
            continue
        added += 1
    return current


def count_overdue_business_days(conn, due_date: datetime, as_of: datetime | None = None) -> int:
    """นับจำนวนวันทำการที่เกินกำหนด (คืนค่า 0 ถ้ายังไม่เกิน)"""
    if as_of is None:
        as_of = datetime.now()
    if as_of <= due_date:
        return 0
    holidays = _get_holiday_dates(conn)
    current = due_date
    count = 0
    while current.date() < as_of.date():
        current += timedelta(days=1)
        if current.weekday() >= 5:
            continue
        if current.date().isoformat() in holidays:
            continue
        count += 1
    return count
