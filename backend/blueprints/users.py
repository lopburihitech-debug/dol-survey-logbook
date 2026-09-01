from flask import Blueprint, g, request

from constants import Role
from db import get_connection
from helpers import err, new_id, now_iso, ok
from security import hash_password, login_required, require_roles
from services.audit import log_action

bp = Blueprint("users", __name__, url_prefix="/api/v1/users")

UPDATABLE_FIELDS = {"full_name", "email", "phone", "role", "office_id", "is_active"}


def _strip_password(row: dict) -> dict:
    row.pop("password_hash", None)
    row.pop("mfa_secret", None)
    return row


# ผู้ดูแลระดับสาขา (BRANCH_USER_ADMIN) เข้าหน้านี้ได้เหมือน system_admin แต่ถูกจำกัดขอบเขตแคบมาก: เห็น/แก้ไขได้เฉพาะ
# บัญชีที่เป็น "ช่างรังวัด" (SURVEYOR) และอยู่สำนักงานเดียวกับตัวเองเท่านั้น — บังคับเงื่อนไขนี้ที่ backend ทุกจุด
# (ไม่พึ่งฝั่ง frontend ที่ซ่อน/ล็อกช่องกรอกไว้อย่างเดียว) กันไม่ให้เรียก API ตรงๆ ข้ามขอบเขตได้ ดู constants.py
# หัวข้อ BRANCH_USER_ADMIN สำหรับที่มา/เหตุผลของขอบเขตนี้
def _is_branch_user_admin(current_user: dict) -> bool:
    return current_user["role"] == Role.BRANCH_USER_ADMIN


def _in_branch_user_admin_scope(current_user: dict, target_row) -> bool:
    """เช็คว่าบัญชีเป้าหมาย (target_row) อยู่ในขอบเขตที่ผู้ดูแลระดับสาขาคนนี้จัดการได้ไหม — ต้องเป็นช่างรังวัด
    และอยู่สำนักงานเดียวกับตัวเองเท่านั้น"""
    return target_row["role"] == Role.SURVEYOR and target_row["office_id"] == current_user.get("office_id")


@bp.get("")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.BRANCH_USER_ADMIN)
def list_users():
    conn = get_connection()
    try:
        current_user = g.current_user
        if _is_branch_user_admin(current_user):
            rows = conn.execute(
                """SELECT u.*, o.name AS office_name
                   FROM users u
                   LEFT JOIN offices o ON o.id = u.office_id
                   WHERE u.role = ? AND u.office_id = ?
                   ORDER BY u.created_at DESC""",
                (Role.SURVEYOR, current_user.get("office_id")),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT u.*, o.name AS office_name
                   FROM users u
                   LEFT JOIN offices o ON o.id = u.office_id
                   ORDER BY u.created_at DESC"""
            ).fetchall()
        return ok([_strip_password(dict(r)) for r in rows])
    finally:
        conn.close()


@bp.post("")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.BRANCH_USER_ADMIN)
def create_user():
    payload = request.get_json(silent=True) or {}
    current_user = g.current_user

    if _is_branch_user_admin(current_user):
        # ผู้ดูแลระดับสาขา: สร้างได้เฉพาะบัญชีช่างรังวัดในสำนักงานตัวเองเท่านั้น — บังคับ role/office_id เป็นของ
        # ตัวเองเสมอ ไม่ว่าฝั่ง client จะส่งค่าอะไรมาก็ตาม (กันเรียก API ตรงๆ ข้ามขอบเขต) และถ้าไม่มี office_id ผูกกับ
        # บัญชีตัวเองอยู่ (ตั้งค่าไม่ครบ) ก็สร้างใครไม่ได้เลย ไม่ปล่อยให้ office_id เป็น NULL
        if not current_user.get("office_id"):
            return err("บัญชีนี้ยังไม่ได้ผูกสำนักงานไว้ กรุณาติดต่อผู้ดูแลระบบ")
        if payload.get("role") and payload["role"] != Role.SURVEYOR:
            return err("ผู้ดูแลระดับสาขาสร้างได้เฉพาะบัญชีช่างรังวัดเท่านั้น")
        payload = {**payload, "role": Role.SURVEYOR, "office_id": current_user["office_id"]}

    required = ["username", "password", "full_name", "role"]
    if not all(payload.get(f) for f in required):
        return err(f"ต้องระบุ {', '.join(required)}")
    if payload["role"] not in Role.ALL:
        return err(f"role ต้องเป็นหนึ่งใน {Role.ALL}")

    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (payload["username"],)).fetchone():
            return err("username นี้ถูกใช้งานแล้ว", 409)

        user_id = new_id()
        ts = now_iso()
        conn.execute(
            """INSERT INTO users (id, username, password_hash, full_name, email, phone, role, office_id,
                                   mfa_enabled, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)""",
            (
                user_id,
                payload["username"],
                hash_password(payload["password"]),
                payload["full_name"],
                payload.get("email"),
                payload.get("phone"),
                payload["role"],
                payload.get("office_id"),
                ts,
                ts,
            ),
        )
        conn.commit()
        log_action(conn, g.current_user["id"], "CREATE", "users", user_id, after={"role": payload["role"]})
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return ok(_strip_password(dict(row)), 201)
    finally:
        conn.close()


@bp.patch("/<user_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.BRANCH_USER_ADMIN)
def update_user(user_id):
    payload = request.get_json(silent=True) or {}
    current_user = g.current_user
    branch_scoped = _is_branch_user_admin(current_user)

    fields = {k: v for k, v in payload.items() if k in UPDATABLE_FIELDS}
    if branch_scoped:
        # ผู้ดูแลระดับสาขา: แก้ได้เฉพาะข้อมูลทั่วไป (ชื่อ/อีเมล/เบอร์โทร/สถานะใช้งาน) ห้ามเปลี่ยนบทบาทหรือย้าย
        # สำนักงานของใครเลย (กันเลื่อนสิทธิ์/ย้ายบัญชีออกนอกขอบเขตที่ตัวเองดูแลอยู่)
        fields.pop("role", None)
        fields.pop("office_id", None)
    if not fields:
        return err("ไม่มีข้อมูลที่จะอัปเดต")

    # กันไม่ให้ system_admin ปิดใช้งาน/ถอดสิทธิ์ผู้ดูแลระบบบัญชีตัวเอง เพราะจะทำให้ล็อกอินกลับเข้าระบบไม่ได้อีก
    if user_id == current_user["id"]:
        if fields.get("is_active") in (0, False, "0"):
            return err("ไม่สามารถปิดใช้งานบัญชีของตัวเองได้")
        if "role" in fields and fields["role"] != Role.SYSTEM_ADMIN:
            return err("ไม่สามารถเปลี่ยนบทบาทของตัวเองออกจากผู้ดูแลระบบได้")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if existing is None:
            return err("ไม่พบผู้ใช้", 404)
        if branch_scoped and not _in_branch_user_admin_scope(current_user, existing):
            return err("เรื่องนี้อยู่นอกเขตที่ท่านดูแล (จัดการได้เฉพาะบัญชีช่างรังวัดในสำนักงานตัวเองเท่านั้น)", 403)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [now_iso(), user_id]
        conn.execute(f"UPDATE users SET {set_clause}, updated_at = ? WHERE id = ?", params)
        conn.commit()
        log_action(conn, g.current_user["id"], "UPDATE", "users", user_id, before=_strip_password(dict(existing)), after=fields)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return ok(_strip_password(dict(row)))
    finally:
        conn.close()


@bp.post("/<user_id>/reset-password")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.BRANCH_USER_ADMIN)
def reset_password(user_id):
    """ตั้งรหัสผ่านใหม่ให้ผู้ใช้ — ใช้ตอนช่วยผู้ใช้ที่ลืมรหัสผ่าน หรือเปลี่ยนรหัสผ่านตัวอย่าง (seed) ก่อนใช้งานจริง"""
    payload = request.get_json(silent=True) or {}
    new_password = payload.get("new_password") or ""
    if len(new_password) < 8:
        return err("รหัสผ่านใหม่ต้องมีความยาวอย่างน้อย 8 ตัวอักษร")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT id, role, office_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if existing is None:
            return err("ไม่พบผู้ใช้", 404)
        if _is_branch_user_admin(g.current_user) and not _in_branch_user_admin_scope(g.current_user, existing):
            return err("เรื่องนี้อยู่นอกเขตที่ท่านดูแล (จัดการได้เฉพาะบัญชีช่างรังวัดในสำนักงานตัวเองเท่านั้น)", 403)

        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), now_iso(), user_id),
        )
        conn.commit()
        log_action(conn, g.current_user["id"], "RESET_PASSWORD", "users", user_id)
        return ok({"message": "ตั้งรหัสผ่านใหม่สำเร็จ"})
    finally:
        conn.close()


@bp.post("/<user_id>/mfa/disable")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.BRANCH_USER_ADMIN)
def admin_disable_mfa(user_id):
    """ปิดใช้งาน 2FA ให้ผู้ใช้คนอื่นแทน — ใช้กรณีทำอุปกรณ์ยืนยันตัวตนหาย/ใช้รหัสสำรองไม่ได้แล้วเข้าระบบไม่ได้เลย"""
    conn = get_connection()
    try:
        existing = conn.execute("SELECT id, role, office_id, mfa_enabled FROM users WHERE id = ?", (user_id,)).fetchone()
        if existing is None:
            return err("ไม่พบผู้ใช้", 404)
        if _is_branch_user_admin(g.current_user) and not _in_branch_user_admin_scope(g.current_user, existing):
            return err("เรื่องนี้อยู่นอกเขตที่ท่านดูแล (จัดการได้เฉพาะบัญชีช่างรังวัดในสำนักงานตัวเองเท่านั้น)", 403)
        if not existing["mfa_enabled"]:
            return err("บัญชีนี้ไม่ได้เปิดใช้งาน 2FA อยู่")

        conn.execute(
            "UPDATE users SET mfa_enabled = 0, mfa_secret = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), user_id),
        )
        conn.execute("DELETE FROM mfa_backup_codes WHERE user_id = ?", (user_id,))
        conn.commit()
        log_action(conn, g.current_user["id"], "MFA_DISABLE_BY_ADMIN", "users", user_id)
        return ok({"message": "ปิดใช้งาน 2FA ให้ผู้ใช้คนนี้แล้ว เข้าสู่ระบบด้วยรหัสผ่านตามปกติได้เลย"})
    finally:
        conn.close()
