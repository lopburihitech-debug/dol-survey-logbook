"""
ความปลอดภัย: การเข้ารหัสรหัสผ่าน (PBKDF2-SHA256 จาก stdlib hashlib), การออก/ตรวจสอบ JWT (PyJWT),
และ decorator สำหรับ RBAC — ตามหัวข้อ 6 (Security & PDPA) ของ System Blueprint v2.0
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, jsonify, request

from config import settings
from db import get_connection

PBKDF2_ITERATIONS = 260_000

# ป้องกันการสุ่มเดารหัสผ่าน (brute force) — ล็อกบัญชีชั่วคราวถ้าใส่รหัสผ่านผิดติดต่อกันเกินจำนวนนี้
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, hex_digest = stored_hash.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return secrets.compare_digest(digest.hex(), hex_digest)


def is_locked_out(user) -> bool:
    """เช็คว่าบัญชีนี้ยังอยู่ในช่วงถูกล็อกชั่วคราวอยู่หรือไม่ (จากการใส่รหัสผ่านผิดติดต่อกันเกินกำหนด)"""
    lockout_until = user["lockout_until"]
    if not lockout_until:
        return False
    try:
        until = datetime.fromisoformat(lockout_until)
    except (ValueError, TypeError):
        return False
    return datetime.now(timezone.utc) < until


def lockout_remaining_minutes(user) -> int:
    """จำนวนนาทีที่เหลือก่อนบัญชีจะถูกปลดล็อกอัตโนมัติ (ปัดขึ้นอย่างน้อย 1 นาทีเสมอ ตราบใดที่ยังล็อกอยู่)"""
    try:
        until = datetime.fromisoformat(user["lockout_until"])
    except (ValueError, TypeError):
        return LOCKOUT_MINUTES
    remaining_seconds = (until - datetime.now(timezone.utc)).total_seconds()
    return max(1, round(remaining_seconds / 60))


def record_failed_login(conn, user) -> bool:
    """นับจำนวนครั้งที่ใส่รหัสผ่านผิดของบัญชีนี้ +1 ถ้าถึง MAX_FAILED_LOGIN_ATTEMPTS ให้ล็อกบัญชีชั่วคราว
    LOCKOUT_MINUTES นาที คืนค่า True ถ้าครั้งนี้เป็นครั้งที่ทำให้บัญชีถูกล็อกพอดี (ให้ผู้เรียกไป log audit ต่อได้)
    """
    attempts = (user["failed_login_attempts"] or 0) + 1
    just_locked = attempts >= MAX_FAILED_LOGIN_ATTEMPTS
    if just_locked:
        lockout_until = (datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
        conn.execute(
            "UPDATE users SET failed_login_attempts = ?, lockout_until = ? WHERE id = ?",
            (attempts, lockout_until, user["id"]),
        )
    else:
        conn.execute("UPDATE users SET failed_login_attempts = ? WHERE id = ?", (attempts, user["id"]))
    conn.commit()
    return just_locked


def reset_failed_login(conn, user_id: str) -> None:
    """เคลียร์ตัวนับ/สถานะล็อก — เรียกทุกครั้งที่ล็อกอินด้วยรหัสผ่านถูกต้อง (แม้จะยังต้องผ่าน 2FA ต่อก็ตาม)"""
    conn.execute("UPDATE users SET failed_login_attempts = 0, lockout_until = NULL WHERE id = ?", (user_id,))
    conn.commit()


def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "role": role, "type": "access", "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": user_id, "type": "refresh", "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def create_mfa_pending_token(user_id: str) -> str:
    """โทเคนชั่วคราวอายุสั้นมาก (5 นาที) ออกให้หลังตรวจ username/password ผ่านแล้วแต่ยังไม่ยืนยันรหัส 2FA
    ใช้ได้เฉพาะกับ endpoint /auth/login/verify เท่านั้น — login_required (ที่ใช้กับทุก endpoint อื่น) จะปฏิเสธ
    โทเคนชนิดนี้เสมอเพราะเช็ค type ต้องเป็น "access" เท่านั้น จึงไม่สามารถใช้แทน token จริงเรียก API อื่นได้
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    payload = {"sub": user_id, "type": "mfa_pending", "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])


def _extract_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return None


def login_required(f):
    """ตรวจสอบ JWT access token และโหลดผู้ใช้ปัจจุบันเข้า flask.g.current_user"""

    @wraps(f)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": {"message": "ต้องแนบ Authorization: Bearer <token>"}}), 401
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": {"message": "Token หมดอายุ กรุณาเข้าสู่ระบบใหม่"}}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": {"message": "Token ไม่ถูกต้อง"}}), 401

        if payload.get("type") != "access":
            return jsonify({"error": {"message": "ต้องใช้ access token"}}), 401

        conn = get_connection()
        try:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (payload["sub"],)).fetchone()
        finally:
            conn.close()

        if user is None or not user["is_active"]:
            return jsonify({"error": {"message": "ไม่พบผู้ใช้หรือบัญชีถูกปิดใช้งาน"}}), 401

        g.current_user = dict(user)
        return f(*args, **kwargs)

    return wrapper


def require_roles(*allowed_roles):
    """RBAC decorator: ใช้ร่วมกับ @login_required เท่านั้น
    ตัวอย่าง: @require_roles("system_admin", "administrator")
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            current_user = g.get("current_user")
            if current_user is None:
                return jsonify({"error": {"message": "ต้องเข้าสู่ระบบก่อน"}}), 401
            if current_user["role"] not in allowed_roles:
                return (
                    jsonify({"error": {"message": f"บทบาท '{current_user['role']}' ไม่มีสิทธิ์เข้าถึงส่วนนี้"}}),
                    403,
                )
            return f(*args, **kwargs)

        return wrapper

    return decorator
