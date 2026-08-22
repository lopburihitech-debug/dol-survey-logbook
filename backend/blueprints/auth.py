from flask import Blueprint, g, jsonify, request

from db import get_connection
from helpers import err, new_id, now_iso, ok
from security import (
    create_access_token,
    create_mfa_pending_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_locked_out,
    lockout_remaining_minutes,
    login_required,
    record_failed_login,
    reset_failed_login,
    verify_password,
)
from services.audit import log_action
from services.qr import generate_qr_data_uri
from services.totp import (
    generate_backup_codes,
    generate_secret,
    hash_backup_code,
    provisioning_uri,
    verify_totp,
)
import jwt as pyjwt

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


@bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    password = payload.get("password")
    if not username or not password:
        return err("ต้องระบุ username และ password")

    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        # เช็คว่าบัญชีนี้ถูกล็อกชั่วคราวอยู่หรือไม่ก่อนตรวจรหัสผ่านด้วยซ้ำ (ป้องกันการสุ่มเดารหัสผ่านต่อ)
        if user is not None and is_locked_out(user):
            remaining = lockout_remaining_minutes(user)
            return err(
                f"บัญชีนี้ถูกล็อกชั่วคราวเนื่องจากใส่รหัสผ่านผิดหลายครั้งเกินไป กรุณาลองใหม่อีกครั้งในอีกประมาณ {remaining} นาที",
                429,
            )

        if user is None or not verify_password(password, user["password_hash"]):
            if user is not None:
                just_locked = record_failed_login(conn, user)
                if just_locked:
                    log_action(conn, user["id"], "LOGIN_LOCKED", "users", user["id"], ip_address=request.remote_addr)
            return err("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", 401)
        if not user["is_active"]:
            return err("บัญชีนี้ถูกปิดใช้งาน", 403)

        reset_failed_login(conn, user["id"])  # รหัสผ่านถูกต้อง -> เคลียร์ตัวนับ (แม้ยังต้องผ่าน 2FA ต่อก็ตาม)

        if user["mfa_enabled"]:
            # ผ่านรหัสผ่านแล้ว แต่ยังไม่ออก access/refresh token จริงจนกว่าจะยืนยันรหัส 2FA สำเร็จ
            mfa_token = create_mfa_pending_token(user["id"])
            return ok({"mfa_required": True, "mfa_token": mfa_token})

        access_token = create_access_token(user["id"], user["role"])
        refresh_token = create_refresh_token(user["id"])
        log_action(conn, user["id"], "LOGIN", "users", user["id"], ip_address=request.remote_addr)

        return ok({"mfa_required": False, "access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"})
    finally:
        conn.close()


@bp.post("/login/verify")
def login_verify():
    """ขั้นตอนที่ 2 ของการเข้าสู่ระบบ — ตรวจรหัส 2FA (จากแอปยืนยันตัวตน หรือรหัสสำรอง) แล้วออก token จริง"""
    payload = request.get_json(silent=True) or {}
    mfa_token = payload.get("mfa_token")
    code = (payload.get("code") or "").strip()
    if not mfa_token or not code:
        return err("ต้องระบุ mfa_token และ code")

    try:
        data = decode_token(mfa_token)
    except pyjwt.ExpiredSignatureError:
        return err("หมดเวลายืนยัน 2FA กรุณาเข้าสู่ระบบใหม่อีกครั้ง", 401)
    except pyjwt.InvalidTokenError:
        return err("mfa_token ไม่ถูกต้อง", 401)

    if data.get("type") != "mfa_pending":
        return err("token ไม่ถูกประเภท", 401)

    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (data["sub"],)).fetchone()
        if user is None or not user["is_active"] or not user["mfa_enabled"] or not user["mfa_secret"]:
            return err("ไม่พบผู้ใช้ หรือบัญชีนี้ไม่ได้เปิดใช้งาน 2FA อยู่", 401)

        verified = verify_totp(user["mfa_secret"], code)
        if not verified:
            verified = _try_consume_backup_code(conn, user["id"], code)

        if not verified:
            return err("รหัสยืนยันไม่ถูกต้อง", 401)

        access_token = create_access_token(user["id"], user["role"])
        refresh_token = create_refresh_token(user["id"])
        log_action(conn, user["id"], "LOGIN", "users", user["id"], ip_address=request.remote_addr)

        return ok({"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"})
    finally:
        conn.close()


def _try_consume_backup_code(conn, user_id: str, code: str) -> bool:
    code_hash = hash_backup_code(code)
    row = conn.execute(
        "SELECT id FROM mfa_backup_codes WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
        (user_id, code_hash),
    ).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE mfa_backup_codes SET used_at = ? WHERE id = ?", (now_iso(), row["id"]))
    conn.commit()
    return True


@bp.post("/refresh")
def refresh():
    payload = request.get_json(silent=True) or {}
    token = payload.get("refresh_token")
    if not token:
        return err("ต้องระบุ refresh_token")
    try:
        data = decode_token(token)
    except pyjwt.ExpiredSignatureError:
        return err("refresh token หมดอายุ กรุณาเข้าสู่ระบบใหม่", 401)
    except pyjwt.InvalidTokenError:
        return err("refresh token ไม่ถูกต้อง", 401)

    if data.get("type") != "refresh":
        return err("ต้องใช้ refresh token", 401)

    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (data["sub"],)).fetchone()
        if user is None or not user["is_active"]:
            return err("ไม่พบผู้ใช้หรือบัญชีถูกปิดใช้งาน", 401)
        access_token = create_access_token(user["id"], user["role"])
        new_refresh = create_refresh_token(user["id"])
        return ok({"access_token": access_token, "refresh_token": new_refresh, "token_type": "bearer"})
    finally:
        conn.close()


@bp.post("/logout")
@login_required
def logout():
    # หมายเหตุ: ใช้ short-lived access token (15 นาที) แบบ stateless
    # หากต้องการ revoke token ทันทีในระยะถัดไป ให้เพิ่ม token blacklist (เช่น Redis)
    return ok({"message": "ออกจากระบบสำเร็จ กรุณาลบ token ที่ฝั่ง client"})


@bp.get("/me")
@login_required
def me():
    user = dict(g.current_user)
    user.pop("password_hash", None)
    user.pop("mfa_secret", None)
    return ok(user)


@bp.post("/change-password")
@login_required
def change_password():
    """เปลี่ยนรหัสผ่านของบัญชีตัวเอง — ต้องกรอกรหัสผ่านเดิมให้ถูกต้องก่อน (ต่างจาก /users/<id>/reset-password
    ซึ่งเป็นสิทธิ์ system_admin ตั้งรหัสผ่านใหม่ให้ผู้ใช้ "คนอื่น" โดยไม่ต้องรู้รหัสผ่านเดิม)"""
    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password") or ""
    new_password = payload.get("new_password") or ""
    user = g.current_user

    if len(new_password) < 8:
        return err("รหัสผ่านใหม่ต้องมีความยาวอย่างน้อย 8 ตัวอักษร")

    conn = get_connection()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None or not verify_password(current_password, row["password_hash"]):
            # หมายเหตุ: ใช้ 403 ไม่ใช่ 401 เพราะผู้ใช้ล็อกอินอยู่แล้ว (มี access token ที่ถูกต้อง) เพียงแค่ยืนยัน
            # รหัสผ่านเดิมไม่ผ่าน — ถ้าใช้ 401 ตัว apiFetch ฝั่ง frontend จะเข้าใจผิดว่า token หมดอายุแล้วเด้งออกจากระบบ
            return err("รหัสผ่านเดิมไม่ถูกต้อง", 403)

        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), now_iso(), user["id"]),
        )
        conn.commit()
        log_action(conn, user["id"], "CHANGE_PASSWORD", "users", user["id"])
        return ok({"message": "เปลี่ยนรหัสผ่านสำเร็จ"})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ยืนยันตัวตนสองชั้น (2FA) — จัดการบัญชีตัวเอง (ทุกบทบาทเปิดใช้งานให้ตัวเองได้)
# ---------------------------------------------------------------------------
@bp.post("/mfa/setup")
@login_required
def mfa_setup():
    """เริ่มตั้งค่า 2FA — สร้าง secret ใหม่ (ยังไม่เปิดใช้งานจริงจนกว่าจะยืนยันรหัสถูกต้องผ่าน /mfa/enable)"""
    user = g.current_user
    if user["mfa_enabled"]:
        return err("บัญชีนี้เปิดใช้งาน 2FA อยู่แล้ว กรุณาปิดใช้งานก่อนตั้งค่าใหม่")

    secret = generate_secret()
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET mfa_secret = ?, updated_at = ? WHERE id = ?", (secret, now_iso(), user["id"]))
        conn.commit()
    finally:
        conn.close()

    uri = provisioning_uri(secret, user["username"])
    qr_data_uri = generate_qr_data_uri(uri)
    return ok({"secret": secret, "otpauth_uri": uri, "qr_data_uri": qr_data_uri})


@bp.post("/mfa/enable")
@login_required
def mfa_enable():
    """ยืนยันรหัส 6 หลักจากแอปเป็นครั้งแรก -> เปิดใช้งาน 2FA จริง พร้อมออกรหัสสำรองให้ครั้งเดียว"""
    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or "").strip()
    user = g.current_user

    if not user["mfa_secret"]:
        return err("ยังไม่ได้เริ่มตั้งค่า 2FA — กรุณาเรียก /mfa/setup ก่อน")
    if not verify_totp(user["mfa_secret"], code):
        return err("รหัสยืนยันไม่ถูกต้อง กรุณาลองใหม่")

    backup_codes = generate_backup_codes()
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET mfa_enabled = 1, updated_at = ? WHERE id = ?", (now_iso(), user["id"]))
        conn.execute("DELETE FROM mfa_backup_codes WHERE user_id = ?", (user["id"],))
        ts = now_iso()
        for code_str in backup_codes:
            conn.execute(
                "INSERT INTO mfa_backup_codes (id, user_id, code_hash, used_at, created_at) VALUES (?, ?, ?, NULL, ?)",
                (new_id(), user["id"], hash_backup_code(code_str), ts),
            )
        conn.commit()
        log_action(conn, user["id"], "MFA_ENABLE", "users", user["id"])
    finally:
        conn.close()

    return ok({"message": "เปิดใช้งาน 2FA สำเร็จ", "backup_codes": backup_codes})


@bp.post("/mfa/disable")
@login_required
def mfa_disable():
    """ปิดใช้งาน 2FA ของบัญชีตัวเอง — ต้องกรอกรหัสผ่านซ้ำเพื่อยืนยันตัวตนก่อน (กันคนอื่นแอบปิดจากเซสชันที่ค้างอยู่)"""
    payload = request.get_json(silent=True) or {}
    password = payload.get("password") or ""
    user = g.current_user

    conn = get_connection()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            # ใช้ 403 (ไม่ใช่ 401) ด้วยเหตุผลเดียวกับ /auth/change-password — ผู้ใช้ล็อกอินอยู่แล้ว ไม่ใช่ token หมดอายุ
            return err("รหัสผ่านไม่ถูกต้อง", 403)

        conn.execute(
            "UPDATE users SET mfa_enabled = 0, mfa_secret = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), user["id"]),
        )
        conn.execute("DELETE FROM mfa_backup_codes WHERE user_id = ?", (user["id"],))
        conn.commit()
        log_action(conn, user["id"], "MFA_DISABLE", "users", user["id"])
        return ok({"message": "ปิดใช้งาน 2FA สำเร็จ"})
    finally:
        conn.close()
