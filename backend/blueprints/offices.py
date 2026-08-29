from flask import Blueprint, g, request

from constants import Role
from db import get_connection
from helpers import err, new_id, now_iso, ok
from security import login_required, require_roles
from services.audit import log_action

bp = Blueprint("offices", __name__, url_prefix="/api/v1/offices")


def _parse_coord(value, lo, hi):
    """แปลงค่าพิกัด (lat/lng) จาก payload ให้เป็น float หรือ None — คืน (ok, value_or_error_message) เพื่อแยกกรณี
    'ไม่ได้ส่งมา' (ไม่แตะต้องค่าเดิม) ออกจาก 'ส่งมาเป็นค่าว่าง' (ล้างพิกัดเดิมทิ้ง) ออกจาก 'ส่งมาแต่ผิดรูปแบบ/นอกช่วง'"""
    if value is None or value == "":
        return True, None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False, "พิกัดต้องเป็นตัวเลข"
    if not (lo <= f <= hi):
        return False, f"ค่าต้องอยู่ระหว่าง {lo} ถึง {hi}"
    return True, f


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

    lat_ok, lat = _parse_coord(payload.get("lat"), -90, 90)
    if not lat_ok:
        return err(f"ละติจูดไม่ถูกต้อง: {lat}")
    lng_ok, lng = _parse_coord(payload.get("lng"), -180, 180)
    if not lng_ok:
        return err(f"ลองจิจูดไม่ถูกต้อง: {lng}")

    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM offices WHERE code = ?", (payload["code"],)).fetchone():
            return err("รหัสสำนักงานนี้ถูกใช้งานแล้ว", 409)

        office_id = new_id()
        ts = now_iso()
        conn.execute(
            """INSERT INTO offices (id, code, name, province, district, address, lat, lng, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (office_id, payload["code"], payload["name"], payload["province"], payload.get("district"), payload.get("address"), lat, lng, ts, ts),
        )
        conn.commit()
        log_action(conn, g.current_user["id"], "CREATE", "offices", office_id, after={"code": payload["code"], "name": payload["name"]})
        row = conn.execute("SELECT * FROM offices WHERE id = ?", (office_id,)).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()


# หมายเหตุ: ไม่ให้แก้ "code" ผ่านช่องทางนี้ เพราะรหัสสำนักงานถูกฝังอยู่ในเลข รว.12 ของเรื่องที่ออกไปแล้ว
# (ดู services/case_code.py) การเปลี่ยนรหัสทีหลังจะทำให้เลขเก่ากับสำนักงานไม่ตรงกันอีกต่อไป
UPDATABLE_OFFICE_FIELDS = {"name", "province", "district", "address", "lat", "lng", "is_active"}


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
    if "lat" in fields:
        lat_ok, lat = _parse_coord(fields["lat"], -90, 90)
        if not lat_ok:
            return err(f"ละติจูดไม่ถูกต้อง: {lat}")
        fields["lat"] = lat
    if "lng" in fields:
        lng_ok, lng = _parse_coord(fields["lng"], -180, 180)
        if not lng_ok:
            return err(f"ลองจิจูดไม่ถูกต้อง: {lng}")
        fields["lng"] = lng

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
