"""หน้าติดตามงานสำหรับประชาชน (ไม่ต้องล็อกอิน) — public-facing, ไม่ผ่าน @login_required เหมือนหน้าอื่นๆ

ออกแบบให้ปลอดภัยพอสมควรแม้เปิดสาธารณะ เพราะเลข รว.12 (case_code) เรียงเลขต่อเนื่องต่อสำนักงาน (เช่น
LB01-2569-00001, ...00002, ...) เดาได้ไม่ยาก จึง**บังคับต้องมีเบอร์โทรผู้ขอ 4 ตัวท้ายกำกับเสมอ** เป็นปัจจัยที่สอง
และจำกัดอัตราการค้นหาต่อ IP กันการไล่ลองอัตโนมัติ (_rate_limited) — ข้อความ error ตั้งใจให้เหมือนกันทุกกรณีที่หา
ไม่เจอ (ไม่ว่าจะพิมพ์เลข รว.12 ผิด หรือเบอร์โทรผิด) เพื่อไม่ให้แยกแยะได้ว่าเลขที่กรอกมีอยู่จริงในระบบหรือไม่

หมายเหตุ: เลข รว.12 unique เฉพาะภายในสำนักงานเดียวกัน (แต่ละสำนักงานออกเลขของตัวเองแยกกัน อาจซ้ำกันข้ามสำนักงาน
ได้ตามจริง — ดู idx_survey_cases_office_code ใน schema.sql) จึง**บังคับให้เลือกสำนักงานด้วยเป็นปัจจัยที่สาม**
ไม่งั้นถ้ามี 2 สำนักงานใช้เลขเดียวกันพอดี การค้นหาจะกำกวมว่าหมายถึงเรื่องของสำนักงานไหน

ข้อมูลที่คืนให้จงใจตัดส่วนที่อ่อนไหวออกส่วนใหญ่: ไม่มีที่อยู่/เลขโฉนดเต็ม, ไม่มีรูปภาพ/หมุดหลักเขต, ไม่มีเหตุผล
การเปลี่ยนสถานะที่เจ้าหน้าที่บันทึกไว้ภายใน — คงไว้แค่รหัสเรื่อง/ประเภทงาน/สำนักงาน/สถานะปัจจุบัน/วันสำคัญ/
ไทม์ไลน์สถานะ ซึ่งเป็นข้อมูลระดับที่ผู้ขอเรื่องนั้นควรรู้ได้อยู่แล้ว ยกเว้น "ชื่อช่างรังวัดที่รับผิดชอบ" ที่แสดงให้
เห็นตามคำขอของผู้ใช้งาน (ทราบว่าใครดูแลเรื่องของตน) — จงใจแสดงเฉพาะชื่อ ไม่มีเบอร์ติดต่อ/รูปถ่ายของช่างรังวัด
เพื่อจำกัดความเสี่ยงที่เจ้าหน้าที่รายบุคคลจะถูกติดต่อ/คุกคามโดยตรงนอกช่องทางของหน่วยงาน

ส่งข้อความติดตาม/สอบถามและให้คะแนนความพึงพอใจได้จากหน้านี้เช่นกัน (ดู submit_track_message/submit_satisfaction
ด้านล่าง) — ทั้งสอง endpoint ตรวจสอบปัจจัยทั้ง 3 (office_id/case_code/phone_last4) ซ้ำแบบเดียวกับ track_case ทุก
ครั้ง (ผ่าน _lookup_case) ไม่ใช้ข้อมูลที่ frontend เก็บไว้เฉยๆ โดยไม่ตรวจสอบซ้ำ กัน endpoint เหล่านี้ถูกเรียกตรงๆ
ข้ามการค้นหาหลัก
"""
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request

from constants import CaseStatus
from db import get_connection
from helpers import err, new_id, now_iso, ok

bp = Blueprint("public_track", __name__, url_prefix="/api/v1/public")

# ป้ายสถานะภาษาไทยสำหรับประชาชน — คล้าย STATUS_LABELS ฝั่งเจ้าหน้าที่ (frontend/js/api.js) แต่บางสถานะเขียน
# อธิบายเพิ่มให้เข้าใจง่ายขึ้น เพราะประชาชนทั่วไปไม่คุ้นศัพท์ภายในหน่วยงานอย่าง "รอตรวจ QC"
STATUS_LABELS_TH = {
    CaseStatus.RECEIVED: "รับเรื่องแล้ว",
    CaseStatus.WAITING_ASSIGNMENT: "รอมอบหมายช่างรังวัด",
    CaseStatus.ASSIGNED: "มอบหมายช่างรังวัดแล้ว",
    CaseStatus.APPOINTED: "นัดหมายวันรังวัดแล้ว",
    CaseStatus.SURVEY_DONE: "รังวัดในพื้นที่เสร็จแล้ว อยู่ระหว่างดำเนินการปิดเรื่อง",
    CaseStatus.IN_SURVEY: "อยู่ระหว่างดำเนินการรังวัด",
    CaseStatus.WAITING_DOCUMENT: "อยู่ระหว่างดำเนินการ (รอเอกสารเพิ่มเติม)",
    CaseStatus.WAITING_ANNOUNCEMENT: "อยู่ระหว่างดำเนินการ (รอปิดประกาศ)",
    CaseStatus.PENDING_REVIEW: "อยู่ระหว่างตรวจสอบ",
    CaseStatus.PENDING_APPROVAL: "อยู่ระหว่างอนุมัติ",
    CaseStatus.COMPLETED: "เสร็จสิ้น",
    CaseStatus.CLOSED: "ถอนจ่ายแล้ว (เสร็จสิ้นกระบวนการ)",
    CaseStatus.POSTPONED: "เลื่อนนัดรังวัด",
    CaseStatus.CANCELLED: "ยกเลิกคำขอ",
    CaseStatus.ON_HOLD: "พักการดำเนินการชั่วคราว",
    CaseStatus.REWORK_REQUIRED: "อยู่ระหว่างดำเนินการแก้ไข",
    CaseStatus.SURVEY_SKIPPED: "ไปพื้นที่แล้ว ยังไม่สามารถรังวัดได้",
    CaseStatus.RE_APPOINTMENT_NEEDED: "รอนัดหมายวันรังวัดใหม่",
}

# ขั้นตอนอย่างง่าย 1-5 สำหรับวาด progress bar ให้ประชาชนเข้าใจภาพรวมได้เร็วโดยไม่ต้องรู้จักสถานะภายในทั้ง 17 แบบ
# CANCELLED ไม่นับเป็นขั้นตอนความคืบหน้า (stage 0) เพราะเป็นทางตันแยกต่างหาก ไม่ใช่ก้าวไปข้างหน้า
STAGE_MAP = {
    CaseStatus.RECEIVED: 1,
    CaseStatus.WAITING_ASSIGNMENT: 1,
    CaseStatus.ASSIGNED: 2,
    CaseStatus.APPOINTED: 3,
    CaseStatus.POSTPONED: 3,
    CaseStatus.RE_APPOINTMENT_NEEDED: 3,
    CaseStatus.SURVEY_SKIPPED: 3,
    CaseStatus.SURVEY_DONE: 4,
    CaseStatus.IN_SURVEY: 4,
    CaseStatus.WAITING_DOCUMENT: 4,
    CaseStatus.WAITING_ANNOUNCEMENT: 4,
    CaseStatus.PENDING_REVIEW: 4,
    CaseStatus.PENDING_APPROVAL: 4,
    CaseStatus.ON_HOLD: 4,
    CaseStatus.REWORK_REQUIRED: 4,
    CaseStatus.COMPLETED: 5,
    CaseStatus.CLOSED: 5,
    CaseStatus.CANCELLED: 0,
}
STAGE_LABELS = {0: "ยกเลิก", 1: "รับเรื่อง", 2: "มอบหมายช่าง", 3: "นัดหมายรังวัด", 4: "ดำเนินการ", 5: "เสร็จสิ้น"}

# ---- กันไล่เดา — จำกัดจำนวนครั้งค้นหาต่อ IP เก็บในหน่วยความจำ (ไม่ต้องพึ่งฐานข้อมูล/บริการภายนอกเพิ่ม) ----
_RATE_LIMIT_MAX = 15          # ครั้ง
_RATE_LIMIT_WINDOW_SEC = 300  # ต่อ 5 นาที ต่อ 1 IP
_rate_log = defaultdict(deque)

GENERIC_NOT_FOUND = "ไม่พบข้อมูล กรุณาตรวจสอบสำนักงาน เลข รว.12 และเบอร์โทรศัพท์ผู้ขออีกครั้ง"


def _client_ip():
    # รองรับกรณีอยู่หลัง reverse proxy (nginx/Railway/Render) ที่ส่ง IP จริงมาทาง X-Forwarded-For
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    q = _rate_log[ip]
    while q and now - q[0] > _RATE_LIMIT_WINDOW_SEC:
        q.popleft()
    if len(q) >= _RATE_LIMIT_MAX:
        return True
    q.append(now)
    return False


def _digits_only(s):
    return re.sub(r"\D", "", s or "")


def _lookup_case(conn, office_id, case_code, phone_last4):
    """คืน case (dict) ถ้าปัจจัยทั้ง 3 (สำนักงาน/เลข รว.12/เบอร์โทร 4 ตัวท้าย) ตรงกัน มิฉะนั้นคืน None โดยไม่แยกแยะ
    สาเหตุ (ดูเหตุผลด้านบนของไฟล์นี้) — endpoint สาธารณะทุกตัวในไฟล์นี้ (ดูสถานะ/ส่งข้อความ/ให้คะแนนความพึงพอใจ)
    ต้องเรียกผ่านฟังก์ชันนี้เท่านั้น ห้าม query ตรงๆ เอง เพื่อไม่ให้ endpoint ใดข้ามการตรวจสอบ 3 ปัจจัยไปได้"""
    if not office_id or not case_code or len(phone_last4) != 4:
        return None
    case = conn.execute(
        "SELECT * FROM survey_cases WHERE case_code = ? COLLATE NOCASE AND office_id = ?",
        (case_code, office_id),
    ).fetchone()
    if case is None:
        return None
    case = dict(case)
    stored_digits = _digits_only(case.get("requester_contact"))
    if len(stored_digits) < 4 or stored_digits[-4:] != phone_last4:
        return None
    return case


@bp.get("/offices")
def list_offices_public():
    """รายชื่อสำนักงานแบบสาธารณะ (ไม่ต้องล็อกอิน) สำหรับเติม dropdown ในหน้าติดตามงาน — คืนแค่ id/ชื่อ/จังหวัด
    ซึ่งเป็นข้อมูลที่เปิดเผยอยู่แล้ว (ไม่ใช่ข้อมูลอ่อนไหว) ไม่ได้แปะไว้ที่ /api/v1/offices ปกติเพราะ endpoint
    นั้นต้องล็อกอินก่อน (@login_required) — หน้านี้ยังไม่ได้ล็อกอิน จึงต้องมีทางเข้าแยกต่างหากในบลูพรินต์นี้"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, province FROM offices WHERE is_active = 1 ORDER BY province, name"
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.get("/track")
def track_case():
    if _rate_limited(_client_ip()):
        return err("ค้นหาบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่อีกครั้ง", 429)

    office_id = (request.args.get("office_id") or "").strip()
    case_code = (request.args.get("case_code") or "").strip()
    phone_last4 = _digits_only(request.args.get("phone_last4"))

    if not office_id or not case_code or len(phone_last4) != 4:
        return err("กรุณาเลือกสำนักงาน และกรอกเลข รว.12 กับเบอร์โทรศัพท์ผู้ขอ 4 ตัวท้ายให้ครบถ้วน")

    conn = get_connection()
    try:
        case = _lookup_case(conn, office_id, case_code, phone_last4)
        if case is None:
            return err(GENERIC_NOT_FOUND, 404)

        survey_type = conn.execute(
            "SELECT name FROM survey_types WHERE id = ?", (case["survey_type_id"],)
        ).fetchone()
        office = conn.execute("SELECT name FROM offices WHERE id = ?", (case["office_id"],)).fetchone()

        # ชื่อช่างรังวัดที่รับผิดชอบปัจจุบัน (ถ้ามอบหมายแล้ว) — แสดงเฉพาะชื่อ ดูเหตุผลด้านบนของไฟล์นี้
        assigned = conn.execute(
            """SELECT u.full_name FROM case_assignments ca
               JOIN surveyors s ON s.id = ca.surveyor_id
               JOIN users u ON u.id = s.user_id
               WHERE ca.case_id = ? AND ca.is_active = 1""",
            (case["id"],),
        ).fetchone()

        rating_row = conn.execute(
            "SELECT rating, comment FROM case_satisfaction_ratings WHERE case_id = ?", (case["id"],)
        ).fetchone()

        # ข้อความที่ประชาชนส่งมา + คำตอบจากเจ้าหน้าที่/ช่างรังวัด (ถ้ามี) — เรียงเก่าไปใหม่เหมือนแชท ไม่รวมเบอร์
        # ติดต่อหรือชื่อผู้ตอบ (ดูเหตุผลเรื่องจำกัดข้อมูลอ่อนไหวด้านบนของไฟล์นี้)
        message_rows = conn.execute(
            """SELECT description, reply_text, replied_at, created_at FROM complaints
               WHERE case_id = ? AND complaint_type = 'INQUIRY' ORDER BY created_at ASC""",
            (case["id"],),
        ).fetchall()
        messages = [
            {
                "message": r["description"],
                "created_at": r["created_at"],
                "reply": r["reply_text"],
                "replied_at": r["replied_at"],
            }
            for r in message_rows
        ]

        history_rows = conn.execute(
            "SELECT new_status, changed_at FROM case_status_history WHERE case_id = ? ORDER BY changed_at",
            (case["id"],),
        ).fetchall()
        timeline = [
            {
                "status": r["new_status"],
                "status_label": STATUS_LABELS_TH.get(r["new_status"], r["new_status"]),
                "changed_at": r["changed_at"],
            }
            for r in history_rows
        ]

        stage = STAGE_MAP.get(case["status"], 0)
        can_rate = case["status"] in (CaseStatus.COMPLETED, CaseStatus.CLOSED)
        result = {
            "case_code": case["case_code"],
            "requester_name": case.get("requester_name"),
            "survey_type": survey_type["name"] if survey_type else None,
            "office_name": office["name"] if office else None,
            "surveyor_name": assigned["full_name"] if assigned else None,
            "status": case["status"],
            "status_label": STATUS_LABELS_TH.get(case["status"], case["status"]),
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage),
            "is_cancelled": case["status"] == CaseStatus.CANCELLED,
            "received_date": case.get("received_date"),
            "appointment_date": case.get("appointment_date"),
            "due_date": case.get("due_date"),
            "timeline": timeline,
            "can_rate_satisfaction": can_rate,
            "satisfaction_rating": dict(rating_row) if rating_row else None,
            "messages": messages,
        }
        return ok(result)
    finally:
        conn.close()


_MESSAGE_MAX_LEN = 1000

# ---- กันข้อความซ้ำ/ถี่เกินไป — แยกจาก _rate_limited ด้านบนซึ่งจำกัดแค่ระดับ IP รวมทุก endpoint (หยาบเกินไปสำหรับ
# ข้อความ เพราะประชาชนหลายคนอาจใช้เน็ตบ้าน/มือถือ IP เดียวกัน หรือคนเดียวกันสลับเครือข่ายก็หลบผ่านได้) ผูกกับ
# case_id โดยตรงแทน (ผ่านตาราง complaints เดิม ไม่ต้องสร้างตารางใหม่): จำกัดทั้ง "ถี่แค่ไหน" (คูลดาวน์ต่อข้อความ),
# "ซ้ำคำเดิมไหม" (กันกดส่งข้อความเดียวกันซ้ำ), และ "รวมทั้งวันเท่าไหร่" (เพดานต่อวัน กันสแปมด้วยข้อความคนละแบบ) ----
_MESSAGE_COOLDOWN_SEC = 60   # ต้องรออย่างน้อยเท่านี้ก่อนส่งข้อความถัดไปในเรื่องเดียวกัน
_MESSAGE_DAILY_CAP = 5       # ส่งได้ไม่เกินกี่ข้อความต่อเรื่องต่อ 24 ชม. ล่าสุด (rolling window ไม่ใช่ตามปฏิทิน)


@bp.post("/track/message")
def submit_track_message():
    """ประชาชนส่งข้อความติดตามเรื่อง/สอบถามเพิ่มเติมจากหน้า track.html — เก็บร่วมตาราง complaints (complaint_type
    = 'INQUIRY' แยกจากข้อร้องเรียนทั่วไป) ให้เจ้าหน้าที่/ช่างรังวัดเจ้าของเรื่องเห็นและพิมพ์ตอบกลับได้ในหน้ารายละเอียด
    เรื่อง (case.html, ดู reply_case_message ใน blueprints/survey_cases.py) คำตอบจะแสดงกลับมาในหน้านี้ (ดู track_case
    ด้านบน คืน field "messages" ที่มีทั้งข้อความเดิมและคำตอบ) นอกเหนือจากการที่เจ้าหน้าที่จะติดต่อกลับทางโทรศัพท์
    ตามเบอร์ที่ผูกกับเรื่องนั้นโดยตรงด้วยตนเองก็ยังทำได้เช่นเดิม"""
    if _rate_limited(_client_ip()):
        return err("ทำรายการบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่อีกครั้ง", 429)

    payload = request.get_json(silent=True) or {}
    office_id = (payload.get("office_id") or "").strip()
    case_code = (payload.get("case_code") or "").strip()
    phone_last4 = _digits_only(payload.get("phone_last4"))
    message = (payload.get("message") or "").strip()

    if not message:
        return err("กรุณาพิมพ์ข้อความที่ต้องการสอบถามหรือติดตาม")
    if len(message) > _MESSAGE_MAX_LEN:
        return err(f"ข้อความยาวเกินไป (ไม่เกิน {_MESSAGE_MAX_LEN} ตัวอักษร)")

    conn = get_connection()
    try:
        case = _lookup_case(conn, office_id, case_code, phone_last4)
        if case is None:
            return err(GENERIC_NOT_FOUND, 404)

        recent = conn.execute(
            """SELECT description, created_at FROM complaints
               WHERE case_id = ? AND complaint_type = 'INQUIRY'
               ORDER BY created_at DESC LIMIT 1""",
            (case["id"],),
        ).fetchone()
        if recent:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(recent["created_at"])).total_seconds()
            if elapsed < _MESSAGE_COOLDOWN_SEC:
                wait = int(_MESSAGE_COOLDOWN_SEC - elapsed) + 1
                return err(f"ส่งข้อความถี่เกินไป กรุณารออีก {wait} วินาทีแล้วลองใหม่", 429)
            if recent["description"].strip() == message:
                return err("ข้อความนี้ซ้ำกับข้อความล่าสุดที่ส่งไปแล้ว เจ้าหน้าที่ได้รับข้อความนี้แล้ว กรุณารอการติดต่อกลับ", 429)

        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        count_today = conn.execute(
            "SELECT COUNT(*) AS c FROM complaints WHERE case_id = ? AND complaint_type = 'INQUIRY' AND created_at >= ?",
            (case["id"], since),
        ).fetchone()["c"]
        if count_today >= _MESSAGE_DAILY_CAP:
            return err(
                f"ส่งข้อความถึงขีดจำกัด {_MESSAGE_DAILY_CAP} ข้อความต่อวันสำหรับเรื่องนี้แล้ว "
                "กรุณาติดต่อสำนักงานที่ดินโดยตรงหากเร่งด่วน",
                429,
            )

        conn.execute(
            """INSERT INTO complaints (id, case_id, citizen_contact, complaint_type, description, status, created_at)
               VALUES (?, ?, ?, 'INQUIRY', ?, 'OPEN', ?)""",
            (new_id(), case["id"], case.get("requester_contact") or phone_last4, message, now_iso()),
        )
        conn.commit()
        return ok({"submitted": True, "cooldown_sec": _MESSAGE_COOLDOWN_SEC}, 201)
    finally:
        conn.close()


_COMMENT_MAX_LEN = 1000


@bp.post("/track/satisfaction")
def submit_satisfaction():
    """ประชาชนให้คะแนนความพึงพอใจ (1-5, หน้ายิ้ม) หลังงานรังวัดเสร็จสิ้น — ให้คะแนนซ้ำได้ (แก้ไขคะแนนเดิม) แต่เก็บ
    ได้แค่ 1 คะแนนต่อ 1 เรื่องเสมอ (case_id UNIQUE ใน case_satisfaction_ratings) เผื่อกดผิดแล้วอยากแก้"""
    if _rate_limited(_client_ip()):
        return err("ทำรายการบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่อีกครั้ง", 429)

    payload = request.get_json(silent=True) or {}
    office_id = (payload.get("office_id") or "").strip()
    case_code = (payload.get("case_code") or "").strip()
    phone_last4 = _digits_only(payload.get("phone_last4"))
    try:
        rating = int(payload.get("rating"))
    except (TypeError, ValueError):
        rating = None
    if rating not in (1, 2, 3, 4, 5):
        return err("กรุณาเลือกคะแนนความพึงพอใจ (1-5)")
    comment = (payload.get("comment") or "").strip() or None
    if comment and len(comment) > _COMMENT_MAX_LEN:
        return err(f"ความคิดเห็นยาวเกินไป (ไม่เกิน {_COMMENT_MAX_LEN} ตัวอักษร)")

    conn = get_connection()
    try:
        case = _lookup_case(conn, office_id, case_code, phone_last4)
        if case is None:
            return err(GENERIC_NOT_FOUND, 404)
        if case["status"] not in (CaseStatus.COMPLETED, CaseStatus.CLOSED):
            return err("ให้คะแนนความพึงพอใจได้เมื่องานรังวัดเสร็จสิ้นแล้วเท่านั้น")

        ts = now_iso()
        existing = conn.execute(
            "SELECT id FROM case_satisfaction_ratings WHERE case_id = ?", (case["id"],)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE case_satisfaction_ratings SET rating = ?, comment = ?, updated_at = ? WHERE case_id = ?",
                (rating, comment, ts, case["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO case_satisfaction_ratings (id, case_id, rating, comment, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_id(), case["id"], rating, comment, ts, ts),
            )
        conn.commit()
        return ok({"submitted": True, "rating": rating})
    finally:
        conn.close()
