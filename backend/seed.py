"""
สคริปต์สร้างข้อมูลตั้งต้น (Phase 0: เตรียมข้อมูลตั้งต้น ตามหัวข้อ 8 ของ Blueprint)
รันด้วยคำสั่ง: python seed.py
"""
import hashlib
from datetime import datetime, timedelta

from db import get_connection, init_db
from helpers import new_id, now_iso
from security import hash_password
from services.case_code import generate_case_code
from services.sla import add_business_days

# รายชื่อประเภทงานรังวัดตามที่ใช้จริงของกรมที่ดิน — ใส่ไว้ครบตามที่ใช้งานจริง
# (เพิ่มเติมได้ทีหลังผ่านหน้า "รับเรื่อง รว.12 ใหม่" -> ช่องประเภทงาน -> พิมพ์ชื่อใหม่ที่ไม่มีในรายการ)
SURVEY_TYPE_MASTER_LIST = [
    "กันเขตส่วนที่เพิกถอนตามคำพิพากษา",
    "ขอคัดถ่ายระวาง",
    "ขอถอนสภาพที่ดินอันเป็นสาธารณสมบัติของแผ่นดินฯ",
    "ขออนุญาตขุดดินลูกรัง (ตามมาตรา ๙)",
    "ขออนุญาตดูดทราย",
    "ขออนุญาตสัมปทานในที่ดินของรัฐ ตามมาตรา 12",
    "ขอใช้ที่สาธารณประโยชน์ (ตามมาตรา ๙)",
    "ขอใช้ประโยชน์ในที่ดินของรัฐ",
    "ชี้ตำเเหน่งที่สาธารณประโยชน์",
    "ชี้ตำเเหน่งหนังสือรับรองการทำประโยชน์",
    "ชี้ตำเเหน่งอาคารชุด",
    "ชี้ตำเเหน่งโฉนด",
    "ตรวจสอบ น.ส. 3",
    "ตรวจสอบ น.ส. 3 ก",
    "ตรวจสอบ น.ส. 3 ข",
    "ตรวจสอบทางสาธารณประโยชน์ (ตามมาตรา ๙)",
    "ตรวจสอบที่สาธารณประโยชน์",
    "ตรวจสอบพื้นที่อาคาร",
    "ตรวจสอบหนังสือสำคัญสำหรับที่หลวง",
    "ตรวจสอบหนังสือสำคัญสำหรับที่หลวง(งานโครงการ)",
    "ตรวจสอบหนังสืออนุญาตให้เข้าทำประโยชน์ในเขตนิคมสร้างตนเอง",
    "ตรวจสอบเนื้อที่ใบจอง (น.ส. 2)",
    "รวม น.ส. 3",
    "รวม น.ส. 3 ก",
    "รวมตราจองที่ตราว่า \"ได้ทำประโยชน์เเล้ว\"",
    "รวมหนังสือรับรองการทำประโยชน์",
    "รวมหนังสือสำคัญสำหรับที่หลวง",
    "รวมโฉนดตราจอง",
    "รวมโฉนดที่ดิน",
    "รังวัดซ้ำใบไต่สวน",
    "สอบเขตตราจองฯ เพื่อเปลี่ยนเป็นโฉนดที่ดิน",
    "สอบเขตที่ดิน เเละเปลี่ยนเป็นโฉนดที่ดิน",
    "สอบเขตโฉนดที่ดิน",
    "สอบเขตใบไต่สวน",
    "ออก น.ส. 3 ก",
    "ออก น.ส.3",
    "ออกหนังสือรับรองการทำประโยชน์",
    "ออกหนังสือสำคัญสำหรับที่หลวง",
    "ออกหนังสือสำคัญสำหรับที่หลวง(งานโครงการ)",
    "ออกโฉนดตกค้าง",
    "ออกโฉนดที่งอก",
    "ออกโฉนดที่ดิน",
    "ออกโฉนดที่ดิน (งานจัดรูป ของสำนักงาน)",
    "ออกโฉนดที่ดิน (งานจัดรูป)",
    "ออกโฉนดที่ดิน (งานปฏิรูป)",
    "ออกโฉนดที่ดิน ใบไต่สวน",
    "ออกโฉนดที่ดินแบบท้องถิ่น",
    "ออกใบจอง",
    "เปลี่ยน น.ส. 3 เป็น น.ส. 3 ก",
    "เปลี่ยนตราจองที่ได้ทำประโยชน์แล้วเป็นโฉนดที่ดิน",
    "เปลี่ยนเป็นโฉนดที่ดิน",
    "เปลี่ยนโฉนดตราจองเป็นโฉนดที่ดิน",
    "เเผนที่พิพาท ก.ส.น.5",
    "เเผนที่พิพาท หนังสือรับรองการทำประโยชน์ (น.ส.3 ก.)",
    "เเผนที่พิพาท หนังสือรับรองการทำประโยชน์ (น.ส.3)",
    "เเผนที่พิพาท แบบหมายเลข 3",
    "เเผนที่พิพาทหนังสืออนุญาต",
    "เเผนที่พิพาทโฉนด",
    "แบ่งกรรมสิทธิ์รวม",
    "แบ่งกรรมสิทธิ์รวม ตราจองที่ตราว่า ได้ทำประโยชน์แล้ว",
    "แบ่งกรรมสิทธิ์รวม หนังสือรับรองการทำประโยชน์",
    "แบ่งกรรมสิทธิ์รวม หนังสือรับรองการทำประโยชน์(น.ส.3)",
    "แบ่งขาย",
    "แบ่งขาย หนังสือรับรองการทำประโยชน์",
    "แบ่งขาย เพื่อการทางหลวง",
    "แบ่งขาย เพื่อการทางหลวง (น.ส.3ก)",
    "แบ่งขาย(เพื่อการรถไฟแห่งประเทศไทย)",
    "แบ่งขายกระทรวงการคลัง(เพื่อประโยชน์แก่การชลประทาน)",
    "แบ่งขายเพื่อการชลประทาน",
    "แบ่งขายเพื่อการทางหลวง (ไม่เหมาจ่าย)",
    "แบ่งจัดสรร",
    "แบ่งจัดสรร (หนังสือรับรองการทำประโยชน์)",
    "แบ่งหักเป็นที่สาธารณประโยชน์",
    "แบ่งหักเป็นที่สาธารณประโยชน์ (น.ส.3ก)",
    "แบ่งเพื่อการทางหลวง",
    "แบ่งเวนคืน",
    "แบ่งเวนคืน หนังสือรับรองการทำประโยชน์",
    "แบ่งเเยกหนังสือสำคัญสำหรับที่หลวง",
    "แบ่งเเยกในนามเดิม",
    "แบ่งเเยกในนามเดิม หนังสือรับรองการทำประโยชน์ (น.ส.3)",
    "แบ่งเเยกในนามเดิม หนังสือรับรองการทำประโยชน์ (น.ส.3ก)",
    "แบ่งเเยกในนามเดิม โฉนดตราจอง",
    "แบ่งแยกเพื่อการชลประทาน",
    "แบ่งให้",
    "แบ่งให้ หนังสือรับรองการทำประโยชน์",
    "แบ่งได้มาโดยการครอบครอง",
    "แบ่งได้มาโดยการครอบครอง หนังสือรับรองการทำประโยชน์",
    "แบ่งได้มาโดยการครอบครอง หนังสือรับรองการทำประโยชน์(น.ส.3)",
    "แบ่งได้มาโดยการครอบครอง โฉนดตราจอง",
    "แผนที่พิพาท ส.ค.1",
    "แผนที่พิพาท(ไม่มีเอกสาร/หลักฐาน)",
    "แผนที่พิพาทหนังสือสำคัญสำหรับที่หลวง",
    "แผนที่พิพาทโฉนดตราจอง",
    "ได้มาโดยการครอบครอง",
    "ได้มาโดยการครอบครอง น.ส. 3",
    "ได้มาโดยการครอบครอง น.ส. 3 ก",
]


def seed_survey_type_master_list(conn):
    """เพิ่มประเภทงานตามรายชื่อจริงของกรมที่ดิน — ทำงานแบบ idempotent (เช็คทีละชื่อ ข้ามถ้ามีอยู่แล้ว)
    เรียกทุกครั้งที่รัน seed.py ได้อย่างปลอดภัย แม้ฐานข้อมูลจะมีข้อมูลอื่นอยู่ก่อนแล้ว
    """
    ts = now_iso()
    added = 0
    for name in SURVEY_TYPE_MASTER_LIST:
        if conn.execute("SELECT 1 FROM survey_types WHERE name = ?", (name,)).fetchone():
            continue
        code = f"ST-{hashlib.md5(name.encode('utf-8')).hexdigest()[:8].upper()}"
        while conn.execute("SELECT 1 FROM survey_types WHERE code = ?", (code,)).fetchone():
            code = f"ST-{new_id()[:8].upper()}"
        conn.execute(
            """INSERT INTO survey_types (id, code, name, target_days, requires_announcement, fee_amount, is_active, created_at, updated_at)
               VALUES (?, ?, ?, 30, 0, NULL, 1, ?, ?)""",
            (new_id(), code, name, ts, ts),
        )
        added += 1
    if added:
        conn.commit()
        print(f"เพิ่มประเภทงานตามรายชื่อจริงของกรมที่ดิน {added} รายการ")
    else:
        print("ประเภทงานตามรายชื่อจริงของกรมที่ดินมีครบอยู่แล้ว")


def run():
    init_db()
    conn = get_connection()
    try:
        # เพิ่มรายชื่อประเภทงานตัวจริงก่อนเสมอ ไม่ว่าฐานข้อมูลจะมีข้อมูลอื่นอยู่แล้วหรือไม่
        seed_survey_type_master_list(conn)

        if conn.execute("SELECT 1 FROM offices LIMIT 1").fetchone():
            print("มีข้อมูลตั้งต้นส่วนอื่น (สำนักงาน/ผู้ใช้/ตัวอย่างเรื่อง) อยู่แล้ว ข้ามส่วนนั้น")
            return

        ts = now_iso()

        # --- สำนักงาน ---
        office_id = new_id()
        conn.execute(
            "INSERT INTO offices (id, code, name, province, district, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (office_id, "LB01", "สำนักงานที่ดินจังหวัดลพบุรี", "ลพบุรี", "เมืองลพบุรี", ts, ts),
        )

        # --- วันหยุดราชการตัวอย่าง (ปี 2569) ---
        for d, name in [
            ("2026-08-12", "วันแม่แห่งชาติ"),
            ("2026-10-13", "วันคล้ายวันสวรรคต ร.9"),
            ("2026-12-05", "วันพ่อแห่งชาติ"),
        ]:
            conn.execute(
                "INSERT INTO public_holidays (id, date, name, office_id) VALUES (?, ?, ?, NULL)",
                (new_id(), d, name),
            )

        # --- ประเภทงาน ---
        survey_type_id = new_id()
        conn.execute(
            """INSERT INTO survey_types (id, code, name, target_days, requires_announcement, fee_amount, is_active, created_at, updated_at)
               VALUES (?, 'SPLIT', 'รังวัดแบ่งแยก', 30, 1, 2500, 1, ?, ?)""",
            (survey_type_id, ts, ts),
        )
        conn.execute(
            """INSERT INTO survey_types (id, code, name, target_days, requires_announcement, fee_amount, is_active, created_at, updated_at)
               VALUES (?, 'COMBINE', 'รังวัดรวมโฉนด', 20, 0, 1800, 1, ?, ?)""",
            (new_id(), ts, ts),
        )

        # --- ผู้ใช้ทุกบทบาท (รหัสผ่านตัวอย่างสำหรับทดสอบเท่านั้น ต้องเปลี่ยนก่อนใช้งานจริง) ---
        users = [
            ("admin", "Admin@12345", "ผู้ดูแลระบบ (System Admin)", "system_admin"),
            ("director", "Director@12345", "นายบริหาร ใจดี (ผู้บริหาร)", "administrator"),
            ("supervisor1", "Supervisor@12345", "นายหัวหน้า ช่างเยี่ยม (หัวหน้าช่างรังวัด)", "supervisor"),
            ("branch_admin1", "BranchAdmin@12345", "นางสาวสาขา ดูแลดี (เจ้าพนักงานที่ดินสาขา)", "branch_admin"),
            ("surveyor1", "Surveyor@12345", "นายสมชาย รังวัดดี", "surveyor"),
            ("surveyor2", "Surveyor@12345", "นางสาวสมหญิง ตรวจแปลง", "surveyor"),
        ]
        user_ids = {}
        for username, password, full_name, role in users:
            uid = new_id()
            user_ids[username] = uid
            conn.execute(
                """INSERT INTO users (id, username, password_hash, full_name, role, office_id, mfa_enabled, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?)""",
                (uid, username, hash_password(password), full_name, role, office_id, ts, ts),
            )

        surveyor1_id = new_id()
        conn.execute(
            """INSERT INTO surveyors (id, user_id, employee_code, nickname, position, office_id, is_active, created_at, updated_at)
               VALUES (?, ?, 'SV-001', 'ชาย', 'นายช่างรังวัดชำนาญงาน', ?, 1, ?, ?)""",
            (surveyor1_id, user_ids["surveyor1"], office_id, ts, ts),
        )
        conn.execute(
            """INSERT INTO surveyors (id, user_id, employee_code, nickname, position, office_id, is_active, created_at, updated_at)
               VALUES (?, ?, 'SV-002', 'หญิง', 'นายช่างรังวัดปฏิบัติงาน', ?, 1, ?, ?)""",
            (new_id(), user_ids["surveyor2"], office_id, ts, ts),
        )

        # --- ตัวอย่างเรื่อง รว.12 ---
        received = datetime.now() - timedelta(days=10)
        due = add_business_days(conn, received, 30)
        case_code = generate_case_code(conn, office_id)
        case_id = new_id()
        conn.execute(
            """INSERT INTO survey_cases (id, case_code, office_id, survey_type_id, requester_name, requester_contact,
                                          received_date, due_date, status, priority, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'นายทดสอบ ระบบดี', '081-234-5678', ?, ?, 'ASSIGNED', 'normal', ?, ?, ?)""",
            (case_id, case_code, office_id, survey_type_id, received.isoformat(), due.isoformat(), user_ids["admin"], ts, ts),
        )
        conn.execute(
            "INSERT INTO case_status_history (id, case_id, previous_status, new_status, changed_by, changed_at) VALUES (?, ?, NULL, 'RECEIVED', ?, ?)",
            (new_id(), case_id, user_ids["admin"], ts),
        )
        conn.execute(
            "INSERT INTO case_status_history (id, case_id, previous_status, new_status, changed_by, changed_at) VALUES (?, ?, 'RECEIVED', 'ASSIGNED', ?, ?)",
            (new_id(), case_id, user_ids["admin"], ts),
        )
        conn.execute(
            """INSERT INTO parcels (id, case_id, deed_type, deed_no, parcel_no, survey_sheet_no, sub_district, district, province,
                                     area_rai, area_ngan, area_wa, lat, lng, created_at, updated_at)
               VALUES (?, ?, 'โฉนดที่ดิน', '12345', '678', '5628 III 7089', 'ทะเลชุบศร', 'เมืองลพบุรี', 'ลพบุรี', 2, 1, 45.5, 14.7995, 100.6534, ?, ?)""",
            (new_id(), case_id, ts, ts),
        )
        conn.execute(
            "INSERT INTO case_assignments (id, case_id, surveyor_id, assigned_by, assigned_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (new_id(), case_id, surveyor1_id, user_ids["admin"], ts),
        )
        appointment_start = (datetime.now() + timedelta(days=3)).isoformat()
        conn.execute(
            """INSERT INTO appointments (id, case_id, appointment_start, location, status, created_by, created_at, updated_at)
               VALUES (?, ?, ?, 'แปลงที่ดิน ต.ทะเลชุบศร อ.เมืองลพบุรี', 'SCHEDULED', ?, ?, ?)""",
            (new_id(), case_id, appointment_start, user_ids["admin"], ts, ts),
        )

        conn.commit()
        print("Seed ข้อมูลตั้งต้นสำเร็จ")
        print("บัญชีทดสอบ (username / password):")
        print("  admin / Admin@12345             (ผู้ดูแลระบบ)")
        print("  director / Director@12345       (ผู้บริหาร)")
        print("  supervisor1 / Supervisor@12345  (หัวหน้าช่างรังวัด)")
        print("  surveyor1 / Surveyor@12345      (ช่างรังวัด)")
        print("  surveyor2 / Surveyor@12345      (ช่างรังวัด)")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
