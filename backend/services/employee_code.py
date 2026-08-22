"""สร้างรหัสพนักงานช่างรังวัดอัตโนมัติ รูปแบบ: SV-{running number 3 หลัก} เช่น SV-001, SV-002
เรียงจากจำนวนช่างรังวัดที่มีอยู่ในระบบทั้งหมด (นับรวมคนที่ปิดใช้งานแล้วด้วย กันรหัสซ้ำกับของเก่า)
ใช้รูปแบบเดียวกับข้อมูลตัวอย่างที่ seed ไว้ (SV-001, SV-002) เพื่อไม่ให้ชนกับของเดิม
"""


def generate_employee_code(conn) -> str:
    count_row = conn.execute("SELECT COUNT(*) AS c FROM surveyors").fetchone()
    count = count_row["c"]

    running = str(count + 1).zfill(3)
    candidate = f"SV-{running}"

    while conn.execute("SELECT 1 FROM surveyors WHERE employee_code = ?", (candidate,)).fetchone() is not None:
        count += 1
        running = str(count + 1).zfill(3)
        candidate = f"SV-{running}"

    return candidate
