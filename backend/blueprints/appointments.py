from flask import Blueprint, g, request

from constants import CaseStatus, Role
from db import get_connection
from helpers import err, is_office_in_user_scope, new_id, now_iso, ok, record_status_change, scope_case_filter
from security import login_required, require_roles

bp = Blueprint("appointments", __name__, url_prefix="/api/v1")

CASE_NOT_YET_APPOINTED = {CaseStatus.RECEIVED, CaseStatus.WAITING_ASSIGNMENT, CaseStatus.ASSIGNED}


@bp.get("/appointments")
@login_required
def list_appointments():
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        query = f"""SELECT a.* FROM appointments a
                    JOIN survey_cases sc ON sc.id = a.case_id
                    WHERE sc.id IN (SELECT id FROM survey_cases WHERE {where_sql})"""

        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        if date_from:
            query += " AND a.appointment_start >= ?"
            params.append(date_from)
        if date_to:
            query += " AND a.appointment_start <= ?"
            params.append(date_to)

        query += " ORDER BY a.appointment_start"
        rows = conn.execute(query, params).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.post("/survey-cases/<case_id>/appointments")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.SURVEYOR)
def create_appointment(case_id):
    payload = request.get_json(silent=True) or {}
    if not payload.get("appointment_start"):
        return err("ต้องระบุ appointment_start")

    conn = get_connection()
    try:
        case = conn.execute("SELECT * FROM survey_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            return err("ไม่พบเรื่องที่ระบุ", 404)
        if g.current_user["role"] == Role.PROVINCE_ADMIN and not is_office_in_user_scope(conn, g.current_user, case["office_id"]):
            return err("เรื่องนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        has_assignment = conn.execute(
            "SELECT 1 FROM case_assignments WHERE case_id = ? AND is_active = 1", (case_id,)
        ).fetchone()
        if not has_assignment:
            return err("ต้องมอบหมายช่างรังวัดก่อนจึงจะสร้างนัดหมายได้ (ตามลำดับ Workflow ข้อ 2-3)")

        appointment_id = new_id()
        ts = now_iso()
        conn.execute(
            """INSERT INTO appointments (id, case_id, appointment_start, appointment_end, location, status, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'SCHEDULED', ?, ?, ?)""",
            (
                appointment_id,
                case_id,
                payload["appointment_start"],
                payload.get("appointment_end"),
                payload.get("location"),
                g.current_user["id"],
                ts,
                ts,
            ),
        )

        if case["status"] in CASE_NOT_YET_APPOINTED:
            record_status_change(conn, case_id, case["status"], CaseStatus.APPOINTED, g.current_user["id"])
        conn.execute("UPDATE survey_cases SET appointment_date = ?, updated_at = ? WHERE id = ?", (payload["appointment_start"], ts, case_id))

        conn.commit()
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()


UPDATABLE_APPOINTMENT_FIELDS = {"appointment_start", "appointment_end", "location", "status"}


@bp.patch("/appointments/<appointment_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.SURVEYOR)
def update_appointment(appointment_id):
    payload = request.get_json(silent=True) or {}
    fields = {k: v for k, v in payload.items() if k in UPDATABLE_APPOINTMENT_FIELDS}
    if not fields:
        return err("ไม่มีข้อมูลที่จะอัปเดต")

    conn = get_connection()
    try:
        appt_case = conn.execute(
            """SELECT sc.office_id FROM appointments a JOIN survey_cases sc ON sc.id = a.case_id WHERE a.id = ?""",
            (appointment_id,),
        ).fetchone()
        if appt_case is None:
            return err("ไม่พบรายการนัดหมาย", 404)
        if g.current_user["role"] == Role.PROVINCE_ADMIN and not is_office_in_user_scope(conn, g.current_user, appt_case["office_id"]):
            return err("รายการนัดหมายนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE appointments SET {set_clause}, updated_at = ? WHERE id = ?",
            list(fields.values()) + [now_iso(), appointment_id],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        return ok(dict(row))
    finally:
        conn.close()
