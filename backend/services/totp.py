"""
การยืนยันตัวตนสองชั้น (2FA) แบบ TOTP (Time-based One-Time Password, RFC 6238)
เขียนเองด้วย stdlib ล้วน (hmac/hashlib/struct/base64/secrets) ไม่พึ่งไลบรารีภายนอก (เช่น pyotp)
เพราะ environment นี้ไม่มีอินเทอร์เน็ตออกไปโหลด package เพิ่มได้ — ตรวจสอบความถูกต้องแล้วด้วย
official test vectors จาก RFC 6238 Appendix B (ครบทั้ง 6 ค่า ผ่านหมด)

ใช้งานร่วมกับแอปยืนยันตัวตนมาตรฐานทั่วไปได้เลย เช่น Google Authenticator, Microsoft Authenticator,
Authy ฯลฯ (ค่าเริ่มต้น: SHA1, 6 หลัก, รอบ 30 วินาที ตรงตามที่แอปเหล่านี้ใช้เป็นมาตรฐาน)
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP_SECONDS = 30
VERIFY_WINDOW = 1  # ยอมรับรหัสจากรอบก่อนหน้า/ถัดไป ±1 รอบ (±30 วินาที) เผื่อนาฬิกาเครื่องคลาดเคลื่อนเล็กน้อย


def generate_secret() -> str:
    """สุ่ม secret ใหม่ 20 ไบต์ (160 บิต ตามมาตรฐาน) เข้ารหัสเป็น Base32 (ไม่มี padding) สำหรับกรอกในแอปยืนยันตัวตน"""
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _hotp(secret_bytes: bytes, counter: int, digits: int = DIGITS) -> str:
    msg = struct.pack(">Q", counter)
    h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code_int).zfill(digits)


def _decode_secret(secret_b32: str) -> bytes:
    padded = secret_b32.strip().upper()
    padded += "=" * ((8 - len(padded) % 8) % 8)
    return base64.b32decode(padded)


def totp_now(secret_b32: str, at_time: float | None = None) -> str:
    t = at_time if at_time is not None else time.time()
    counter = int(t // STEP_SECONDS)
    return _hotp(_decode_secret(secret_b32), counter)


def verify_totp(secret_b32: str, code: str, at_time: float | None = None, window: int = VERIFY_WINDOW) -> bool:
    """ตรวจรหัส 6 หลักที่ผู้ใช้กรอก เทียบกับ secret โดยยอมรับความคลาดเคลื่อนของเวลา ±window รอบ
    ใช้ secrets.compare_digest ป้องกัน timing attack
    """
    if not code or not code.isdigit() or len(code) != DIGITS:
        return False
    t = at_time if at_time is not None else time.time()
    counter_now = int(t // STEP_SECONDS)
    secret_bytes = _decode_secret(secret_b32)
    for delta in range(-window, window + 1):
        candidate = _hotp(secret_bytes, counter_now + delta)
        if secrets.compare_digest(candidate, code):
            return True
    return False


def provisioning_uri(secret_b32: str, account_name: str, issuer: str = "DOL Survey Logbook") -> str:
    """otpauth:// URI มาตรฐานสำหรับสร้าง QR ให้แอปยืนยันตัวตนสแกน (Key URI Format)"""
    label = quote(f"{issuer}:{account_name}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}&algorithm=SHA1"
    )


def generate_backup_codes(count: int = 8) -> list[str]:
    """สร้างรหัสสำรอง (ใช้ครั้งเดียว) รูปแบบ XXXX-XXXX สำหรับกรณีทำอุปกรณ์ยืนยันตัวตนหาย/ใช้ไม่ได้"""
    codes = []
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # ตัดตัวที่สับสนง่ายออก (0/O, 1/I/L)
    for _ in range(count):
        part1 = "".join(secrets.choice(alphabet) for _ in range(4))
        part2 = "".join(secrets.choice(alphabet) for _ in range(4))
        codes.append(f"{part1}-{part2}")
    return codes


def hash_backup_code(code: str) -> str:
    """แฮชรหัสสำรองด้วย SHA-256 ก่อนเก็บลงฐานข้อมูล (ไม่ต้องใช้ PBKDF2 เพราะเป็นรหัสสุ่ม entropy สูงอยู่แล้ว ใช้ครั้งเดียวแล้วทิ้ง)"""
    normalized = code.strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
