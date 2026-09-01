"""ค่าคงที่ร่วม: บทบาทผู้ใช้และสถานะงาน ตามหัวข้อ 0 และ 3 ของ System Blueprint v2.0"""


class Role:
    SYSTEM_ADMIN = "system_admin"
    ADMINISTRATOR = "administrator"  # ผู้บริหาร (เห็น/จัดการได้ทุกจังหวัดทุกสำนักงาน)
    PROVINCE_ADMIN = "province_admin"  # ผู้ดูแลระดับจังหวัด (เหมือน administrator ทุกอย่าง แต่ขอบเขตจำกัดแค่จังหวัดของ
    # สำนักงานที่ตัวเองสังกัดอยู่ — ดู get_user_province()/scope_case_filter() ใน helpers.py สำหรับตรรกะขอบเขต)
    SUPERVISOR = "supervisor"  # หัวหน้าช่างรังวัด
    BRANCH_ADMIN = "branch_admin"  # เจ้าพนักงานที่ดินสาขา — เหมือนหัวหน้าช่างรังวัดทุกอย่าง (ขอบเขตจำกัดแค่สำนักงาน/สาขา
    # ของตัวเอง เช่นเดียวกับ supervisor) บวกสิทธิ์เพิ่มเติม: เพิ่ม/แก้ไขบัญชีช่างรังวัดในสาขาตนเองได้ (ดู surveyors.py)
    # ผู้ดูแลระดับสาขา — เพิ่มตามที่ผู้ใช้ระบบขอ: บทบาทใหม่แยกต่างหากจาก BRANCH_ADMIN ข้างบน (ไม่เกี่ยวกับงานรังวัด/
    # เคสใดๆ เลย ไม่มีสิทธิ์เห็นเมนูงานรังวัด/ปฏิทิน/แผนที่ช่างรังวัด ฯลฯ) มีสิทธิ์เดียวคือเข้าหน้า "จัดการผู้ใช้งาน"
    # (users.py) ได้ แต่ถูกจำกัดขอบเขตแคบมาก: เห็น/สร้าง/แก้ไข/ตั้งรหัสผ่านใหม่/ปิด 2FA ได้เฉพาะบัญชีที่เป็น "ช่างรังวัด"
    # (SURVEYOR) และอยู่สำนักงานเดียวกับตัวเองเท่านั้น (บังคับทั้ง office_id และ role ฝั่ง backend เสมอ ไม่พึ่งฝั่ง
    # frontend อย่างเดียว) เปลี่ยนบทบาท/ย้ายสำนักงานของผู้ใช้ หรือแตะบัญชีบทบาทอื่นใดไม่ได้เลย — กันการเลื่อนสิทธิ์ตัวเอง/
    # ผู้อื่นสูงเกินควร (ดู blueprints/users.py)
    BRANCH_USER_ADMIN = "branch_user_admin"
    SURVEYOR = "surveyor"  # ช่างรังวัด
    CITIZEN = "citizen"  # ประชาชน (ใช้ tracking token/OTP แยกต่างหาก ไม่ผ่าน login นี้ — Phase 3)

    ALL = [SYSTEM_ADMIN, ADMINISTRATOR, PROVINCE_ADMIN, SUPERVISOR, BRANCH_ADMIN, BRANCH_USER_ADMIN, SURVEYOR, CITIZEN]
    INTERNAL = [SYSTEM_ADMIN, ADMINISTRATOR, PROVINCE_ADMIN, SUPERVISOR, BRANCH_ADMIN, BRANCH_USER_ADMIN, SURVEYOR]


class CaseStatus:
    RECEIVED = "RECEIVED"
    WAITING_ASSIGNMENT = "WAITING_ASSIGNMENT"
    ASSIGNED = "ASSIGNED"
    APPOINTED = "APPOINTED"
    IN_SURVEY = "IN_SURVEY"
    WAITING_DOCUMENT = "WAITING_DOCUMENT"
    WAITING_ANNOUNCEMENT = "WAITING_ANNOUNCEMENT"
    PENDING_REVIEW = "PENDING_REVIEW"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    SURVEY_SKIPPED = "SURVEY_SKIPPED"              # งดรังวัด (ไปพบพื้นที่แล้วแต่รังวัดไม่ได้ เช่น เจ้าของไม่อยู่)
    RE_APPOINTMENT_NEEDED = "RE_APPOINTMENT_NEEDED"  # นัดตรวจสอบใหม่ (ยังไม่มีนัด ต้องกำหนดนัดใหม่ทั้งหมด)
    SURVEY_DONE = "SURVEY_DONE"  # รังวัดเสร็จแล้ว (ลงพื้นที่รังวัดเสร็จสิ้นแล้ว รอเจ้าหน้าที่ปิดเรื่อง/ถอนจ่ายต่อไป)

    # สถานะที่ถือว่า "จบงาน/ไม่ใช่งานค้างแล้ว" — ถอนจ่ายแล้ว, ยกเลิก, งดรังวัด (ตามที่ผู้ใช้ระบบยืนยัน ไม่รวม COMPLETED
    # เพราะไม่มีเส้นทางไหนในระบบตั้งสถานะนี้จริง — ดู ALLOWED_TRANSITIONS ด้านล่าง เก็บ COMPLETED ไว้เป็นค่าคงที่เผื่อ
    # ข้อมูลเก่า/นำเข้าจากระบบอื่นเท่านั้น) ใช้คำนวณ "งานค้าง (ยังไม่ปิด)"/"เกินกำหนด" ทั่วทั้งระบบ (dashboard.py,
    # surveyors.py, และฝั่ง frontend ที่มีชุดเดียวกันนี้ซ้ำ — ดู NAV_CLOSED_SET ใน js/api.js, CLOSED_STATUSES ใน
    # dashboard.html, CLOSED_SET ใน my-work.html ต้องแก้พร้อมกันถ้าเปลี่ยนชุดนี้)
    CLOSED_SET = {CLOSED, CANCELLED, SURVEY_SKIPPED}


# การเปลี่ยนสถานะที่อนุญาตแบบ manual ผ่าน PATCH /status
# หมายเหตุ: ตัด PENDING_REVIEW/PENDING_APPROVAL ออกจากเส้นทางหลักแล้ว (เดิมต้องผ่าน QC หัวหน้าช่าง + อนุมัติผู้บริหาร
# 2 ขั้นตอนแยก) ตอนนี้ "ถอนจ่ายแล้ว" (CLOSED) เลือกได้ตรงจากดรอปดาวน์ "เปลี่ยนสถานะ" เลย (จำกัดเฉพาะผู้บริหาร/ผู้ดูแลระบบ
# ที่ endpoint update_status) — endpoint /review และ /approve ยังเก็บไว้เผื่อใช้ QC แยกในอนาคต แต่ไม่ผูกกับ flow หลักแล้ว
# IN_SURVEY / WAITING_DOCUMENT / WAITING_ANNOUNCEMENT / ON_HOLD / PENDING_REVIEW / PENDING_APPROVAL ยังคงไว้
# เพื่อความเข้ากันได้กับประวัติสถานะเก่า แต่ไม่ได้อยู่ในเส้นทางที่เลือกได้จากหน้าจอ "ดำเนินการ" อีกต่อไป
ALLOWED_TRANSITIONS = {
    CaseStatus.RECEIVED: {CaseStatus.WAITING_ASSIGNMENT, CaseStatus.CANCELLED},
    CaseStatus.WAITING_ASSIGNMENT: {CaseStatus.ASSIGNED, CaseStatus.CANCELLED},
    CaseStatus.ASSIGNED: {CaseStatus.APPOINTED, CaseStatus.CANCELLED},
    CaseStatus.APPOINTED: {
        CaseStatus.SURVEY_DONE,
        CaseStatus.CLOSED,
        CaseStatus.POSTPONED,
        CaseStatus.SURVEY_SKIPPED,
        CaseStatus.RE_APPOINTMENT_NEEDED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.POSTPONED: {CaseStatus.APPOINTED, CaseStatus.CANCELLED},
    CaseStatus.SURVEY_SKIPPED: {CaseStatus.RE_APPOINTMENT_NEEDED, CaseStatus.CANCELLED},
    CaseStatus.RE_APPOINTMENT_NEEDED: {CaseStatus.APPOINTED, CaseStatus.CANCELLED},
    # งานที่ต้องแก้ไข/รังวัดซ้ำอาจไม่ได้กลับไปแค่ "รอการรังวัด" เพียงอย่างเดียว — อาจพบว่าต้องเลื่อนนัด/งดรังวัด/
    # นัดตรวจสอบใหม่/ยกเลิก/ถอนจ่าย/บันทึกว่ารังวัดเสร็จแล้วไปเลยก็ได้ ให้ตัวเลือกครบเหมือนตอนอยู่สถานะ "รอการรังวัด" (APPOINTED)
    CaseStatus.REWORK_REQUIRED: {
        CaseStatus.APPOINTED,
        CaseStatus.SURVEY_DONE,
        CaseStatus.CLOSED,
        CaseStatus.POSTPONED,
        CaseStatus.SURVEY_SKIPPED,
        CaseStatus.RE_APPOINTMENT_NEEDED,
        CaseStatus.CANCELLED,
    },
    # รังวัดในพื้นที่เสร็จแล้ว รอเจ้าหน้าที่ปิดเรื่อง — ยังไม่ใช่ขั้นตอนสุดท้าย จึงไปต่อได้ทั้งปิดเรื่อง (ถอนจ่ายแล้ว)
    # หรือย้อนกลับไปแก้ไข/รังวัดซ้ำถ้าพบปัญหาภายหลัง หรือยกเลิกในกรณีพิเศษ — เพิ่ม SURVEY_SKIPPED/RE_APPOINTMENT_NEEDED
    # เผื่อกรณีตรวจพบภายหลังว่าจริงๆ แล้วรังวัดไม่สำเร็จ/ต้องนัดใหม่ทั้งที่บันทึกว่า "รังวัดเสร็จแล้ว" ไปก่อนหน้านี้
    # (จำกัดสิทธิ์เฉพาะผู้บริหาร/ผู้ดูแลระบบเท่านั้น ตรวจที่ endpoint update_status — ดู RESTRICTED ด้านล่าง)
    CaseStatus.SURVEY_DONE: {
        CaseStatus.CLOSED,
        CaseStatus.REWORK_REQUIRED,
        CaseStatus.SURVEY_SKIPPED,
        CaseStatus.RE_APPOINTMENT_NEEDED,
        CaseStatus.CANCELLED,
    },
    # เผื่องานที่ "ถอนจ่ายแล้ว" (CLOSED) ต้องกลับมาแก้ไข/รังวัดซ้ำภายหลัง (เช่น พบข้อผิดพลาด/ต้องซ่อมงาน)
    # อนุญาตให้ย้อนกลับเป็น REWORK_REQUIRED ได้ — จำกัดเฉพาะผู้บริหาร/ผู้ดูแลระบบเท่านั้น (ตรวจที่ endpoint update_status)
    # จากนั้นไหลกลับเข้าเส้นทางปกติผ่าน REWORK_REQUIRED ด้านบน ซึ่งเลือกสถานะถัดไปได้ครบเหมือน APPOINTED
    CaseStatus.CLOSED: {CaseStatus.REWORK_REQUIRED},
    # สถานะเก่าที่คงไว้เพื่อความเข้ากันได้ย้อนหลัง (ข้อมูลเก่า/เรียกผ่าน API โดยตรง)
    CaseStatus.IN_SURVEY: {CaseStatus.WAITING_DOCUMENT, CaseStatus.WAITING_ANNOUNCEMENT, CaseStatus.PENDING_REVIEW, CaseStatus.ON_HOLD},
    CaseStatus.WAITING_DOCUMENT: {CaseStatus.IN_SURVEY, CaseStatus.PENDING_REVIEW, CaseStatus.ON_HOLD},
    CaseStatus.WAITING_ANNOUNCEMENT: {CaseStatus.PENDING_REVIEW, CaseStatus.ON_HOLD},
    CaseStatus.ON_HOLD: {CaseStatus.ASSIGNED, CaseStatus.APPOINTED, CaseStatus.IN_SURVEY, CaseStatus.WAITING_DOCUMENT, CaseStatus.CANCELLED},
    CaseStatus.PENDING_REVIEW: {CaseStatus.PENDING_APPROVAL, CaseStatus.REWORK_REQUIRED},
    CaseStatus.PENDING_APPROVAL: {CaseStatus.CLOSED},
}

# สถานะที่จำกัดสิทธิ์เฉพาะผู้บริหาร/ผู้ดูแลระบบ เวลาเลือกจากดรอปดาวน์ "เปลี่ยนสถานะ" (ขั้นตอนสุดท้ายของงาน)
RESTRICTED_STATUS_TRANSITIONS = {CaseStatus.CLOSED}

# เปลี่ยนสถานะจาก "รังวัดเสร็จแล้ว" (SURVEY_DONE) ไปเป็นสถานะเหล่านี้ — จำกัดสิทธิ์เฉพาะผู้บริหาร/ผู้ดูแลระบบ
# ตั้งแต่ระดับจังหวัดขึ้นไป (system_admin/administrator/province_admin) เท่านั้น เพราะเป็นการย้อนกลับว่างานที่เคย
# บันทึกว่ารังวัดเสร็จแล้วจริงๆ แล้วไม่เสร็จ/ต้องนัดใหม่ — แยกจาก RESTRICTED_STATUS_TRANSITIONS ด้านบนเพราะตัวนั้น
# gate ตาม new_status อย่างเดียวไม่สนสถานะปัจจุบัน ส่วนตัวนี้ gate เฉพาะเมื่อสถานะปัจจุบันคือ SURVEY_DONE เท่านั้น
# (การเปลี่ยนเป็นสถานะเดียวกันนี้จากสถานะอื่น เช่น APPOINTED/REWORK_REQUIRED ยังคงไม่จำกัดสิทธิ์เหมือนเดิม)
RESTRICTED_FROM_SURVEY_DONE = {CaseStatus.SURVEY_SKIPPED, CaseStatus.RE_APPOINTMENT_NEEDED}

# ตัวเลือกสถานะที่แสดงในหน้าจอ "ดำเนินการ" ของ case.html (ใช้ทั้ง backend เพื่ออ้างอิง label และ frontend เพื่อ mirror)
ACTION_STATUS_LABELS = {
    CaseStatus.APPOINTED: "รอการรังวัด",
    CaseStatus.SURVEY_DONE: "รังวัดเสร็จแล้ว",
    CaseStatus.CLOSED: "ถอนจ่ายแล้ว",
    CaseStatus.CANCELLED: "ยกเลิก",
    CaseStatus.SURVEY_SKIPPED: "งดรังวัด",
    CaseStatus.RE_APPOINTMENT_NEEDED: "นัดตรวจสอบใหม่",
    CaseStatus.POSTPONED: "เลื่อนรังวัด",
}
