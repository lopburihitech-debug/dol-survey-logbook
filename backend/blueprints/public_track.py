"""หน้าติดตามงานสำหรับประชาชน (ไม่ต้องล็อกอิน) — public-facing, ไม่ผ่าน @login_required เหมือนหน้าอื่นๆ

ออกแบบให้ปลอดภัยพอสมควรแม้เปิดสาธารณะ เพราะเลข รว.19 (case_code) เรียงเลขต่อเนื่องต่อสำนักงาน (เช่น
LB01-2569-00001, ...00002, ...) เดาได้ไม่ยาก จึง**บังคับต้องมีเบอร์โทรผู้ขอ 4 ตัวท้ายกำกับเสมอ** เป็นปัจจัยที่สอง
และจำกัดอัตราการค้นหาต่อ IP กันการไล่ลองอัตโนมัติ (_rate_limited) — ข้อความ error ตั้งใจให้เหมือนกันทุกกรณีที่หา
ไม่เจอ (ไม่ว่าจะพิมพ์เลข รว.19 ผิด หรือเบอร์โทรผิด) เพื่อไม่ให้แยกแยะได้ว่าเลขที่กรอกมีอยู่จริงในระบบหรือไม่

ข้อมูลที่คืนให้จงใจตัดส่วนที่อ่อนไหวออกทั้งหมด: ไม่มีที่อยู่/เลขโฉนดเต็ม, ไม่มีรูปภาพ/หมุดหลักเขต, ไม่มีชื่อช่าง
รังวัดหรือเจ้าหน้าที่ที่รับผิดชอบ, ไม่มีเหตุผลการเปลี่ยนสถานะที่เจ้าหน้าที่บันทึกไว้ภายใน — คงไว้แค่รหัสเรื่อง/
ประเภทงาน/สำนักงาน/สถานะปัจจุบัน/วันสำคัญ/ไทม์ไลน์สถานะ ซึ่งเป็นข้อมูลระดับที่ผู้ขอเรื่องนั้นควรรู้ได้อยู่แล้ว
"""
import re
import time
from collections import defaultdict, deque

from flask import Blueprint, request

from constants import CaseStatus
from db import get_connection
from helpers import err, ok

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

GENERIC_NOT_FOUND = "ไม่พบข้อมูล กรุณาตรวจสอบเลข รว.19 และเบอร์โทรศัพท์ผู้ขออีกครั้ง"


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


@bp.get("/track")
def track_case():
    if _rate_limited(_client_ip()):
        return err("ค้นหาบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่อีกครั้ง", 429)

    case_code = (request.args.get("case_code") or "").strip()
    phone_last4 = _digits_only(request.args.get("phone_last4"))

    if not case_code or len(phone_last4) != 4:
        return err("กรุณากรอกเลข รว.19 และเบอร์โทรศัพท์ผู้ขอ 4 ตัวท้ายให้ครบถ้วน")

    conn = get_connection()
    try:
        case = conn.execute(
            "SELECT * FROM survey_cases WHERE case_code = ? COLLATE NOCASE",
            (case_code,),
        ).fetchone()
        if case is None:
            return err(GENERIC_NOT_FOUND, 404)
        case = dict(case)

        stored_digits = _digits_only(case.get("requester_contact"))
        if len(stored_digits) < 4 or stored_digits[-4:] != phone_last4:
            return err(GENERIC_NOT_FOUND, 404)

        survey_type = conn.execute(
            "SELECT name FROM survey_types WHERE id = ?", (case["survey_type_id"],)
        ).fetchone()
        office = conn.execute("SELECT name FROM offices WHERE id = ?", (case["office_id"],)).fetchone()

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
        result = {
            "case_code": case["case_code"],
            "requester_name": case.get("requester_name"),
            "survey_type": survey_type["name"] if survey_type else None,
            "office_name": office["name"] if office else None,
            "status": case["status"],
            "status_label": STATUS_LABELS_TH.get(case["status"], case["status"]),
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage),
            "is_cancelled": case["status"] == CaseStatus.CANCELLED,
            "received_date": case.get("received_date"),
            "appointment_date": case.get("appointment_date"),
            "due_date": case.get("due_date"),
            "timeline": timeline,
        }
        return ok(result)
    finally:
        conn.close()
