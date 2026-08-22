from flask import Blueprint, request

from constants import Role
from db import get_connection
from helpers import err, new_id, now_iso, ok
from security import login_required, require_roles

bp = Blueprint("survey_types", __name__, url_prefix="/api/v1/survey-types")


@bp.get("")
@login_required
def list_survey_types():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM survey_types WHERE is_active = 1 ORDER BY name").fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.post("")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR)
def create_survey_type():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return err("ต้องระบุ name")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM survey_types WHERE name = ? AND is_active = 1", (name,)).fetchone()
        if existing:
            # มีประเภทงานชื่อนี้อยู่แล้ว -> คืนตัวเดิมแทนการสร้างซ้ำ (กันกรณีพิมพ์เพิ่มชื่อที่มีอยู่แล้วผ่านช่องค้นหา)
            return ok(dict(existing), 200)

        # code ใช้แค่เป็นรหัสอ้างอิงภายใน ไม่แสดงในหน้าจอ -> สร้างให้อัตโนมัติถ้าไม่ระบุมา
        code = (payload.get("code") or "").strip() or f"CUSTOM-{new_id()[:8].upper()}"
        if conn.execute("SELECT 1 FROM survey_types WHERE code = ?", (code,)).fetchone():
            code = f"CUSTOM-{new_id()[:8].upper()}"

        type_id = new_id()
        ts = now_iso()
        conn.execute(
            """INSERT INTO survey_types (id, code, name, target_days, requires_announcement, fee_amount, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                type_id,
                code,
                name,
                payload.get("target_days", 30),
                1 if payload.get("requires_announcement") else 0,
                payload.get("fee_amount"),
                ts,
                ts,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM survey_types WHERE id = ?", (type_id,)).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()
