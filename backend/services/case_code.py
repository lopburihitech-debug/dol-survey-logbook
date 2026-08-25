"""สร้างเลข รว.12 อัตโนมัติ รูปแบบ: {รหัสสำนักงาน}-{ปี พ.ศ.}-{running number 5 หลัก}
ตัวอย่าง: LB01-2569-00001
กฎข้อมูลสำคัญ (หัวข้อ 1 ของ Blueprint): เลข รว.12 ต้องไม่ซ้ำ
"""
from datetime import datetime


def generate_case_code(conn, office_id: str) -> str:
    office = conn.execute("SELECT code FROM offices WHERE id = ?", (office_id,)).fetchone()
    if office is None:
        raise ValueError("ไม่พบสำนักงานที่ระบุ")

    be_year = datetime.now().year + 543
    prefix = f"{office['code']}-{be_year}-"

    count_row = conn.execute(
        "SELECT COUNT(*) AS c FROM survey_cases WHERE office_id = ? AND case_code LIKE ?",
        (office_id, f"{prefix}%"),
    ).fetchone()
    count = count_row["c"]

    running = str(count + 1).zfill(5)
    candidate = f"{prefix}{running}"

    while conn.execute("SELECT 1 FROM survey_cases WHERE case_code = ?", (candidate,)).fetchone() is not None:
        count += 1
        running = str(count + 1).zfill(5)
        candidate = f"{prefix}{running}"

    return candidate
