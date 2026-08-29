import csv
import io
import shutil
import sqlite3
from datetime import datetime

from flask import Blueprint, g, request

from blueprints.case_documents import UPLOAD_DIR
from constants import ALLOWED_TRANSITIONS, RESTRICTED_FROM_SURVEY_DONE, RESTRICTED_STATUS_TRANSITIONS, CaseStatus, Role
from db import get_connection
from helpers import (
    err,
    get_surveyor_profile,
    is_office_in_user_scope,
    new_id,
    now_iso,
    ok,
    record_status_change,
    scope_case_filter,
)
from security import login_required, require_roles
from services.audit import log_action
from services.case_code import generate_case_code
from services.gmaps_link import extract_coords_from_maps_url
from services.sla import add_business_days
from services.thai_date import parse_flexible_date

bp = Blueprint("survey_cases", __name__, url_prefix="/api/v1/survey-cases")

# หัวคอลัมน์ที่จำเป็นต้องมีในไฟล์ CSV นำเข้างานค้าง (ดู IMPORT_STATUS_LABELS ด้านล่างสำหรับคอลัมน์ "สถานะเริ่มต้น")
IMPORT_REQUIRED_HEADERS = ["รว.12", "ชื่อผู้ขอรังวัด", "ประเภท"]

# ป้ายภาษาไทยที่ยอมรับในคอลัมน์ "สถานะเริ่มต้น" (ไม่ระบุ/เว้นว่างได้ — ระบบจะเลือกให้อัตโนมัติจากข้อมูลแถวนั้น)
IMPORT_STATUS_LABELS = {
    "รับเรื่องแล้ว": CaseStatus.RECEIVED,
    "มอบหมายแล้ว": CaseStatus.ASSIGNED,
    "รอการรังวัด": CaseStatus.APPOINTED,
    "เลื่อนรังวัด": CaseStatus.POSTPONED,
    "งดรังวัด": CaseStatus.SURVEY_SKIPPED,
    "นัดตรวจสอบใหม่": CaseStatus.RE_APPOINTMENT_NEEDED,
    "ถอนจ่ายแล้ว": CaseStatus.CLOSED,
    "เสร็จสิ้น": CaseStatus.CLOSED,
    "ยกเลิก": CaseStatus.CANCELLED,
}


def _enrich_case(conn, case: dict) -> dict:
    parcel = conn.execute("SELECT * FROM parcels WHERE case_id = ?", (case["id"],)).fetchone()
    case["parcel"] = dict(parcel) if parcel else None

    assigned = conn.execute(
        """SELECT s.id AS surveyor_id, s.employee_code, s.nickname, s.photo_url, u.full_name
           FROM case_assignments ca
           JOIN surveyors s ON s.id = ca.surveyor_id
           JOIN users u ON u.id = s.user_id
           WHERE ca.case_id = ? AND ca.is_active = 1""",
        (case["id"],),
    ).fetchone()
    case["assigned_surveyor"] = dict(assigned) if assigned else None

    rating = conn.execute(
        "SELECT rating, comment, created_at, updated_at FROM case_satisfaction_ratings WHERE case_id = ?",
        (case["id"],),
    ).fetchone()
    case["satisfaction_rating"] = dict(rating) if rating else None

    unresolved_messages = conn.execute(
        "SELECT COUNT(*) AS c FROM complaints WHERE case_id = ? AND status = 'OPEN'", (case["id"],)
    ).fetchone()["c"]
    case["unresolved_message_count"] = unresolved_messages
    return case


@bp.get("")
@login_required
def list_cases():
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        query = f"SELECT * FROM survey_cases WHERE {where_sql}"

        status_filter = request.args.get("status")
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)

        office_id = request.args.get("office_id")
        if office_id:
            query += " AND office_id = ?"
            params.append(office_id)

        survey_type_id = request.args.get("survey_type_id")
        if survey_type_id:
            query += " AND survey_type_id = ?"
            params.append(survey_type_id)

        # ช่วงวันนัดรังวัด — ใช้กรองในหน้าปฏิทินนัดรังวัด (calendar.html) เพื่อดึงเฉพาะเรื่องของเดือนที่กำลังแสดง
        # appointment_to เป็นขอบเขตแบบ "น้อยกว่า" (ไม่รวมค่านี้) ไม่ใช่ "ถึงวันที่นี้" — ผู้เรียกต้องส่งเป็นวันแรก
        # ของเดือนถัดไป เพราะ appointment_date อาจเป็น ISO datetime เต็ม (มีเวลาต่อท้าย) เทียบ string ตรงๆ กับ
        # วันสุดท้ายของเดือนแบบ "YYYY-MM-DD" เฉยๆ จะตัดข้อมูลของวันนั้นที่มีเวลากำกับออกไปผิดพลาด
        appointment_from = request.args.get("appointment_from")
        if appointment_from:
            query += " AND appointment_date >= ?"
            params.append(appointment_from)
        appointment_to = request.args.get("appointment_to")
        if appointment_to:
            query += " AND appointment_date < ?"
            params.append(appointment_to)

        search = request.args.get("search")
        if search:
            query += " AND (case_code LIKE ? OR requester_name LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like])

        # กรองเฉพาะเรื่องที่ช่างรังวัดคนนี้กำลังรับผิดชอบอยู่ (is_active = 1 ใน case_assignments) — ใช้กรองในหน้า
        # "รายการงาน" เพื่อดูงานของช่างคนใดคนหนึ่งได้ทันที (ไม่ใช่ unassigned_only — ถ้าไม่ระบุตัวกรองนี้จะไม่กรองเลย)
        surveyor_id = request.args.get("surveyor_id")
        if surveyor_id == "unassigned":
            query += """ AND NOT EXISTS (
                SELECT 1 FROM case_assignments ca
                WHERE ca.case_id = survey_cases.id AND ca.is_active = 1
            )"""
        elif surveyor_id:
            query += """ AND EXISTS (
                SELECT 1 FROM case_assignments ca
                WHERE ca.case_id = survey_cases.id AND ca.is_active = 1 AND ca.surveyor_id = ?
            )"""
            params.append(surveyor_id)

        query += " ORDER BY received_date DESC"
        rows = conn.execute(query, params).fetchall()
        cases = [_enrich_case(conn,dict(r)) for r in rows]
        return ok(cases)
    finally:
        conn.close()


@bp.post("")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN)
def create_case():
    payload = request.get_json(silent=True) or {}
    required = ["office_id", "survey_type_id", "requester_name", "received_date"]
    if not all(payload.get(f) for f in required):
        return err(f"ต้องระบุ {', '.join(required)}")

    conn = get_connection()
    try:
        survey_type = conn.execute("SELECT * FROM survey_types WHERE id = ?", (payload["survey_type_id"],)).fetchone()
        if survey_type is None:
            return err("ไม่พบประเภทงานที่ระบุ", 404)
        if conn.execute("SELECT 1 FROM offices WHERE id = ?", (payload["office_id"],)).fetchone() is None:
            return err("ไม่พบสำนักงานที่ระบุ", 404)
        if not is_office_in_user_scope(conn, g.current_user, payload["office_id"]):
            return err("สำนักงานที่ระบุอยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        surveyor_id = payload.get("surveyor_id") or None
        if surveyor_id and conn.execute("SELECT 1 FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone() is None:
            return err("ไม่พบข้อมูลช่างรังวัดที่ระบุ", 404)

        try:
            received_date = datetime.fromisoformat(payload["received_date"])
        except ValueError:
            return err("received_date ต้องเป็นรูปแบบ ISO-8601 เช่น 2026-08-20 หรือ 2026-08-20T09:00:00")

        # เลข รว.12 — กรอกเองได้ตามเลขจริงจากแบบฟอร์มกระดาษ ถ้าไม่ระบุจะสร้างให้อัตโนมัติ (สำรองไว้สำหรับผู้เรียก API อื่น)
        # unique เฉพาะภายในสำนักงานเดียวกัน (ผูกกับ office_id) เพราะแต่ละสำนักงานออกเลขของตัวเองแยกกัน อาจซ้ำกัน
        # ข้ามสำนักงานได้ตามจริง
        case_code = (payload.get("case_code") or "").strip()
        if case_code:
            if conn.execute(
                "SELECT 1 FROM survey_cases WHERE case_code = ? AND office_id = ?",
                (case_code, payload["office_id"]),
            ).fetchone():
                return err(f"เลข รว.12 '{case_code}' มีอยู่ในระบบแล้วสำหรับสำนักงานนี้ กรุณาตรวจสอบ", 409)
        else:
            case_code = generate_case_code(conn, payload["office_id"])

        appointment_date = (payload.get("appointment_date") or "").strip() or None

        due_date = add_business_days(conn, received_date, survey_type["target_days"])

        case_id = new_id()
        ts = now_iso()
        conn.execute(
            """INSERT INTO survey_cases (id, case_code, office_id, survey_type_id, requester_name, requester_contact,
                                          received_date, due_date, appointment_date, status, priority, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case_id,
                case_code,
                payload["office_id"],
                payload["survey_type_id"],
                payload["requester_name"],
                payload.get("requester_contact"),
                received_date.isoformat(),
                due_date.isoformat(),
                appointment_date,
                CaseStatus.RECEIVED,
                payload.get("priority", "normal"),
                g.current_user["id"],
                ts,
                ts,
            ),
        )
        conn.execute(
            """INSERT INTO case_status_history (id, case_id, previous_status, new_status, changed_by, reason, changed_at)
               VALUES (?, ?, NULL, ?, ?, NULL, ?)""",
            (new_id(), case_id, CaseStatus.RECEIVED, g.current_user["id"], ts),
        )

        parcel = payload.get("parcel")
        if parcel:
            conn.execute(
                """INSERT INTO parcels (id, case_id, deed_no, parcel_no, survey_sheet_no, sub_district, district,
                                         province, area_rai, area_ngan, area_wa, lat, lng, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    case_id,
                    parcel.get("deed_no"),
                    parcel.get("parcel_no"),
                    parcel.get("survey_sheet_no"),
                    parcel.get("sub_district"),
                    parcel.get("district"),
                    parcel.get("province"),
                    parcel.get("area_rai"),
                    parcel.get("area_ngan"),
                    parcel.get("area_wa"),
                    parcel.get("lat"),
                    parcel.get("lng"),
                    ts,
                    ts,
                ),
            )

        if surveyor_id:
            conn.execute(
                """INSERT INTO case_assignments (id, case_id, surveyor_id, assigned_by, assigned_at, is_active)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (new_id(), case_id, surveyor_id, g.current_user["id"], ts),
            )
            record_status_change(conn, case_id, CaseStatus.RECEIVED, CaseStatus.ASSIGNED, g.current_user["id"], "มอบหมายตอนรับเรื่อง")

        conn.commit()
        log_action(conn, g.current_user["id"], "CREATE", "survey_cases", case_id, after={"case_code": case_code})

        row = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        return ok(_enrich_case(conn,dict(row)), 201)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# นำเข้างานค้างจากไฟล์ CSV (เช่น export จาก Google Sheets ของช่างรังวัดแต่ละคน)
# ---------------------------------------------------------------------------
def _decode_csv_bytes(raw: bytes) -> str:
    """ไฟล์ CSV ที่ export จาก Google Sheets/Excel อาจเป็น utf-8-sig (มี BOM) หรือบางเครื่อง Windows
    ที่ตั้งค่าภาษาไทยแบบเก่าอาจ export เป็น cp874 (Thai ANSI) แทน — ลองไล่ทีละแบบแทนบังคับ utf-8 อย่างเดียว"""
    for encoding in ("utf-8-sig", "utf-8", "cp874"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("อ่านไฟล์ไม่ได้ กรุณาบันทึกไฟล์เป็น CSV UTF-8 แล้วลองใหม่")


@bp.post("/import")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN)
def import_cases():
    """นำเข้างานค้างจำนวนมากจากไฟล์ CSV ครั้งเดียว (เช่น backlog เดิมของช่างแต่ละคนที่เก็บใน Google Sheets)
    แทนการเพิ่มทีละเรื่องผ่านฟอร์ม — ตรวจสอบข้อมูลทุกแถวก่อน (pass 1) แล้วค่อยบันทึกจริงเป็นก้อนเดียว (pass 2)
    เพื่อไม่ให้ error ของบางแถวทำให้แถวอื่นที่ถูกต้องอยู่แล้วถูกยกเลิกไปด้วย (แถวที่ผิดจะถูกข้าม ไม่ทำให้ทั้งไฟล์ล้มเหลว)"""
    file = request.files.get("file")
    if not file or not file.filename:
        return err("ต้องแนบไฟล์ CSV (field name: file)")

    office_id = (request.form.get("office_id") or "").strip()
    surveyor_id = (request.form.get("surveyor_id") or "").strip() or None
    if not office_id:
        return err("ต้องระบุสำนักงาน")

    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM offices WHERE id = ?", (office_id,)).fetchone() is None:
            return err("ไม่พบสำนักงานที่ระบุ", 404)
        if not is_office_in_user_scope(conn, g.current_user, office_id):
            return err("สำนักงานที่ระบุอยู่นอกเขตจังหวัดที่ท่านดูแล", 403)
        surveyor = None
        if surveyor_id:
            surveyor = conn.execute("SELECT * FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone()
            if surveyor is None:
                return err("ไม่พบข้อมูลช่างรังวัดที่ระบุ", 404)

        try:
            text = _decode_csv_bytes(file.stream.read())
        except ValueError as exc:
            return err(str(exc))

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return err("ไฟล์ไม่มีแถวหัวตาราง หรือไม่มีข้อมูล")
        missing_headers = [h for h in IMPORT_REQUIRED_HEADERS if h not in reader.fieldnames]
        if missing_headers:
            return err(f"ไฟล์ขาดคอลัมน์ที่จำเป็น: {', '.join(missing_headers)} — ดาวน์โหลดเทมเพลตแล้วเทียบหัวคอลัมน์อีกครั้ง")

        survey_type_rows = conn.execute("SELECT id, name FROM survey_types WHERE is_active = 1").fetchall()
        survey_type_by_name = {t["name"].strip(): t["id"] for t in survey_type_rows}

        skipped = []
        parsed = []
        seen_codes = set()

        for line_no, row in enumerate(reader, start=2):
            def cell(header):
                return (row.get(header) or "").strip()

            case_code = cell("รว.12")
            if not case_code:
                skipped.append({"row": line_no, "reason": "ไม่มีเลข รว.12"})
                continue
            if case_code in seen_codes:
                skipped.append({"row": line_no, "case_code": case_code, "reason": "เลข รว.12 นี้ซ้ำกันในไฟล์เดียวกัน"})
                continue
            # unique เฉพาะภายในสำนักงานเดียวกัน — office_id ของทั้งไฟล์นี้ถูกเลือกไว้แล้วครั้งเดียวตอนนำเข้า
            if conn.execute(
                "SELECT 1 FROM survey_cases WHERE case_code = ? AND office_id = ?", (case_code, office_id)
            ).fetchone():
                skipped.append({"row": line_no, "case_code": case_code, "reason": "เลข รว.12 นี้มีอยู่ในระบบแล้วสำหรับสำนักงานนี้"})
                continue

            requester_name = cell("ชื่อผู้ขอรังวัด")
            if not requester_name:
                skipped.append({"row": line_no, "case_code": case_code, "reason": "ไม่มีชื่อผู้ขอรังวัด"})
                continue

            type_name = cell("ประเภท")
            survey_type_id = survey_type_by_name.get(type_name)
            if not survey_type_id:
                skipped.append({"row": line_no, "case_code": case_code, "reason": f"ไม่พบประเภทงาน '{type_name}' ในระบบ (ชื่อต้องตรงกับที่ตั้งไว้ในระบบเป๊ะๆ)"})
                continue

            try:
                appointment_date = parse_flexible_date(cell("วันที่นัดรังวัด"))
                received_date = parse_flexible_date(cell("วันที่รับเรื่อง")) or appointment_date
            except ValueError as exc:
                skipped.append({"row": line_no, "case_code": case_code, "reason": str(exc)})
                continue
            if not received_date:
                skipped.append({"row": line_no, "case_code": case_code, "reason": "ต้องระบุ วันที่รับเรื่อง หรือ วันที่นัดรังวัด อย่างน้อยหนึ่งอย่าง"})
                continue

            status_label = cell("สถานะเริ่มต้น")
            if status_label:
                target_status = IMPORT_STATUS_LABELS.get(status_label)
                if target_status is None:
                    valid = ", ".join(IMPORT_STATUS_LABELS.keys())
                    skipped.append({"row": line_no, "case_code": case_code, "reason": f"ไม่รู้จักสถานะ '{status_label}' (ต้องเป็นหนึ่งใน: {valid} หรือเว้นว่างไว้)"})
                    continue
            elif surveyor_id:
                target_status = CaseStatus.APPOINTED if appointment_date else CaseStatus.ASSIGNED
            else:
                # ยังไม่ได้เลือกช่างรังวัดรับผิดชอบตอนนำเข้า — ถ้ามีวันนัดอยู่แล้วให้ขึ้นเป็น "รอมอบหมาย" (จะได้ไป
                # มอบหมายช่างทีหลังจากหน้ารายการงานได้ทันที) ไม่ปั้นเป็น "รอการรังวัด" ทั้งที่ยังไม่มีคนรับผิดชอบ
                target_status = CaseStatus.WAITING_ASSIGNMENT if appointment_date else CaseStatus.RECEIVED

            seen_codes.add(case_code)
            parsed.append(
                {
                    "case_code": case_code,
                    "requester_name": requester_name,
                    "requester_contact": cell("เบอร์ติดต่อ/หมายเหตุ") or None,
                    "survey_type_id": survey_type_id,
                    "received_date": received_date,
                    "appointment_date": appointment_date,
                    "target_status": target_status,
                }
            )

        if not parsed:
            return ok({"imported": 0, "skipped": skipped})

        # ต้องใช้ target_days จริงของแต่ละประเภทงานตอนคำนวณ due_date — ดึงแยกเป็น map เพราะ query ด้านบนเลือกแค่ id/name
        target_days_map = {
            t["id"]: t["target_days"]
            for t in conn.execute("SELECT id, target_days FROM survey_types WHERE is_active = 1").fetchall()
        }

        imported_codes = []
        ts = now_iso()
        for item in parsed:
            case_id = new_id()
            received_dt = datetime.fromisoformat(item["received_date"])
            due_date = add_business_days(conn, received_dt, target_days_map.get(item["survey_type_id"], 30))
            conn.execute(
                """INSERT INTO survey_cases (id, case_code, office_id, survey_type_id, requester_name, requester_contact,
                                              received_date, due_date, appointment_date, status, priority, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?)""",
                (
                    case_id,
                    item["case_code"],
                    office_id,
                    item["survey_type_id"],
                    item["requester_name"],
                    item["requester_contact"],
                    received_dt.isoformat(),
                    due_date.isoformat(),
                    item["appointment_date"],
                    CaseStatus.RECEIVED,
                    g.current_user["id"],
                    ts,
                    ts,
                ),
            )
            conn.execute(
                """INSERT INTO case_status_history (id, case_id, previous_status, new_status, changed_by, reason, changed_at)
                   VALUES (?, ?, NULL, ?, ?, ?, ?)""",
                (new_id(), case_id, CaseStatus.RECEIVED, g.current_user["id"], "นำเข้าข้อมูลเดิม", ts),
            )

            current_status = CaseStatus.RECEIVED
            if surveyor_id:
                conn.execute(
                    """INSERT INTO case_assignments (id, case_id, surveyor_id, assigned_by, assigned_at, is_active)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (new_id(), case_id, surveyor_id, g.current_user["id"], ts),
                )
                if item["target_status"] != CaseStatus.RECEIVED:
                    record_status_change(conn, case_id, current_status, CaseStatus.ASSIGNED, g.current_user["id"], "นำเข้าข้อมูลเดิม")
                    current_status = CaseStatus.ASSIGNED

            if item["target_status"] != current_status:
                record_status_change(conn, case_id, current_status, item["target_status"], g.current_user["id"], "นำเข้าข้อมูลเดิม")

            imported_codes.append(item["case_code"])

        conn.commit()
        log_action(
            conn,
            g.current_user["id"],
            "IMPORT",
            "survey_cases",
            None,
            after={"office_id": office_id, "surveyor_id": surveyor_id, "imported_count": len(imported_codes)},
        )

        return ok({"imported": len(imported_codes), "imported_codes": imported_codes, "skipped": skipped}, 201)
    finally:
        conn.close()


@bp.get("/<case_id>")
@login_required
def get_case(case_id):
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        row = conn.execute(f"SELECT * FROM survey_cases WHERE id = ? AND {where_sql}", [case_id] + params).fetchone()
        if row is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)
        return ok(_enrich_case(conn,dict(row)))
    finally:
        conn.close()


# แก้ไขได้ "ทุกอย่าง" ของตัวเรื่อง ยกเว้นฟิลด์ที่มีช่องทางแก้ไขเฉพาะของตัวเองอยู่แล้ว (status ต้องผ่าน state machine
# ที่ /status, เช็คลิสต์ภาคสนามต้องผ่าน /checklist) และฟิลด์ที่ระบบดูแลเอง (id, created_by, created_at, updated_at,
# citizen_id — ผูกจากระบบติดตามของประชาชน ไม่ใช่ข้อมูลที่แก้ตรงๆ ในแบบฟอร์มนี้)
UPDATABLE_CASE_FIELDS = {
    "case_code",
    "office_id",
    "survey_type_id",
    "requester_name",
    "requester_contact",
    "received_date",
    "due_date",
    "appointment_date",
    "priority",
}


@bp.patch("/<case_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN)
def update_case(case_id):
    payload = request.get_json(silent=True) or {}
    fields = {k: v for k, v in payload.items() if k in UPDATABLE_CASE_FIELDS}
    if not fields:
        return err("ไม่มีข้อมูลที่จะอัปเดต")

    conn = get_connection()
    try:
        existing_case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if existing_case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if not is_office_in_user_scope(conn, g.current_user, existing_case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        if "office_id" in fields:
            office = conn.execute("SELECT id FROM offices WHERE id = ?", (fields["office_id"],)).fetchone()
            if office is None:
                return err("ไม่พบสำนักงานที่ระบุ")
            # ต้องอยู่ในขอบเขตที่ผู้ใช้ดูแลด้วย ไม่ใช่แค่สำนักงานเดิมของเรื่อง — กัน province_admin ย้ายเรื่องออก
            # นอกจังหวัดตัวเอง (system_admin/administrator ไม่มีขอบเขตอยู่แล้ว ผ่านเงื่อนไขนี้เสมอ)
            if not is_office_in_user_scope(conn, g.current_user, fields["office_id"]):
                return err("ไม่มีสิทธิ์ย้ายเรื่องไปยังสำนักงานนอกเขตที่ท่านดูแล", 403)

        if "case_code" in fields:
            new_code = (fields["case_code"] or "").strip()
            if not new_code:
                return err("เลข รว.12 ห้ามเว้นว่าง")
            # unique เฉพาะภายในสำนักงานเดียวกัน — ใช้สำนักงานใหม่ถ้ากำลังย้ายสำนักงานในคำขอเดียวกันนี้ด้วย
            # ไม่งั้นใช้สำนักงานเดิมของเรื่อง
            target_office_id = fields.get("office_id", existing_case["office_id"])
            dup = conn.execute(
                "SELECT 1 FROM survey_cases WHERE case_code = ? AND office_id = ? AND id != ?",
                (new_code, target_office_id, case_id),
            ).fetchone()
            if dup:
                return err(f"เลข รว.12 '{new_code}' ถูกใช้กับเรื่องอื่นอยู่แล้วในสำนักงานนี้")
            fields["case_code"] = new_code

        if "survey_type_id" in fields:
            st = conn.execute("SELECT id FROM survey_types WHERE id = ?", (fields["survey_type_id"],)).fetchone()
            if st is None:
                return err("ไม่พบประเภทงานที่ระบุ")

        before_snapshot = {k: existing_case[k] for k in fields if k in existing_case.keys()}
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        try:
            conn.execute(f"UPDATE survey_cases SET {set_clause}, updated_at = ? WHERE id = ?", list(fields.values()) + [now_iso(), case_id])
        except sqlite3.IntegrityError:
            return err("บันทึกไม่สำเร็จ — ข้อมูลที่กรอกซ้ำกับเรื่องอื่น (เช่น เลข รว.12)")
        log_action(conn, g.current_user["id"], "UPDATE", "survey_cases", case_id, before=before_snapshot, after=fields)
        conn.commit()
        row = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        return ok(_enrich_case(conn,dict(row)))
    finally:
        conn.close()


@bp.delete("/<case_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.PROVINCE_ADMIN)
def delete_case(case_id):
    """ลบเรื่อง (รว.12) แบบถาวร — ลบข้อมูลที่เกี่ยวข้องทั้งหมด (มอบหมาย/นัดหมาย/รูปภาพ-หมุด/ประวัติสถานะ/ตรวจสอบ/
    แก้ไขซ้ำ/ร้องเรียน/ค่าธรรมเนียม/แจ้งเตือน/เบิกอุปกรณ์) รวมถึงไฟล์ที่อัปโหลดไว้บนดิสก์ ทำคืนไม่ได้ — เก็บ snapshot
    ก่อนลบไว้ใน audit_logs (before_data) เพื่อการตรวจสอบย้อนหลังเท่านั้น ไม่ใช่การกู้คืนอัตโนมัติ"""
    conn = get_connection()
    try:
        existing_case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if existing_case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if not is_office_in_user_scope(conn, g.current_user, existing_case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        before_snapshot = dict(existing_case)

        for table in (
            "parcels",
            "case_assignments",
            "appointments",
            "case_documents",
            "case_neighbors",
            "case_status_history",
            "case_reviews",
            "rework_requests",
            "complaints",
            "case_satisfaction_ratings",
            "fees",
            "notifications",
            "equipment_assignments",
        ):
            conn.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))
        conn.execute("DELETE FROM survey_cases WHERE id = ?", (case_id,))
        log_action(conn, g.current_user["id"], "DELETE", "survey_cases", case_id, before=before_snapshot)
        conn.commit()

        # ลบไฟล์แนบ (รูปภาพ/แผนที่หมุด) ที่เก็บไว้บนดิสก์ — ทำหลังลบข้อมูลในฐานข้อมูลสำเร็จแล้ว ถ้าลบไฟล์จริงไม่สำเร็จ
        # ก็ไม่ต้อง fail ทั้ง request เพราะข้อมูลหลักถูกลบแล้ว (ไฟล์ค้างเป็นเพียง orphan บนดิสก์)
        try:
            shutil.rmtree(UPLOAD_DIR / case_id, ignore_errors=True)
        except Exception:
            pass

        return ok({"deleted": True})
    finally:
        conn.close()


CHECKLIST_FIELDS = {
    "survey_result": {"DONE", "NOT_DONE"},
    "mapping_status": {"DONE", "NOT_DONE"},
    "neighbor_status": {"COMPLETE", "WAITING_AGENCY"},
    "announcement_status": {"POSTED", "WAITING"},
}


@bp.patch("/<case_id>/checklist")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.SURVEYOR)
def update_checklist(case_id):
    payload = request.get_json(silent=True) or {}
    fields = {}
    for key, allowed_values in CHECKLIST_FIELDS.items():
        if key in payload:
            if payload[key] not in allowed_values:
                return err(f"{key} ต้องเป็นหนึ่งใน {sorted(allowed_values)}")
            fields[key] = payload[key]
    if not fields:
        return err("ไม่มีข้อมูลเช็คลิสต์ที่จะอัปเดต")

    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        visible = conn.execute(f"SELECT 1 FROM survey_cases WHERE id = ? AND {where_sql}", [case_id] + params).fetchone()
        if visible is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE survey_cases SET {set_clause}, updated_at = ? WHERE id = ?", list(fields.values()) + [now_iso(), case_id])
        conn.commit()
        row = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        return ok(_enrich_case(conn, dict(row)))
    finally:
        conn.close()


@bp.get("/<case_id>/history")
@login_required
def get_history(case_id):
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        visible = conn.execute(f"SELECT 1 FROM survey_cases WHERE id = ? AND {where_sql}", [case_id] + params).fetchone()
        if visible is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)

        rows = conn.execute(
            """SELECT h.*, u.full_name AS changed_by_name FROM case_status_history h
               LEFT JOIN users u ON u.id = h.changed_by
               WHERE h.case_id = ? ORDER BY h.changed_at""",
            (case_id,),
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


PARCEL_FIELDS = [
    "deed_no", "parcel_no", "survey_sheet_no", "sub_district", "district", "province", "area_rai", "area_ngan", "area_wa", "lat", "lng", "location_url",
]


@bp.patch("/<case_id>/parcel")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.SURVEYOR)
def upsert_parcel(case_id):
    payload = request.get_json(silent=True) or {}
    conn = get_connection()
    try:
        case_row = conn.execute("SELECT office_id FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if case_row is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if g.current_user["role"] == Role.PROVINCE_ADMIN and not is_office_in_user_scope(conn, g.current_user, case_row["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        existing = conn.execute("SELECT * FROM parcels WHERE case_id = ?", (case_id,)).fetchone()
        ts = now_iso()

        # ถ้าผู้ใช้วาง/แก้ไขลิงก์แผนที่ (location_url) ไว้ และไม่ได้กรอกพิกัด lat/lng เองตรงๆ ในคำขอนี้ ลองแยกพิกัด
        # จากลิงก์ให้อัตโนมัติ (ดู services/gmaps_link.py) เพื่อให้เรื่องนี้ไปปรากฏบนหน้าแผนที่ช่างรังวัด
        # (field-map.html) ได้โดยไม่ต้องพึ่ง Shapefile — ทำเฉพาะตอนลิงก์เปลี่ยนไปจากเดิมจริงๆ หรือยังไม่เคยดึงพิกัด
        # ได้เลย (กันการยิง request ไปข้างนอกซ้ำโดยไม่จำเป็นทุกครั้งที่บันทึกฟอร์มนี้ทั้งที่ลิงก์เดิม)
        location_url = payload.get("location_url")
        if location_url and "lat" not in payload and "lng" not in payload:
            existing_url = existing["location_url"] if existing else None
            existing_lat = existing["lat"] if existing else None
            if location_url != existing_url or existing_lat is None:
                derived_lat, derived_lng = extract_coords_from_maps_url(location_url)
                if derived_lat is not None:
                    payload = {**payload, "lat": derived_lat, "lng": derived_lng}

        if existing is None:
            values = [payload.get(f) for f in PARCEL_FIELDS]
            conn.execute(
                f"""INSERT INTO parcels (id, case_id, {', '.join(PARCEL_FIELDS)}, created_at, updated_at)
                    VALUES (?, ?, {', '.join(['?'] * len(PARCEL_FIELDS))}, ?, ?)""",
                [new_id(), case_id] + values + [ts, ts],
            )
        else:
            fields = {k: v for k, v in payload.items() if k in PARCEL_FIELDS}
            if fields:
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE parcels SET {set_clause}, updated_at = ? WHERE case_id = ?",
                    list(fields.values()) + [ts, case_id],
                )
        conn.commit()
        row = conn.execute("SELECT * FROM parcels WHERE case_id = ?", (case_id,)).fetchone()
        return ok(dict(row))
    finally:
        conn.close()


# จำกัดจำนวนแปลงที่ประมวลผลต่อคำขอ — backend/entrypoint.sh ตั้ง gunicorn --timeout 60 วินาที และการตามลิงก์แบบย่อ
# (maps.app.goo.gl) ไปดู URL ปลายทางจริงแต่ละอันอาจใช้เวลาได้ถึง ~4 วินาที (ดู services/gmaps_link.py) ถ้าประมวลผล
# ทีเดียวหมดในคำขอเดียวอาจทำให้ worker ถูกฆ่าก่อนตอบกลับ จึงให้ฝั่ง frontend เรียกซ้ำเป็นชุดๆ จนกว่าจะครบแทน
_BACKFILL_BATCH_SIZE = 15


@bp.post("/parcels/backfill-coords")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR)
def backfill_parcel_coords():
    """ไล่ดึงพิกัด (lat/lng) จากลิงก์แผนที่ (location_url) ย้อนหลัง ให้กับแปลงที่ดินซึ่งเคยกรอกลิงก์ไว้ก่อนที่ระบบจะมี
    ฟีเจอร์แยกพิกัดอัตโนมัติ (upsert_parcel ด้านบน) — แปลงเหล่านี้จะไม่มีวันได้พิกัดเองเพราะการแยกพิกัดอัตโนมัติทำงาน
    เฉพาะตอนบันทึกฟอร์มแปลงใหม่หรือแก้ไขลิงก์เท่านั้น ไม่ได้ไล่ย้อนหลังให้เอง เอนด์พอยต์นี้จึงเปิดให้ผู้ดูแลระบบกดสั่ง
    ประมวลผลย้อนหลังได้เอง (ดูปุ่มในหน้าแผนที่ช่างรังวัด field-map.html)

    ประมวลผลทีละชุดเล็ก (ดู _BACKFILL_BATCH_SIZE ด้านบน) แล้วคืนจำนวนที่เหลือ (remaining) ให้ฝั่ง frontend เรียกซ้ำวน
    จนกว่าจะครบ — หมายเหตุ: แปลงที่ไม่เคยกรอกลิงก์แผนที่ไว้เลย (location_url ว่างเปล่า) จะไม่อยู่ในรายการนี้ตั้งแต่แรก
    เพราะไม่มีอะไรให้ดึง ต้องให้ช่างรังวัดกรอกลิงก์เพิ่มในหน้ารายละเอียดเรื่องก่อนจึงจะปักหมุดได้"""
    conn = get_connection()
    try:
        candidates = conn.execute(
            """SELECT id, location_url FROM parcels
               WHERE location_url IS NOT NULL AND location_url != ''
                 AND (lat IS NULL OR lng IS NULL)
               ORDER BY updated_at
               LIMIT ?""",
            (_BACKFILL_BATCH_SIZE,),
        ).fetchall()

        updated = 0
        ts = now_iso()
        for row in candidates:
            derived_lat, derived_lng = extract_coords_from_maps_url(row["location_url"])
            if derived_lat is not None:
                conn.execute(
                    "UPDATE parcels SET lat = ?, lng = ?, updated_at = ? WHERE id = ?",
                    (derived_lat, derived_lng, ts, row["id"]),
                )
                updated += 1
        conn.commit()

        remaining = conn.execute(
            """SELECT COUNT(*) AS c FROM parcels
               WHERE location_url IS NOT NULL AND location_url != ''
                 AND (lat IS NULL OR lng IS NULL)"""
        ).fetchone()["c"]

        return ok(
            {
                "processed": len(candidates),
                "updated": updated,
                "failed": len(candidates) - updated,
                "remaining": remaining,
            }
        )
    finally:
        conn.close()


@bp.patch("/<case_id>/status")
@login_required
def update_status(case_id):
    payload = request.get_json(silent=True) or {}
    new_status = payload.get("new_status")
    reason = payload.get("reason")
    if not new_status:
        return err("ต้องระบุ new_status")

    conn = get_connection()
    try:
        case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)

        if g.current_user["role"] == Role.SURVEYOR:
            surveyor = get_surveyor_profile(conn, g.current_user["id"])
            owns_case = surveyor and conn.execute(
                "SELECT 1 FROM case_assignments WHERE case_id = ? AND surveyor_id = ? AND is_active = 1",
                (case_id, surveyor["id"]),
            ).fetchone()
            if not owns_case:
                return err("ไม่มีสิทธิ์แก้ไขเรื่องนี้", 403)

        if g.current_user["role"] == Role.PROVINCE_ADMIN and not is_office_in_user_scope(conn, g.current_user, case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        if new_status == CaseStatus.COMPLETED:
            return err("ไม่รองรับการตั้งสถานะนี้โดยตรงผ่าน endpoint นี้")

        if new_status in RESTRICTED_STATUS_TRANSITIONS and g.current_user["role"] not in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN):
            return err("เปลี่ยนเป็นสถานะนี้ได้เฉพาะผู้บริหารหรือผู้ดูแลระบบเท่านั้น", 403)

        # เปิดเรื่องที่ "ถอนจ่ายแล้ว" (CLOSED) กลับมาแก้ไข/รังวัดซ้ำ ถือเป็นการย้อนขั้นตอนสุดท้ายของงาน
        # จำกัดสิทธิ์เฉพาะผู้บริหาร/ผู้ดูแลระบบเช่นเดียวกับตอนปิดเรื่อง (ไม่ผูกกับ RESTRICTED_STATUS_TRANSITIONS
        # เพราะตัวนั้น gate ตาม new_status ไม่ใช่ status ปัจจุบัน)
        if case["status"] == CaseStatus.CLOSED and g.current_user["role"] not in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN):
            return err("เปิดเรื่องที่ถอนจ่ายแล้วกลับมาแก้ไขได้เฉพาะผู้บริหารหรือผู้ดูแลระบบเท่านั้น", 403)

        # จากสถานะ "รังวัดเสร็จแล้ว" (SURVEY_DONE) เปลี่ยนเป็น "งดรังวัด"/"นัดตรวจสอบใหม่" ถือเป็นการย้อนกลับว่า
        # จริงๆ แล้วรังวัดยังไม่เสร็จ — จำกัดสิทธิ์เฉพาะผู้บริหาร/ผู้ดูแลระบบตั้งแต่ระดับจังหวัดขึ้นไปเท่านั้น
        # (การเปลี่ยนเป็นสถานะเดียวกันนี้จากสถานะอื่น เช่น APPOINTED/REWORK_REQUIRED ยังไม่จำกัดสิทธิ์เหมือนเดิม)
        if (
            case["status"] == CaseStatus.SURVEY_DONE
            and new_status in RESTRICTED_FROM_SURVEY_DONE
            and g.current_user["role"] not in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN)
        ):
            return err("เปลี่ยนสถานะนี้จากขั้นตอน 'รังวัดเสร็จแล้ว' ได้เฉพาะผู้บริหารหรือผู้ดูแลระบบเท่านั้น", 403)

        allowed = ALLOWED_TRANSITIONS.get(case["status"], set())
        if new_status not in allowed:
            return err(f"ไม่สามารถเปลี่ยนสถานะจาก {case['status']} เป็น {new_status} ได้")

        record_status_change(conn, case_id, case["status"], new_status, g.current_user["id"], reason)
        conn.commit()
        row = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        return ok(_enrich_case(conn,dict(row)))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Assignment / Reassignment
# ---------------------------------------------------------------------------
@bp.post("/<case_id>/assignments")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN)
def assign_surveyor(case_id):
    payload = request.get_json(silent=True) or {}
    surveyor_id = payload.get("surveyor_id")
    if not surveyor_id:
        return err("ต้องระบุ surveyor_id")

    conn = get_connection()
    try:
        case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if not is_office_in_user_scope(conn, g.current_user, case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)
        if case["status"] not in (CaseStatus.RECEIVED, CaseStatus.WAITING_ASSIGNMENT):
            return err("มอบหมายได้เฉพาะเรื่องที่ยังไม่ได้มอบหมาย")
        surveyor_row = conn.execute("SELECT office_id FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone()
        if surveyor_row is None:
            return err("ไม่พบข้อมูลช่างรังวัด", 404)
        if not is_office_in_user_scope(conn, g.current_user, surveyor_row["office_id"]):
            return err("ช่างรังวัดที่เลือกอยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        assignment_id = new_id()
        ts = now_iso()
        conn.execute(
            """INSERT INTO case_assignments (id, case_id, surveyor_id, assigned_by, assigned_at, is_active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (assignment_id, case_id, surveyor_id, g.current_user["id"], ts),
        )
        record_status_change(conn, case_id, case["status"], CaseStatus.ASSIGNED, g.current_user["id"])
        conn.commit()
        log_action(conn, g.current_user["id"], "CREATE", "case_assignments", assignment_id, after={"surveyor_id": surveyor_id})

        row = conn.execute("SELECT * FROM case_assignments WHERE id = ?", (assignment_id,)).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()


@bp.post("/<case_id>/reassign")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN)
def reassign_surveyor(case_id):
    """เปลี่ยน/ย้ายช่างรังวัดที่รับผิดชอบ — ใช้ได้ทั้งกรณีมอบหมายครั้งแรก (ยังไม่มีช่างรับผิดชอบ)
    และกรณีเปลี่ยนช่างระหว่างทาง ประวัติช่างคนก่อนหน้ายังคงอยู่ในตาราง case_assignments (is_active = 0)
    ให้ดูย้อนหลังได้เสมอผ่าน GET /<case_id>/assignments"""
    payload = request.get_json(silent=True) or {}
    new_surveyor_id = payload.get("new_surveyor_id")
    reason = payload.get("reason")
    if not new_surveyor_id or not reason:
        return err("ต้องระบุ new_surveyor_id และ reason")

    conn = get_connection()
    try:
        case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if not is_office_in_user_scope(conn, g.current_user, case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)
        new_surveyor_row = conn.execute("SELECT office_id FROM surveyors WHERE id = ?", (new_surveyor_id,)).fetchone()
        if new_surveyor_row is None:
            return err("ไม่พบข้อมูลช่างรังวัดที่ระบุ", 404)
        if not is_office_in_user_scope(conn, g.current_user, new_surveyor_row["office_id"]):
            return err("ช่างรังวัดที่เลือกอยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        ts = now_iso()
        current = conn.execute(
            "SELECT * FROM case_assignments WHERE case_id = ? AND is_active = 1", (case_id,)
        ).fetchone()
        if current:
            if current["surveyor_id"] == new_surveyor_id:
                return err("ช่างรังวัดที่เลือกเป็นคนที่รับผิดชอบอยู่แล้ว")
            # หมายเหตุ: ไม่เขียนทับ reason เดิมของรายการที่ปิดใช้งาน (นั่นคือเหตุผลตอนมอบหมายครั้งนั้น)
            # ส่วนเหตุผลของการเปลี่ยนแปลงครั้งนี้จะถูกบันทึกไว้ที่รายการใหม่ (reason ที่ส่งมาใน request) แทน
            conn.execute(
                "UPDATE case_assignments SET is_active = 0, unassigned_at = ? WHERE id = ?",
                (ts, current["id"]),
            )

        assignment_id = new_id()
        conn.execute(
            """INSERT INTO case_assignments (id, case_id, surveyor_id, assigned_by, assigned_at, reason, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (assignment_id, case_id, new_surveyor_id, g.current_user["id"], ts, reason),
        )

        # ถ้าเรื่องยังไม่เคยถูกมอบหมายมาก่อน (สร้างไว้เฉยๆ) ให้เลื่อนสถานะไปมอบหมายแล้วด้วยในตัว
        if current is None and case["status"] in (CaseStatus.RECEIVED, CaseStatus.WAITING_ASSIGNMENT):
            record_status_change(conn, case_id, case["status"], CaseStatus.ASSIGNED, g.current_user["id"], reason)

        conn.commit()
        log_action(conn, g.current_user["id"], "UPDATE", "case_assignments", assignment_id, after={"surveyor_id": new_surveyor_id, "reason": reason})

        row = conn.execute("SELECT * FROM case_assignments WHERE id = ?", (assignment_id,)).fetchone()
        return ok(dict(row))
    finally:
        conn.close()


@bp.get("/<case_id>/assignments")
@login_required
def list_assignments(case_id):
    """ประวัติช่างรังวัดที่เคยรับผิดชอบเรื่องนี้ทั้งหมด (รวมคนปัจจุบัน) เรียงจากล่าสุดไปเก่าสุด"""
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        visible = conn.execute(f"SELECT 1 FROM survey_cases WHERE id = ? AND {where_sql}", [case_id] + params).fetchone()
        if visible is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)

        rows = conn.execute(
            """SELECT ca.*, s.employee_code, s.nickname, u.full_name AS surveyor_name,
                      ab.full_name AS assigned_by_name
               FROM case_assignments ca
               JOIN surveyors s ON s.id = ca.surveyor_id
               JOIN users u ON u.id = s.user_id
               LEFT JOIN users ab ON ab.id = ca.assigned_by
               WHERE ca.case_id = ?
               ORDER BY ca.assigned_at DESC""",
            (case_id,),
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ข้อความติดตาม/สอบถามจากประชาชน (ส่งเข้ามาจากหน้าสาธารณะ frontend/track.html ผ่าน
# POST /api/v1/public/track/message — ดู blueprints/public_track.py) ใช้ตาราง complaints ร่วมกับข้อร้องเรียน
# ทั่วไป (complaint_type แยกชนิด: "INQUIRY" = ข้อความสอบถาม/ติดตามเรื่องจากหน้านี้)
# ---------------------------------------------------------------------------
@bp.get("/<case_id>/messages")
@login_required
def list_case_messages(case_id):
    """ข้อความ/ข้อร้องเรียนทั้งหมดของเรื่องนี้ เรียงจากล่าสุดไปเก่าสุด — ทุกบทบาทที่มองเห็นเรื่องนี้ดูได้"""
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        visible = conn.execute(f"SELECT 1 FROM survey_cases WHERE id = ? AND {where_sql}", [case_id] + params).fetchone()
        if visible is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)

        rows = conn.execute(
            """SELECT c.*, u.full_name AS resolved_by_name, r.full_name AS replied_by_name
               FROM complaints c
               LEFT JOIN users u ON u.id = c.resolved_by
               LEFT JOIN users r ON r.id = c.replied_by
               WHERE c.case_id = ?
               ORDER BY c.created_at DESC""",
            (case_id,),
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


def _check_message_access(conn, case_id: str) -> bool:
    """เช็คว่า current_user แตะข้อความของเรื่องนี้ได้ไหม — ใช้ร่วมกันทั้ง resolve และ reply โดยแยกเงื่อนไข
    ช่างรังวัด (ต้องเป็นผู้รับมอบหมายเรื่องนี้อยู่จริง ผ่าน case_assignments) ออกจากบทบาทอื่นๆ (ใช้ is_office_in_user_scope
    ตามขอบเขตสำนักงาน/จังหวัดตามปกติ) — ดูรูปแบบเดียวกันใน update_status() ด้านบน"""
    case = conn.execute("SELECT office_id FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        return False
    if g.current_user["role"] == Role.SURVEYOR:
        surveyor = get_surveyor_profile(conn, g.current_user["id"])
        owns_case = surveyor and conn.execute(
            "SELECT 1 FROM case_assignments WHERE case_id = ? AND surveyor_id = ? AND is_active = 1",
            (case_id, surveyor["id"]),
        ).fetchone()
        return bool(owns_case)
    return is_office_in_user_scope(conn, g.current_user, case["office_id"])


@bp.patch("/messages/<message_id>/resolve")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN)
def resolve_case_message(message_id):
    """ทำเครื่องหมายว่าดำเนินการแล้วโดยไม่ตอบกลับเป็นข้อความ (เช่น ติดต่อกลับทางโทรศัพท์แล้ว หรือข้อความซ้ำ/ไม่ต้องตอบ)
    — ถ้าต้องการพิมพ์คำตอบให้ประชาชนเห็นในหน้าติดตามงานด้วย ใช้ /messages/<id>/reply แทน (จะปิดเรื่องให้อัตโนมัติ)"""
    conn = get_connection()
    try:
        msg = conn.execute("SELECT * FROM complaints WHERE id = ?", (message_id,)).fetchone()
        if msg is None:
            return err("ไม่พบข้อความที่ระบุ", 404)
        if not _check_message_access(conn, msg["case_id"]):
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)

        conn.execute(
            "UPDATE complaints SET status = 'RESOLVED', resolved_by = ?, resolved_at = ? WHERE id = ?",
            (g.current_user["id"], now_iso(), message_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (message_id,)).fetchone()
        return ok(dict(row))
    finally:
        conn.close()


_REPLY_MAX_LEN = 1000


@bp.patch("/messages/<message_id>/reply")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.SURVEYOR)
def reply_case_message(message_id):
    """พิมพ์คำตอบกลับข้อความของประชาชน — ให้ช่างรังวัดเจ้าของเรื่องตอบเองได้ด้วย (ผู้ดูแลข้อมูลตรงกับเรื่องมากที่สุด)
    ไม่ใช่แค่บทบาทผู้บริหาร/หัวหน้าเหมือน resolve เดิม คำตอบจะไปแสดงในหน้าติดตามงานสาธารณะ (track.html) ให้ประชาชน
    เห็นทันที และถือว่าข้อความนี้ได้รับการดำเนินการแล้ว (ปิดเรื่องให้อัตโนมัติพร้อมกัน ไม่ต้องกดปุ่ม resolve ซ้ำ)"""
    payload = request.get_json(silent=True) or {}
    reply_text = (payload.get("reply") or "").strip()
    if not reply_text:
        return err("กรุณาพิมพ์ข้อความตอบกลับ")
    if len(reply_text) > _REPLY_MAX_LEN:
        return err(f"ข้อความยาวเกินไป (ไม่เกิน {_REPLY_MAX_LEN} ตัวอักษร)")

    conn = get_connection()
    try:
        msg = conn.execute("SELECT * FROM complaints WHERE id = ?", (message_id,)).fetchone()
        if msg is None:
            return err("ไม่พบข้อความที่ระบุ", 404)
        if not _check_message_access(conn, msg["case_id"]):
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)

        ts = now_iso()
        conn.execute(
            """UPDATE complaints
               SET reply_text = ?, replied_by = ?, replied_at = ?,
                   status = 'RESOLVED', resolved_by = ?, resolved_at = ?
               WHERE id = ?""",
            (reply_text, g.current_user["id"], ts, g.current_user["id"], ts, message_id),
        )
        conn.commit()
        row = conn.execute(
            """SELECT c.*, u.full_name AS resolved_by_name, r.full_name AS replied_by_name
               FROM complaints c
               LEFT JOIN users u ON u.id = c.resolved_by
               LEFT JOIN users r ON r.id = c.replied_by
               WHERE c.id = ?""",
            (message_id,),
        ).fetchone()
        return ok(dict(row))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Technical Review (QC) — เพิ่มใหม่ตาม Blueprint v2
# ---------------------------------------------------------------------------
@bp.post("/<case_id>/review")
@login_required
@require_roles(Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.PROVINCE_ADMIN)
def review_case(case_id):
    payload = request.get_json(silent=True) or {}
    review_result = payload.get("review_result")
    comments = payload.get("comments")
    if review_result not in ("APPROVE", "REJECT"):
        return err("review_result ต้องเป็น APPROVE หรือ REJECT")

    conn = get_connection()
    try:
        case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if g.current_user["role"] == Role.PROVINCE_ADMIN and not is_office_in_user_scope(conn, g.current_user, case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)
        if case["status"] != CaseStatus.PENDING_REVIEW:
            return err("ตรวจสอบ QC ได้เฉพาะเรื่องที่อยู่ในสถานะรอตรวจสอบ")

        ts = now_iso()
        conn.execute(
            """INSERT INTO case_reviews (id, case_id, reviewed_by, review_result, comments, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id(), case_id, g.current_user["id"], review_result, comments, ts),
        )

        if review_result == "APPROVE":
            record_status_change(conn, case_id, case["status"], CaseStatus.PENDING_APPROVAL, g.current_user["id"], comments)
        else:
            conn.execute(
                """INSERT INTO rework_requests (id, case_id, requested_by, reason, status, created_at)
                   VALUES (?, ?, ?, ?, 'OPEN', ?)""",
                (new_id(), case_id, g.current_user["id"], comments or "QC ไม่ผ่าน", ts),
            )
            record_status_change(conn, case_id, case["status"], CaseStatus.REWORK_REQUIRED, g.current_user["id"], comments)

        conn.commit()
        row = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        return ok(_enrich_case(conn,dict(row)))
    finally:
        conn.close()


@bp.post("/<case_id>/approve")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN)
def approve_case(case_id):
    conn = get_connection()
    try:
        case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if g.current_user["role"] == Role.PROVINCE_ADMIN and not is_office_in_user_scope(conn, g.current_user, case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)
        if case["status"] != CaseStatus.PENDING_APPROVAL:
            return err("อนุมัติปิดงานได้เฉพาะเรื่องที่ผ่าน QC แล้ว (PENDING_APPROVAL)")

        record_status_change(conn, case_id, case["status"], CaseStatus.CLOSED, g.current_user["id"], "อนุมัติปิดงานโดยผู้บริหาร")
        conn.commit()
        log_action(conn, g.current_user["id"], "UPDATE", "survey_cases", case_id, after={"status": CaseStatus.CLOSED})

        row = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        return ok(_enrich_case(conn,dict(row)))
    finally:
        conn.close()
