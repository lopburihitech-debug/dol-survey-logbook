from flask import Blueprint, g, request

from constants import Role
from db import get_connection
from helpers import err, new_id, now_iso, ok
from security import login_required, require_roles
from services.audit import log_action

bp = Blueprint("offices", __name__, url_prefix="/api/v1/offices")


@bp.get("")
@login_required
def list_offices():
    """ปกติคืนเฉพาะสำนักงานที่เปิดใช้งานอยู่ (ใช้เติม dropdown เลือกสำนักงานทั่วระบบ) — ผู้ดูแลระบบเท่านั้นที่ขอ
    ?include_inactive=1 เพื่อดูสำนักงานที่ปิดใช้งานด้วยได้ (ใช้ในหน้าจัดการสำนักงาน จะได้เปิดใช้งานกลับได้)"""
    conn = get_connection()
    try:
        include_inactive = request.args.get("include_inactive") == "1" and g.current_user["role"] == Role.SYSTEM_ADMIN
        where_sql = "1=1" if include_inactive else "is_active = 1"
        rows = conn.execute(f"SELECT * FROM offices WHERE {where_sql} ORDER BY name").fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.post("")
@login_required
@require_roles(Role.SYSTEM_ADMIN)
def create_office():
    payload = request.get_json(silent=True) or {}
    required = ["code", "name", "province"]
    if not all(payload.get(f) for f in required):
        return err(f"ต้องระบุ {', '.join(required)}")

    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM offices WHERE code = ?", (payload["code"],)).fetchone():
            return err("รหัสสำนักงานนี้ถูกใช้งานแล้ว", 409)

        office_id = new_id()
        ts = now_iso()
        conn.execute(
            """INSERT INTO offices (id, code, name, province, district, address, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (office_id, payload["code"], payload["name"], payload["province"], payload.get("district"), payload.get("address"), ts, ts),
        )
        conn.commit()
        log_action(conn, g.current_user["id"], "CREATE", "offices", office_id, after={"code": payload["code"], "name": payload["name"]})
        row = conn.execute("SELECT * FROM offices WHERE id = ?", (office_id,)).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()


# หมายเหตุ: ไม่ให้แก้ "code" ผ่านช่องทางนี้ เพราะรหัสสำนักงานถูกฝังอยู่ในเลข รว.12 ของเรื่องที่ออกไปแล้ว
# (ดู services/case_code.py) การเปลี่ยนรหัสทีหลังจะทำให้เลขเก่ากับสำนักงานไม่ตรงกันอีกต่อไป
UPDATABLE_OFFICE_FIELDS = {"name", "province", "district", "address", "is_active"}


@bp.patch("/<office_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN)
def update_office(office_id):
    payload = request.get_json(silent=True) or {}
    fields = {k: v for k, v in payload.items() if k in UPDATABLE_OFFICE_FIELDS}
    if not fields:
        return err("ไม่มีข้อมูลที่จะอัปเดต")
    if "name" in fields and not fields["name"]:
        return err("ต้องระบุชื่อสำนักงาน")
    if "province" in fields and not fields["province"]:
        return err("ต้องระบุจังหวัด")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM offices WHERE id = ?", (office_id,)).fetchone()
        if existing is None:
            return err("ไม่พบสำนักงานที่ระบุ", 404)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE offices SET {set_clause}, updated_at = ? WHERE id = ?",
            list(fields.values()) + [now_iso(), office_id],
        )
        conn.commit()
        log_action(conn, g.current_user["id"], "UPDATE", "offices", office_id, before=dict(existing), after=fields)

        row = conn.execute("SELECT * FROM offices WHERE id = ?", (office_id,)).fetchone()
        return ok(dict(row))
    finally:
        conn.close()
