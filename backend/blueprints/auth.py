import json
from datetime import datetime, timedelta, timezone

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
import services.webauthn as webauthn_service
import jwt as pyjwt

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")

# PIN สำหรับล็อกอิน — ตัวเลข 6 หลักเสมอ (สั้นกว่ารหัสผ่านมาก จึงกันเดาง่ายด้วยรายการนี้ + lockout เดียวกับรหัสผ่าน
# ไม่ครอบคลุมทุกแพทเทิร์นที่เดาง่าย แต่กันกรณีตั้งค่าไม่ระวังที่พบบ่อยที่สุดไว้ก่อน)
_WEAK_PINS = {
    "000000", "111111", "222222", "333333", "444444", "555555", "666666", "777777", "888888", "999999",
    "123456", "654321", "123123", "121212", "112233",
}


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
    # ไม่ส่ง pin_hash ออกไปตรงๆ (เป็นค่าแฮชก็จริง แต่ไม่มีเหตุผลต้องให้ frontend เห็น) — ส่งแค่ true/false ว่าตั้งไว้หรือยัง
    user["pin_enabled"] = bool(user.pop("pin_hash", None))
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


# ---------------------------------------------------------------------------
# ตัวช่วยร่วม — ให้ทุกวิธีล็อกอิน (รหัสผ่าน/PIN/ลายนิ้วมือ-ใบหน้า) มีพฤติกรรม "หลังยืนยันตัวตนขั้นแรกสำเร็จ
# แล้ว" และ "ยืนยันขั้นแรกไม่ผ่าน" เหมือนกันทุกเส้นทาง (เช็ค 2FA เดียวกัน, นับล็อกอินผิด/lockout ร่วมกัน)
# หมายเหตุ: /login (รหัสผ่าน) เขียนไว้ก่อนหน้านี้แล้วและทำงานถูกต้องอยู่แล้ว จึงไม่แตะโค้ดเดิมส่วนนั้น เพื่อไม่ให้
# กระทบเส้นทางล็อกอินหลักที่ผู้ใช้ทุกคนพึ่งพาอยู่ — ตัวช่วยนี้ใช้เฉพาะกับ 2 วิธีใหม่ (PIN, WebAuthn) เท่านั้น
# ---------------------------------------------------------------------------
def _complete_credential_login(conn, user):
    reset_failed_login(conn, user["id"])
    if user["mfa_enabled"]:
        mfa_token = create_mfa_pending_token(user["id"])
        return ok({"mfa_required": True, "mfa_token": mfa_token})

    access_token = create_access_token(user["id"], user["role"])
    refresh_token = create_refresh_token(user["id"])
    log_action(conn, user["id"], "LOGIN", "users", user["id"], ip_address=request.remote_addr)
    return ok({"mfa_required": False, "access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"})


def _reject_failed_credential(conn, user):
    if user is not None:
        just_locked = record_failed_login(conn, user)
        if just_locked:
            log_action(conn, user["id"], "LOGIN_LOCKED", "users", user["id"], ip_address=request.remote_addr)


# ---------------------------------------------------------------------------
# เข้าสู่ระบบด้วย PIN — ทางเลือกเสริมควบคู่กับรหัสผ่าน (ไม่ได้แทนที่) เหมาะกับการใช้งานบนมือถือภาคสนามที่พิมพ์
# รหัสผ่านยาวๆ ทุกครั้งไม่สะดวก ใช้ตัวนับ/lockout ป้องกันการเดาร่วมกับรหัสผ่าน (บัญชีเดียวกัน นับรวมกัน)
# ---------------------------------------------------------------------------
@bp.post("/login-pin")
def login_pin():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    pin = (payload.get("pin") or "").strip()
    if not username or not pin:
        return err("ต้องระบุ username และ pin")

    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if user is not None and is_locked_out(user):
            remaining = lockout_remaining_minutes(user)
            return err(
                f"บัญชีนี้ถูกล็อกชั่วคราวเนื่องจากใส่รหัสผิดหลายครั้งเกินไป กรุณาลองใหม่อีกครั้งในอีกประมาณ {remaining} นาที",
                429,
            )

        if user is None or not user["pin_hash"] or not verify_password(pin, user["pin_hash"]):
            _reject_failed_credential(conn, user)
            return err("ชื่อผู้ใช้หรือ PIN ไม่ถูกต้อง", 401)
        if not user["is_active"]:
            return err("บัญชีนี้ถูกปิดใช้งาน", 403)

        return _complete_credential_login(conn, user)
    finally:
        conn.close()


@bp.post("/pin/set")
@login_required
def pin_set():
    """ตั้งค่า/เปลี่ยน PIN ของบัญชีตัวเอง — ต้องกรอกรหัสผ่านเดิมให้ถูกต้องก่อนเสมอ (รหัสผ่านเป็นความลับที่แข็งแรง
    กว่า PIN จึงใช้ยืนยันตัวตนก่อนอนุญาตให้ตั้ง/เปลี่ยน PIN ได้ ต่างจาก reset-password ของ system_admin)"""
    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password") or ""
    pin = (payload.get("pin") or "").strip()
    pin_confirm = (payload.get("pin_confirm") or "").strip()
    user = g.current_user

    if not pin.isdigit() or len(pin) != 6:
        return err("PIN ต้องเป็นตัวเลข 6 หลักเท่านั้น")
    if pin != pin_confirm:
        return err("PIN ทั้งสองช่องไม่ตรงกัน")
    if len(set(pin)) == 1 or pin in _WEAK_PINS:
        return err("PIN นี้เดาง่ายเกินไป กรุณาเลือกชุดตัวเลขอื่น")

    conn = get_connection()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None or not verify_password(current_password, row["password_hash"]):
            return err("รหัสผ่านเดิมไม่ถูกต้อง", 403)

        conn.execute(
            "UPDATE users SET pin_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(pin), now_iso(), user["id"]),
        )
        conn.commit()
        log_action(conn, user["id"], "PIN_SET", "users", user["id"])
        return ok({"message": "ตั้งค่า PIN สำเร็จ"})
    finally:
        conn.close()


@bp.post("/pin/disable")
@login_required
def pin_disable():
    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password") or ""
    user = g.current_user

    conn = get_connection()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None or not verify_password(current_password, row["password_hash"]):
            return err("รหัสผ่านเดิมไม่ถูกต้อง", 403)

        conn.execute("UPDATE users SET pin_hash = NULL, updated_at = ? WHERE id = ?", (now_iso(), user["id"]))
        conn.commit()
        log_action(conn, user["id"], "PIN_DISABLE", "users", user["id"])
        return ok({"message": "ปิดใช้งาน PIN สำเร็จ"})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# เข้าสู่ระบบด้วยลายนิ้วมือ/ใบหน้า (WebAuthn / Passkey) — ทางเลือกเสริม ดู services/webauthn.py สำหรับ
# รายละเอียดว่าทำไมข้อมูลไบโอเมตริกซ์จริงไม่เคยผ่าน server เลย
# ---------------------------------------------------------------------------
_WEBAUTHN_CHALLENGE_TTL_SECONDS = webauthn_service.CHALLENGE_TTL_SECONDS


def _store_webauthn_challenge(conn, user_id: str, purpose: str, challenge: bytes) -> None:
    # ลบ challenge ค้างเก่าของ user+purpose นี้ทิ้งก่อนเสมอ (กันสะสม + กันใช้ challenge เก่าซ้ำ)
    conn.execute("DELETE FROM webauthn_challenges WHERE user_id = ? AND purpose = ?", (user_id, purpose))
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=_WEBAUTHN_CHALLENGE_TTL_SECONDS)
    conn.execute(
        "INSERT INTO webauthn_challenges (id, user_id, purpose, challenge, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), user_id, purpose, webauthn_service.b64url_encode(challenge), now.isoformat(), expires.isoformat()),
    )
    conn.commit()


def _consume_webauthn_challenge(conn, user_id: str, purpose: str):
    """ดึง challenge ล่าสุดของ user+purpose นี้มาใช้ครั้งเดียวแล้วลบทิ้งทันที (กัน replay) คืน None ถ้าไม่พบ/หมดอายุ"""
    row = conn.execute(
        "SELECT * FROM webauthn_challenges WHERE user_id = ? AND purpose = ? ORDER BY created_at DESC LIMIT 1",
        (user_id, purpose),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM webauthn_challenges WHERE id = ?", (row["id"],))
    conn.commit()
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    if datetime.now(timezone.utc) > expires_at:
        return None
    return webauthn_service.b64url_decode(row["challenge"])


@bp.post("/webauthn/register/options")
@login_required
def webauthn_register_options():
    user = g.current_user
    rp_id, _origin = webauthn_service.rp_id_and_origin_from_request(request)
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT credential_id FROM webauthn_credentials WHERE user_id = ?", (user["id"],)
        ).fetchall()
        challenge = webauthn_service.generate_challenge()
        _store_webauthn_challenge(conn, user["id"], "register", challenge)
        options = webauthn_service.build_registration_options(
            rp_id=rp_id,
            rp_name="DOL Survey Logbook",
            user_id=user["id"],
            username=user["username"],
            display_name=user["full_name"],
            challenge=challenge,
            exclude_credential_ids=[row["credential_id"] for row in existing],
        )
        return ok(options)
    finally:
        conn.close()


@bp.post("/webauthn/register/verify")
@login_required
def webauthn_register_verify():
    payload = request.get_json(silent=True) or {}
    credential = payload.get("credential") or {}
    device_label = (payload.get("device_label") or "").strip()[:100] or None
    user = g.current_user

    response = credential.get("response") or {}
    client_data_json_b64 = response.get("clientDataJSON")
    attestation_object_b64 = response.get("attestationObject")
    if not client_data_json_b64 or not attestation_object_b64:
        return err("ข้อมูลจากอุปกรณ์ไม่ครบถ้วน")

    conn = get_connection()
    try:
        challenge = _consume_webauthn_challenge(conn, user["id"], "register")
        if challenge is None:
            return err("คำขอลงทะเบียนหมดอายุแล้ว กรุณาลองใหม่")

        rp_id, origin = webauthn_service.rp_id_and_origin_from_request(request)
        try:
            result = webauthn_service.verify_registration_response(
                client_data_json_b64=client_data_json_b64,
                attestation_object_b64=attestation_object_b64,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
            )
        except webauthn_service.WebAuthnError as e:
            return err(str(e))

        dup = conn.execute(
            "SELECT id FROM webauthn_credentials WHERE credential_id = ?", (result["credential_id"],)
        ).fetchone()
        if dup is not None:
            return err("อุปกรณ์นี้ลงทะเบียนไว้กับระบบแล้ว")

        ts = now_iso()
        cred_row_id = new_id()
        conn.execute(
            """INSERT INTO webauthn_credentials
               (id, user_id, credential_id, public_key_json, sign_count, device_label, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                cred_row_id, user["id"], result["credential_id"], json.dumps(result["public_key"]),
                result["sign_count"], device_label, ts,
            ),
        )
        conn.commit()
        log_action(conn, user["id"], "WEBAUTHN_REGISTER", "users", user["id"])
        return ok({"message": "ลงทะเบียนอุปกรณ์สำเร็จ", "id": cred_row_id, "device_label": device_label})
    finally:
        conn.close()


@bp.get("/webauthn/credentials")
@login_required
def webauthn_list_credentials():
    user = g.current_user
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, device_label, created_at, last_used_at FROM webauthn_credentials WHERE user_id = ? ORDER BY created_at",
            (user["id"],),
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.delete("/webauthn/credentials/<credential_row_id>")
@login_required
def webauthn_delete_credential(credential_row_id):
    user = g.current_user
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM webauthn_credentials WHERE id = ? AND user_id = ?", (credential_row_id, user["id"])
        ).fetchone()
        if row is None:
            return err("ไม่พบอุปกรณ์นี้", 404)
        conn.execute("DELETE FROM webauthn_credentials WHERE id = ?", (credential_row_id,))
        conn.commit()
        log_action(conn, user["id"], "WEBAUTHN_REMOVE", "users", user["id"])
        return ok({"message": "ลบอุปกรณ์สำเร็จ"})
    finally:
        conn.close()


@bp.post("/webauthn/authenticate/options")
def webauthn_authenticate_options():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    if not username:
        return err("ต้องระบุ username")

    generic_not_found = "ไม่พบผู้ใช้นี้ หรือยังไม่ได้ตั้งค่าลายนิ้วมือ/ใบหน้าสำหรับบัญชีนี้"
    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None:
            return err(generic_not_found, 404)
        if is_locked_out(user):
            remaining = lockout_remaining_minutes(user)
            return err(
                f"บัญชีนี้ถูกล็อกชั่วคราวเนื่องจากใส่รหัสผิดหลายครั้งเกินไป กรุณาลองใหม่อีกครั้งในอีกประมาณ {remaining} นาที",
                429,
            )

        creds = conn.execute(
            "SELECT credential_id FROM webauthn_credentials WHERE user_id = ?", (user["id"],)
        ).fetchall()
        if not creds:
            return err(generic_not_found, 404)

        rp_id, _origin = webauthn_service.rp_id_and_origin_from_request(request)
        challenge = webauthn_service.generate_challenge()
        _store_webauthn_challenge(conn, user["id"], "authenticate", challenge)
        options = webauthn_service.build_authentication_options(
            rp_id=rp_id,
            challenge=challenge,
            allow_credential_ids=[c["credential_id"] for c in creds],
        )
        return ok(options)
    finally:
        conn.close()


@bp.post("/webauthn/authenticate/verify")
def webauthn_authenticate_verify():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    credential = payload.get("credential") or {}
    if not username or not credential:
        return err("ต้องระบุ username และข้อมูลจากอุปกรณ์")

    response = credential.get("response") or {}
    client_data_json_b64 = response.get("clientDataJSON")
    authenticator_data_b64 = response.get("authenticatorData")
    signature_b64 = response.get("signature")
    raw_id = credential.get("id")
    if not client_data_json_b64 or not authenticator_data_b64 or not signature_b64 or not raw_id:
        return err("ข้อมูลจากอุปกรณ์ไม่ครบถ้วน")

    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None:
            return err("ชื่อผู้ใช้หรืออุปกรณ์ไม่ถูกต้อง", 401)
        if is_locked_out(user):
            remaining = lockout_remaining_minutes(user)
            return err(
                f"บัญชีนี้ถูกล็อกชั่วคราวเนื่องจากใส่รหัสผิดหลายครั้งเกินไป กรุณาลองใหม่อีกครั้งในอีกประมาณ {remaining} นาที",
                429,
            )

        cred_row = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE user_id = ? AND credential_id = ?",
            (user["id"], raw_id),
        ).fetchone()
        if cred_row is None:
            _reject_failed_credential(conn, user)
            return err("ไม่พบอุปกรณ์นี้ที่ลงทะเบียนไว้กับบัญชีนี้", 401)

        challenge = _consume_webauthn_challenge(conn, user["id"], "authenticate")
        if challenge is None:
            return err("คำขอยืนยันตัวตนหมดอายุแล้ว กรุณาลองใหม่")

        rp_id, origin = webauthn_service.rp_id_and_origin_from_request(request)
        try:
            new_sign_count = webauthn_service.verify_authentication_response(
                client_data_json_b64=client_data_json_b64,
                authenticator_data_b64=authenticator_data_b64,
                signature_b64=signature_b64,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                stored_public_key=json.loads(cred_row["public_key_json"]),
                stored_sign_count=cred_row["sign_count"],
            )
        except webauthn_service.WebAuthnError as e:
            _reject_failed_credential(conn, user)
            return err(str(e), 401)

        if not user["is_active"]:
            return err("บัญชีนี้ถูกปิดใช้งาน", 403)

        conn.execute(
            "UPDATE webauthn_credentials SET sign_count = ?, last_used_at = ? WHERE id = ?",
            (new_sign_count, now_iso(), cred_row["id"]),
        )
        conn.commit()
        return _complete_credential_login(conn, user)
    finally:
        conn.close()
