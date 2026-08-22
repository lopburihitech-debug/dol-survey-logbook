import os
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, g, request, send_from_directory

from blueprints.case_documents import BLOCKED_EXTENSIONS, MAX_FILE_SIZE_BYTES, UPLOAD_DIR
from constants import CaseStatus, Role
from db import get_connection
from helpers import err, get_user_province, is_office_in_user_scope, new_id, now_iso, ok, province_office_ids
from security import hash_password, login_required, require_roles
from services.audit import log_action
from services.employee_code import generate_employee_code

bp = Blueprint("surveyors", __name__, url_prefix="/api/v1/surveyors")
uploads_bp = Blueprint("surveyor_uploads", __name__, url_prefix="/api/v1/surveyor-uploads")

ACTIVE_CASE_COUNT_SQL = """
    (SELECT COUNT(*) FROM case_assignments ca
     JOIN survey_cases sc ON sc.id = ca.case_id
     WHERE ca.surveyor_id = s.id AND ca.is_active = 1
       AND sc.status NOT IN ('COMPLETED', 'CLOSED', 'CANCELLED')) AS active_case_count
"""

# จำนวนงานทั้งหมด/งานเสร็จ (ถอนจ่ายแล้ว) ของช่างแต่ละคน — ใช้ ca.is_active = 1 เป็นเกณฑ์เดียวกับ active_case_count
# ด้านบนและหน้าโปรไฟล์ช่างรังวัด (surveyor-profile.html) คือ "เรื่องที่ช่างคนนี้เป็นผู้รับผิดชอบล่าสุดอยู่ตอนนี้"
# ไม่ว่าเรื่องนั้นจะจบไปแล้วหรือไม่ก็ตาม (การมอบหมายจะถูกปิด is_active=0 ก็ต่อเมื่อมีการ "เปลี่ยนช่าง" เท่านั้น)
TOTAL_CASE_COUNT_SQL = """
    (SELECT COUNT(*) FROM case_assignments ca
     WHERE ca.surveyor_id = s.id AND ca.is_active = 1) AS total_case_count
"""
COMPLETED_CASE_COUNT_SQL = """
    (SELECT COUNT(*) FROM case_assignments ca
     JOIN survey_cases sc ON sc.id = ca.case_id
     WHERE ca.surveyor_id = s.id AND ca.is_active = 1 AND sc.status = 'CLOSED') AS completed_case_count
"""


def _scope_office(conn, current_user: dict):
    """system_admin/administrator เห็นทุกสำนักงาน, province_admin เห็นทุกสำนักงานในจังหวัดตัวเอง,
    ส่วน supervisor/surveyor เห็นเฉพาะสำนักงานตน"""
    if current_user["role"] in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR):
        return "1=1", []
    if current_user["role"] == Role.PROVINCE_ADMIN:
        province = get_user_province(conn, current_user)
        office_ids = province_office_ids(conn, province) if province else []
        if not office_ids:
            return "1=0", []
        placeholders = ", ".join("?" for _ in office_ids)
        return f"s.office_id IN ({placeholders})", office_ids
    return "s.office_id = ?", [current_user.get("office_id")]


@bp.get("")
@login_required
def list_surveyors():
    conn = get_connection()
    try:
        where_sql, params = _scope_office(conn, g.current_user)
        office_id = request.args.get("office_id")
        if office_id:
            where_sql += " AND s.office_id = ?"
            params.append(office_id)

        rows = conn.execute(
            f"""SELECT s.*, u.full_name, u.email, u.phone, u.username, u.is_active AS user_is_active,
                       o.name AS office_name, o.province AS office_province,
                       {ACTIVE_CASE_COUNT_SQL}, {TOTAL_CASE_COUNT_SQL}, {COMPLETED_CASE_COUNT_SQL}
                FROM surveyors s
                JOIN users u ON u.id = s.user_id
                JOIN offices o ON o.id = s.office_id
                WHERE s.is_active = 1 AND {where_sql}
                ORDER BY u.full_name""",
            params,
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.post("")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.BRANCH_ADMIN)
def create_surveyor():
    payload = request.get_json(silent=True) or {}
    if not payload.get("office_id"):
        return err("ต้องระบุ office_id")

    conn = get_connection()
    try:
        if not is_office_in_user_scope(conn, g.current_user, payload["office_id"]):
            return err("สำนักงานที่ระบุอยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        # รหัสพนักงานออกให้อัตโนมัติเสมอ (รูปแบบ SV-XXX) ไม่รับค่าจากผู้ใช้ตอนเพิ่มช่างรังวัดใหม่ กันรหัสซ้ำ/พิมพ์ผิด
        employee_code = generate_employee_code(conn)

        user_id = payload.get("user_id")
        ts = now_iso()

        if not user_id:
            # ยังไม่มีบัญชีผู้ใช้อยู่ก่อน -> สร้างให้ใหม่ในขั้นตอนเดียวกัน (system_admin, province_admin หรือ branch_admin
            # ที่สร้างให้สำนักงาน/สาขาตัวเองเท่านั้น — administrator ไม่ให้สร้างบัญชีใหม่ตรงนี้ ต้องมี user_id อยู่แล้ว)
            if g.current_user["role"] not in (Role.SYSTEM_ADMIN, Role.PROVINCE_ADMIN, Role.BRANCH_ADMIN):
                return err("การสร้างบัญชีผู้ใช้ใหม่ต้องใช้สิทธิ์ผู้ดูแลระบบ ผู้ดูแลระดับจังหวัด หรือเจ้าพนักงานที่ดินสาขา — ถ้ามีบัญชีผู้ใช้อยู่แล้วให้ระบุ user_id แทน", 403)
            required = ["username", "password", "full_name"]
            if not all(payload.get(f) for f in required):
                return err(f"ยังไม่มี user_id — ต้องระบุ {', '.join(required)} เพื่อสร้างบัญชีผู้ใช้ใหม่ให้ช่างรังวัด")
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (payload["username"],)).fetchone():
                return err("username นี้ถูกใช้งานแล้ว", 409)

            user_id = new_id()
            conn.execute(
                """INSERT INTO users (id, username, password_hash, full_name, email, phone, role, office_id,
                                       mfa_enabled, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'surveyor', ?, 0, 1, ?, ?)""",
                (
                    user_id,
                    payload["username"],
                    hash_password(payload["password"]),
                    payload["full_name"],
                    payload.get("email"),
                    payload.get("phone"),
                    payload["office_id"],
                    ts,
                    ts,
                ),
            )
        else:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if user is None:
                return err("ไม่พบบัญชีผู้ใช้ที่ระบุ", 404)
            if user["role"] != Role.SURVEYOR:
                return err("บัญชีผู้ใช้ที่ระบุต้องมีบทบาทเป็นช่างรังวัด (surveyor)")
            if conn.execute("SELECT 1 FROM surveyors WHERE user_id = ?", (user_id,)).fetchone():
                return err("บัญชีผู้ใช้นี้มีข้อมูลช่างรังวัดอยู่แล้ว", 409)

        surveyor_id = new_id()
        conn.execute(
            """INSERT INTO surveyors (id, user_id, employee_code, nickname, position, photo_url, office_id, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                surveyor_id,
                user_id,
                employee_code,
                payload.get("nickname"),
                payload.get("position"),
                payload.get("photo_url"),
                payload["office_id"],
                ts,
                ts,
            ),
        )
        conn.commit()
        log_action(conn, g.current_user["id"], "CREATE", "surveyors", surveyor_id, after={"employee_code": employee_code})

        row = conn.execute(
            """SELECT s.*, u.full_name, u.email, u.phone, u.username, o.name AS office_name
               FROM surveyors s JOIN users u ON u.id = s.user_id JOIN offices o ON o.id = s.office_id
               WHERE s.id = ?""",
            (surveyor_id,),
        ).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()


UPDATABLE_SURVEYOR_FIELDS = {"employee_code", "nickname", "position", "photo_url", "office_id", "is_active"}

# ฟิลด์ของบัญชีผู้ใช้ (users) ที่แก้ไขได้ผ่านหน้า "จัดการข้อมูลช่างรังวัด" ไปพร้อมกับข้อมูลช่างในคำขอเดียวกัน
# (แยกจาก PATCH /api/v1/users/<id> ซึ่งจำกัดเฉพาะ system_admin และแก้ไขผู้ใช้ "ทุกบทบาท" ได้ กว้างเกินไปสำหรับหน้านี้
#  ซึ่งต้องการให้ administrator แก้ไขข้อมูลช่างรังวัด "เฉพาะของช่างที่ตัวเองดูแล" ได้ทั้งหมดในหน้าเดียว — username ไม่ให้แก้
# เพราะใช้เป็น login identifier ถาวร ส่วนรหัสผ่านมีปุ่มแยกที่เรียก /api/v1/users/<id>/reset-password ของเดิม)
UPDATABLE_USER_FIELDS_VIA_SURVEYOR = {"full_name", "email", "phone"}


@bp.patch("/<surveyor_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.BRANCH_ADMIN)
def update_surveyor(surveyor_id):
    payload = request.get_json(silent=True) or {}
    fields = {k: v for k, v in payload.items() if k in UPDATABLE_SURVEYOR_FIELDS}
    user_fields = {k: v for k, v in payload.items() if k in UPDATABLE_USER_FIELDS_VIA_SURVEYOR}
    # user_is_active คุมสิทธิ์ "เข้าสู่ระบบได้ไหม" (users.is_active) แยกจาก is_active ข้างบนซึ่งคุม "ยังนับเป็นช่างที่ปฏิบัติงานอยู่ไหม"
    # (surveyors.is_active — ใช้กรองรายชื่อในหน้าภาพรวมช่างรังวัด) สองอย่างนี้ตั้งใจแยกกันเพราะบางครั้งย้าย/ลาออกแต่ยังไม่ปิดบัญชี
    user_is_active = payload.get("user_is_active")
    if not fields and not user_fields and user_is_active is None:
        return err("ไม่มีข้อมูลที่จะอัปเดต")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone()
        if existing is None:
            return err("ไม่พบข้อมูลช่างรังวัด", 404)

        # province_admin แก้ได้เฉพาะช่างที่สังกัดสำนักงานในจังหวัดตัวเอง (ทั้งตอนนี้ และถ้าจะย้าย ปลายทางก็ต้องอยู่ในจังหวัด
        # เดียวกันด้วย — กันไม่ให้ยืมช่องแก้ไขนี้ "ดึง" ช่างจากจังหวัดอื่นเข้ามา หรือ "ส่ง" ช่างของตัวเองออกไปจังหวัดอื่น)
        if not is_office_in_user_scope(conn, g.current_user, existing["office_id"]):
            return err("ช่างรังวัดคนนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)
        if "office_id" in fields and not is_office_in_user_scope(conn, g.current_user, fields["office_id"]):
            return err("สำนักงานปลายทางอยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        if "employee_code" in fields and fields["employee_code"] != existing["employee_code"]:
            if conn.execute(
                "SELECT 1 FROM surveyors WHERE employee_code = ? AND id != ?", (fields["employee_code"], surveyor_id)
            ).fetchone():
                return err("รหัสพนักงานนี้ถูกใช้งานแล้ว", 409)

        if "full_name" in user_fields and not user_fields["full_name"]:
            return err("ต้องระบุชื่อ-นามสกุล")

        office_changed = "office_id" in fields and fields["office_id"] != existing["office_id"]
        if office_changed and not conn.execute("SELECT 1 FROM offices WHERE id = ?", (fields["office_id"],)).fetchone():
            return err("ไม่พบสำนักงานที่ระบุ")

        if fields:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE surveyors SET {set_clause}, updated_at = ? WHERE id = ?",
                list(fields.values()) + [now_iso(), surveyor_id],
            )

        # อัปเดตบัญชีผู้ใช้ที่ผูกกับช่างคนนี้ในคำขอเดียวกัน — รวมถึง office_id ด้วยถ้าย้ายสำนักงาน (เดิม users.office_id
        # ไม่ได้ตามไปแก้เลย ทำให้หน้า "ผู้ใช้งาน" กับหน้า "ช่างรังวัด" แสดงสำนักงานไม่ตรงกันหลังแก้ไข)
        user_set = dict(user_fields)
        if user_is_active is not None:
            user_set["is_active"] = 1 if user_is_active else 0
        if office_changed:
            user_set["office_id"] = fields["office_id"]
        if user_set:
            set_clause = ", ".join(f"{k} = ?" for k in user_set)
            conn.execute(
                f"UPDATE users SET {set_clause}, updated_at = ? WHERE id = ?",
                list(user_set.values()) + [now_iso(), existing["user_id"]],
            )

        moved_case_count = 0
        if office_changed:
            # ย้ายเรื่องที่ช่างคนนี้เป็นผู้รับผิดชอบอยู่ในปัจจุบัน (case_assignments.is_active = 1 ไม่ว่าเรื่องนั้นจะจบไปแล้ว
            # หรือยังค้างอยู่ก็ตาม — เกณฑ์เดียวกับ TOTAL_CASE_COUNT_SQL ด้านบน) ให้ตามไปสำนักงานใหม่ด้วย ไม่งั้น Dashboard
            # ภาพรวมตามจังหวัด/สาขา (ซึ่งนับจาก survey_cases.office_id) จะเห็นสำนักงานใหม่มีงาน 0 ทั้งที่ช่างมีงานอยู่จริง
            moved = conn.execute(
                """UPDATE survey_cases SET office_id = ?, updated_at = ?
                   WHERE office_id != ? AND id IN (
                       SELECT case_id FROM case_assignments WHERE surveyor_id = ? AND is_active = 1
                   )""",
                (fields["office_id"], now_iso(), fields["office_id"], surveyor_id),
            )
            moved_case_count = moved.rowcount

        conn.commit()
        log_action(
            conn, g.current_user["id"], "UPDATE", "surveyors", surveyor_id,
            before=dict(existing), after={**fields, **user_set, "moved_case_count": moved_case_count},
        )

        row = conn.execute(
            """SELECT s.*, u.full_name, u.email, u.phone, u.username, u.is_active AS user_is_active, o.name AS office_name
               FROM surveyors s JOIN users u ON u.id = s.user_id JOIN offices o ON o.id = s.office_id
               WHERE s.id = ?""",
            (surveyor_id,),
        ).fetchone()
        result = dict(row)
        result["moved_case_count"] = moved_case_count
        return ok(result)
    finally:
        conn.close()


@bp.post("/sync-office-cases")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.PROVINCE_ADMIN)
def sync_office_cases():
    """เครื่องมือซ่อมข้อมูลครั้งเดียว (one-time repair) — ใช้กรณีเคยเปลี่ยนสำนักงานของช่างรังวัดไว้ตั้งแต่ก่อนที่ระบบจะ
    ย้ายเรื่องให้อัตโนมัติ (ดู update_surveyor ด้านบน) ทำให้ office_id ของเรื่องกับสำนักงานปัจจุบันของช่างไม่ตรงกันค้างอยู่
    เดินตรวจช่างทุกคน แล้วย้าย office_id ของเรื่องที่ช่างคนนั้นรับผิดชอบอยู่ในปัจจุบัน (case_assignments.is_active = 1)
    ให้ตรงกับสำนักงานปัจจุบันของช่างเสมอ — กดซ้ำได้เรื่อยๆ ไม่มีผลเสีย (ถ้าตรงกันอยู่แล้วจะไม่มีอะไรถูกย้าย)
    province_admin: จำกัดเฉพาะช่างที่สังกัดสำนักงานในจังหวัดตัวเองเท่านั้น (system_admin ซ่อมได้ทั้งระบบ)
    """
    conn = get_connection()
    try:
        if g.current_user["role"] == Role.PROVINCE_ADMIN:
            province = get_user_province(conn, g.current_user)
            office_ids = province_office_ids(conn, province) if province else []
            if not office_ids:
                return ok({"total_moved": 0, "surveyors_affected": 0, "details": []})
            placeholders = ", ".join("?" for _ in office_ids)
            surveyors = conn.execute(
                f"SELECT id, office_id FROM surveyors WHERE office_id IN ({placeholders})", office_ids
            ).fetchall()
        else:
            surveyors = conn.execute("SELECT id, office_id FROM surveyors").fetchall()
        ts = now_iso()
        total_moved = 0
        details = []
        for s in surveyors:
            moved = conn.execute(
                """UPDATE survey_cases SET office_id = ?, updated_at = ?
                   WHERE office_id != ? AND id IN (
                       SELECT case_id FROM case_assignments WHERE surveyor_id = ? AND is_active = 1
                   )""",
                (s["office_id"], ts, s["office_id"], s["id"]),
            )
            if moved.rowcount:
                total_moved += moved.rowcount
                details.append({"surveyor_id": s["id"], "moved": moved.rowcount})
        conn.commit()
        if total_moved:
            log_action(conn, g.current_user["id"], "SYNC_OFFICE_CASES", "survey_cases", None, after={"total_moved": total_moved, "surveyors_affected": len(details)})
        return ok({"total_moved": total_moved, "surveyors_affected": len(details), "details": details})
    finally:
        conn.close()


@bp.post("/<surveyor_id>/photo")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.BRANCH_ADMIN)
def upload_surveyor_photo(surveyor_id):
    conn = get_connection()
    try:
        surveyor = conn.execute("SELECT * FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone()
        if surveyor is None:
            return err("ไม่พบข้อมูลช่างรังวัด", 404)
        if not is_office_in_user_scope(conn, g.current_user, surveyor["office_id"]):
            return err("ช่างรังวัดคนนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        file = request.files.get("file")
        if not file or not file.filename:
            return err("ต้องแนบไฟล์รูปภาพ (field name: file)")

        ext = Path(file.filename).suffix.lower()
        if ext in BLOCKED_EXTENSIONS:
            return err("ไม่รองรับไฟล์ประเภทนี้")

        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        file.stream.seek(0)
        if size > MAX_FILE_SIZE_BYTES:
            return err(f"ไฟล์ใหญ่เกินไป (จำกัดไม่เกิน {MAX_FILE_SIZE_BYTES // (1024*1024)}MB ต่อไฟล์)")

        # ลบรูปเดิม (ถ้ามี) ก่อนบันทึกรูปใหม่ เพราะช่างแต่ละคนมีได้แค่รูปเดียว
        photo_dir = UPLOAD_DIR / "surveyors" / surveyor_id
        if photo_dir.exists():
            for old_file in photo_dir.glob("*"):
                try:
                    old_file.unlink()
                except Exception:
                    pass
        photo_dir.mkdir(parents=True, exist_ok=True)

        stored_name = f"{new_id()}{ext}"
        file.save(str(photo_dir / stored_name))

        photo_url = f"/api/v1/surveyor-uploads/{surveyor_id}/{stored_name}"
        conn.execute("UPDATE surveyors SET photo_url = ?, updated_at = ? WHERE id = ?", (photo_url, now_iso(), surveyor_id))
        conn.commit()

        row = conn.execute(
            """SELECT s.*, u.full_name, u.email, u.phone, u.username, o.name AS office_name
               FROM surveyors s JOIN users u ON u.id = s.user_id JOIN offices o ON o.id = s.office_id
               WHERE s.id = ?""",
            (surveyor_id,),
        ).fetchone()
        return ok(dict(row))
    finally:
        conn.close()


@bp.delete("/<surveyor_id>/photo")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.BRANCH_ADMIN)
def delete_surveyor_photo(surveyor_id):
    conn = get_connection()
    try:
        surveyor = conn.execute("SELECT * FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone()
        if surveyor is None:
            return err("ไม่พบข้อมูลช่างรังวัด", 404)
        if not is_office_in_user_scope(conn, g.current_user, surveyor["office_id"]):
            return err("ช่างรังวัดคนนี้อยู่นอกเขตจังหวัดที่ท่านดูแล", 403)

        photo_dir = UPLOAD_DIR / "surveyors" / surveyor_id
        if photo_dir.exists():
            for old_file in photo_dir.glob("*"):
                try:
                    old_file.unlink()
                except Exception:
                    pass

        conn.execute("UPDATE surveyors SET photo_url = NULL, updated_at = ? WHERE id = ?", (now_iso(), surveyor_id))
        conn.commit()
        return ok({"deleted": True})
    finally:
        conn.close()


@uploads_bp.get("/<surveyor_id>/<filename>")
@login_required
def serve_surveyor_photo(surveyor_id, filename):
    return send_from_directory(str(UPLOAD_DIR / "surveyors" / surveyor_id), filename)


@bp.get("/<surveyor_id>/workload")
@login_required
def get_workload(surveyor_id):
    conn = get_connection()
    try:
        surveyor = conn.execute("SELECT * FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone()
        if surveyor is None:
            return err("ไม่พบข้อมูลช่างรังวัด", 404)
        user = conn.execute("SELECT full_name FROM users WHERE id = ?", (surveyor["user_id"],)).fetchone()

        cases = conn.execute(
            """SELECT sc.status, sc.due_date FROM survey_cases sc
               JOIN case_assignments ca ON ca.case_id = sc.id
               WHERE ca.surveyor_id = ? AND ca.is_active = 1""",
            (surveyor_id,),
        ).fetchall()

        now = datetime.now().isoformat()
        active_count = len([c for c in cases if c["status"] not in CaseStatus.CLOSED_SET])
        overdue_count = len(
            [c for c in cases if c["due_date"] and c["due_date"] < now and c["status"] not in CaseStatus.CLOSED_SET]
        )

        return ok(
            {
                "surveyor_id": surveyor_id,
                "full_name": user["full_name"] if user else "",
                "active_case_count": active_count,
                "overdue_case_count": overdue_count,
            }
        )
    finally:
        conn.close()


@bp.get("/<surveyor_id>/cases")
@login_required
def get_surveyor_cases(surveyor_id):
    conn = get_connection()
    try:
        surveyor = conn.execute("SELECT * FROM surveyors WHERE id = ?", (surveyor_id,)).fetchone()
        if surveyor is None:
            return err("ไม่พบข้อมูลช่างรังวัด", 404)

        role = g.current_user["role"]
        if role == Role.SURVEYOR:
            own = conn.execute("SELECT id FROM surveyors WHERE user_id = ?", (g.current_user["id"],)).fetchone()
            if not own or own["id"] != surveyor_id:
                return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)
        elif role in (Role.SUPERVISOR, Role.BRANCH_ADMIN):
            if surveyor["office_id"] != g.current_user.get("office_id"):
                return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)
        elif role == Role.PROVINCE_ADMIN:
            if not is_office_in_user_scope(conn, g.current_user, surveyor["office_id"]):
                return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)
        elif role not in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR):
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)

        rows = conn.execute(
            """SELECT sc.id, sc.case_code, sc.requester_name, sc.status, sc.due_date, sc.appointment_date, sc.survey_type_id
               FROM survey_cases sc
               JOIN case_assignments ca ON ca.case_id = sc.id
               WHERE ca.surveyor_id = ? AND ca.is_active = 1
               ORDER BY (sc.status NOT IN ('COMPLETED', 'CLOSED', 'CANCELLED')) DESC, sc.due_date""",
            (surveyor_id,),
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


def _check_profile_access(conn, surveyor: dict, current_user: dict):
    """สิทธิ์เข้าดูโปรไฟล์ช่างรังวัด: system_admin/administrator ดูได้ทุกคน, province_admin ดูได้เฉพาะช่างในจังหวัดตัวเอง,
    supervisor ดูได้เฉพาะช่างในสำนักงานตน, surveyor ดูได้เฉพาะโปรไฟล์ของตัวเอง — ใช้กฎเดียวกับ get_surveyor_cases() ด้านบน"""
    role = current_user["role"]
    if role in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR):
        return None
    if role == Role.PROVINCE_ADMIN:
        if not is_office_in_user_scope(conn, current_user, surveyor["office_id"]):
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)
        return None
    if role in (Role.SUPERVISOR, Role.BRANCH_ADMIN):
        if surveyor["office_id"] != current_user.get("office_id"):
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)
        return None
    if role == Role.SURVEYOR:
        own = conn.execute("SELECT id FROM surveyors WHERE user_id = ?", (current_user["id"],)).fetchone()
        if not own or own["id"] != surveyor["id"]:
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)
        return None
    return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)


def _parse_date(value):
    """แปลงค่าวันที่ที่เก็บเป็น TEXT (อาจเป็น "YYYY-MM-DD" ล้วนๆ หรือ ISO datetime เต็ม) ให้เป็น date object
    คืนค่า None ถ้าไม่มีค่าหรือแปลงไม่ได้ — ใช้ร่วมกันทั้งการนับเดือน/ปี และการคำนวณวันเกินนัดรังวัด"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


# สถานะที่ถือว่า "จบงานแล้ว" ไม่ต้องนับเป็นเกินนัดรังวัด แม้จะเลยวันนัดมานานแค่ไหนก็ตาม — ตรงกับ
# OVERDUE_EXCLUDED_STATUSES ฝั่ง frontend (cases.html) เพื่อให้ตัวเลขในหน้าโปรไฟล์กับหน้ารายการงานตรงกัน
OVERDUE_EXCLUDED_STATUSES = {
    CaseStatus.CLOSED, CaseStatus.CANCELLED, "SURVEY_SKIPPED", "RE_APPOINTMENT_NEEDED", "POSTPONED",
    CaseStatus.SURVEY_DONE,  # รังวัดเสร็จแล้ว ไม่นับเกินนัดรังวัดอีกต่อไป (ลงพื้นที่ไปแล้วจริง)
}

# เกณฑ์เช็คลิสต์ความคืบหน้า 4 รายการ — ตรงกับ CHECKLIST_DEFS ฝั่ง frontend (cases.html/case.html) ใช้กำหนดว่า
# ค่าไหนถือเป็น "เสร็จ/ครบ" (pos) ค่าไหนถือเป็น "ยังไม่เสร็จ/รอ" (neg) ส่วนที่เหลือ (null หรือค่าอื่น) นับเป็น "ยังไม่ระบุ"
CHECKLIST_FIELDS = [
    ("survey_result", "DONE", "NOT_DONE"),
    ("mapping_status", "DONE", "NOT_DONE"),
    ("neighbor_status", "COMPLETE", "WAITING_AGENCY"),
    ("announcement_status", "POSTED", "WAITING"),
]


@bp.get("/<surveyor_id>/profile")
@login_required
def get_surveyor_profile_page(surveyor_id):
    """สรุปข้อมูลผลงานของช่างรังวัดคนหนึ่งแบบครบวงจร (หน้าโปรไฟล์ช่าง): จำนวนงานรวม/ค้าง/เสร็จ/ยกเลิก,
    แยกตามประเภทงาน, เกินนัดรังวัด 30/60 วัน, แยกตามเดือนที่รับเรื่อง, สรุปเช็คลิสต์ความคืบหน้ารวม และรายการงานทั้งหมด"""
    conn = get_connection()
    try:
        surveyor = conn.execute(
            """SELECT s.*, u.full_name, u.email, u.phone, u.username, o.name AS office_name
               FROM surveyors s JOIN users u ON u.id = s.user_id JOIN offices o ON o.id = s.office_id
               WHERE s.id = ?""",
            (surveyor_id,),
        ).fetchone()
        if surveyor is None:
            return err("ไม่พบข้อมูลช่างรังวัด", 404)

        access_error = _check_profile_access(conn, surveyor, g.current_user)
        if access_error:
            return access_error

        cases = conn.execute(
            """SELECT sc.id, sc.case_code, sc.requester_name, sc.status, sc.survey_type_id,
                      sc.received_date, sc.appointment_date, sc.due_date,
                      sc.survey_result, sc.mapping_status, sc.neighbor_status, sc.announcement_status
               FROM survey_cases sc
               JOIN case_assignments ca ON ca.case_id = sc.id
               WHERE ca.surveyor_id = ? AND ca.is_active = 1
               ORDER BY sc.received_date DESC""",
            (surveyor_id,),
        ).fetchall()
        cases = [dict(r) for r in cases]

        type_rows = conn.execute("SELECT id, name FROM survey_types").fetchall()
        type_name_map = {t["id"]: t["name"] for t in type_rows}

        today = date.today()

        kpi = {"total": len(cases), "active": 0, "completed": 0, "cancelled": 0, "overdue_30": 0, "overdue_60": 0}
        by_type = {}
        by_month = {}
        checklist = {field: {"pos": 0, "neg": 0, "unset": 0} for field, _, _ in CHECKLIST_FIELDS}

        for c in cases:
            status = c["status"]
            if status == CaseStatus.CLOSED:
                kpi["completed"] += 1
            elif status == CaseStatus.CANCELLED:
                kpi["cancelled"] += 1
            else:
                kpi["active"] += 1

            if status not in OVERDUE_EXCLUDED_STATUSES:
                appt = _parse_date(c["appointment_date"])
                if appt:
                    diff_days = (today - appt).days
                    if diff_days >= 60:
                        kpi["overdue_60"] += 1
                    elif diff_days >= 30:
                        kpi["overdue_30"] += 1

            type_id = c["survey_type_id"]
            bucket = by_type.setdefault(
                type_id, {"survey_type_id": type_id, "name": type_name_map.get(type_id, "-"), "total": 0, "active": 0, "completed": 0}
            )
            bucket["total"] += 1
            if status == CaseStatus.CLOSED:
                bucket["completed"] += 1
            elif status != CaseStatus.CANCELLED:
                bucket["active"] += 1

            received = _parse_date(c["received_date"])
            if received:
                key = f"{received.year:04d}-{received.month:02d}"
                mbucket = by_month.setdefault(key, {"year_month": key, "total": 0, "completed": 0})
                mbucket["total"] += 1
                if status == CaseStatus.CLOSED:
                    mbucket["completed"] += 1

            for field, pos_value, neg_value in CHECKLIST_FIELDS:
                value = c.get(field)
                if value == pos_value:
                    checklist[field]["pos"] += 1
                elif value == neg_value:
                    checklist[field]["neg"] += 1
                else:
                    checklist[field]["unset"] += 1

        return ok(
            {
                "surveyor": dict(surveyor),
                "kpi": kpi,
                "by_type": sorted(by_type.values(), key=lambda t: t["total"], reverse=True),
                "by_month": sorted(by_month.values(), key=lambda m: m["year_month"]),
                "checklist": checklist,
                "cases": cases,
            }
        )
    finally:
        conn.close()
