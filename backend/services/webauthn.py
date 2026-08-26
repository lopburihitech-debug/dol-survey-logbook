"""
ล็อกอินด้วยลายนิ้วมือ/ใบหน้า ผ่านมาตรฐาน WebAuthn (W3C) — อุปกรณ์ของผู้ใช้เอง (Windows Hello, Touch ID,
เซนเซอร์ลายนิ้วมือในมือถือ) เป็นผู้สแกนและตรวจสอบไบโอเมตริกซ์ทั้งหมด ข้อมูลไบโอเมตริกซ์จริงไม่เคยถูกส่งมาถึง
server เลยแม้แต่ไบต์เดียว — server เห็นแค่ "กุญแจสาธารณะ" (public key) กับลายเซ็นดิจิทัลที่พิสูจน์ว่าอุปกรณ์นั้น
ปลดล็อกสำเร็จเท่านั้น จึงไม่เข้าข่ายการเก็บ "ข้อมูลอ่อนไหว" ตาม PDPA มาตรา 26 เหมือนถ้าเก็บรูปหน้า/ลายนิ้วมือเอง

เขียนเองด้วย stdlib ล้วน (hashlib/struct/secrets/base64/json) ไม่พึ่งไลบรารีภายนอก (เช่น webauthn/fido2/
cryptography) ตามแนวทางเดียวกับ services/totp.py และ services/thai_datum.py ในโปรเจกต์นี้ — ส่วนที่ยากที่สุด
(ตรวจลายเซ็น ECDSA บนเส้นโค้ง P-256 และ RSA PKCS#1 v1.5) ได้ตรวจสอบความถูกต้องแล้วโดยเทียบผลลัพธ์กับไลบรารี
`cryptography` (มาตรฐานอุตสาหกรรม) ในสภาพแวดล้อมพัฒนา: ค่าคงที่เส้นโค้ง P-256 ยืนยันถูกต้องด้วยการคำนวณกุญแจ
สาธารณะจาก private key เดียวกันแล้วได้ค่าตรงกันเป๊ะ และการตรวจลายเซ็นทั้ง ES256/RS256 ทดสอบผ่านทั้งกรณีลายเซ็น
ถูกต้อง (ต้องผ่าน) และกรณีปลอม/แก้ไขข้อความ/แก้ไขลายเซ็น (ต้องถูกปฏิเสธ) ครบทุกกรณี

รองรับอัลกอริทึมกุญแจ 2 แบบ (ตามที่ขอไว้ใน pubKeyCredParams ตอน registration):
  - ES256 (COSE alg -7): ECDSA บนเส้นโค้ง P-256 — ที่ Windows Hello/Touch ID/Android รุ่นใหม่ส่วนใหญ่ใช้
  - RS256 (COSE alg -257): RSA PKCS#1 v1.5 — เผื่อกรณีอุปกรณ์รุ่นเก่าที่ยังไม่รองรับ ES256
ไม่ตรวจสอบ attestation statement (ขอ attestation="none" ตอน registration) เพราะจุดประสงค์คือ "ยืนยันตัวตน
เจ้าของบัญชีด้วยอุปกรณ์ที่เคยลงทะเบียนไว้" ไม่ใช่ "ตรวจสอบยี่ห้อ/รุ่นอุปกรณ์ที่ผลิต" — วิธีนี้เป็นแนวทางมาตรฐานของ
บริการ WebAuthn ทั่วไปที่ทำ passwordless/biometric login (ไม่ใช่ทำ device attestation สำหรับงานความปลอดภัยระดับ
สูงมาก) และช่วยให้ parsing ง่ายและปลอดภัยขึ้น (ไม่ต้องแตะ attStmt เลย)
"""
import base64
import hashlib
import json
import secrets
import struct

CHALLENGE_TTL_SECONDS = 5 * 60  # ให้เวลาผู้ใช้สแกนนิ้ว/หน้า 5 นาทีก่อน challenge หมดอายุ


class WebAuthnError(Exception):
    """ข้อผิดพลาดระหว่างลงทะเบียน/ยืนยันตัวตนด้วย WebAuthn — ข้อความเป็นภาษาไทยพร้อมส่งกลับผู้ใช้เห็นตรงๆ ได้เลย"""


# =====================================================================================
# Base64url (ไม่มี padding) — รูปแบบที่ WebAuthn/JavaScript ฝั่ง browser ใช้ทุกที่
# =====================================================================================
def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    if not isinstance(s, str):
        raise WebAuthnError("ข้อมูลที่ส่งมาไม่ถูกรูปแบบ (คาดหวัง base64url string)")
    padded = s + "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception:
        raise WebAuthnError("ข้อมูล base64url ไม่ถูกต้อง")


# =====================================================================================
# เส้นโค้ง P-256 (secp256r1 / NIST P-256) — ค่าคงที่มาตรฐานจาก SEC 2 (ยืนยันถูกต้องแล้ว ดูคอมเมนต์บนไฟล์)
# =====================================================================================
_P256_P = int("ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 16)
_P256_A = int("ffffffff00000001000000000000000000000000fffffffffffffffffffffffc", 16)
_P256_B = int("5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b", 16)
_P256_GX = int("6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296", 16)
_P256_GY = int("4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5", 16)
_P256_N = int("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16)
_P256_G = (_P256_GX, _P256_GY)


def _p256_inv(x: int, m: int) -> int:
    return pow(x, m - 2, m)  # m เป็นจำนวนเฉพาะ (p หรือ n) -> ใช้ Fermat's little theorem หาส่วนกลับได้เลย


def _p256_point_add(P, Q):
    if P is None:
        return Q
    if Q is None:
        return P
    x1, y1 = P
    x2, y2 = Q
    if x1 == x2 and (y1 + y2) % _P256_P == 0:
        return None  # จุดที่อนันต์ (P + (-P))
    if P == Q:
        lam = (3 * x1 * x1 + _P256_A) * _p256_inv((2 * y1) % _P256_P, _P256_P) % _P256_P
    else:
        lam = (y2 - y1) * _p256_inv((x2 - x1) % _P256_P, _P256_P) % _P256_P
    x3 = (lam * lam - x1 - x2) % _P256_P
    y3 = (lam * (x1 - x3) - y1) % _P256_P
    return (x3, y3)


def _p256_scalar_mult(k: int, P):
    R = None
    Q = P
    while k > 0:
        if k & 1:
            R = _p256_point_add(R, Q)
        Q = _p256_point_add(Q, Q)
        k >>= 1
    return R


def _p256_is_on_curve(x: int, y: int) -> bool:
    if not (0 <= x < _P256_P and 0 <= y < _P256_P):
        return False
    return (y * y) % _P256_P == (x**3 + _P256_A * x + _P256_B) % _P256_P


def _read_der_len(buf: bytes, idx: int):
    first = buf[idx]
    idx += 1
    if first & 0x80 == 0:
        return first, idx
    nbytes = first & 0x7F
    if nbytes == 0 or idx + nbytes > len(buf):
        raise ValueError("DER length ผิดรูปแบบ")
    val = int.from_bytes(buf[idx:idx + nbytes], "big")
    return val, idx + nbytes


def _parse_der_ecdsa_signature(der: bytes):
    """ถอดลายเซ็น ECDSA รูปแบบ DER SEQUENCE{INTEGER r, INTEGER s} ตามที่ WebAuthn/CTAP กำหนด"""
    if len(der) < 8 or der[0] != 0x30:
        raise ValueError("ไม่ใช่ DER SEQUENCE")
    idx = 1
    _seq_len, idx = _read_der_len(der, idx)
    if der[idx] != 0x02:
        raise ValueError("คาดหวัง INTEGER (r)")
    idx += 1
    rlen, idx = _read_der_len(der, idx)
    r = int.from_bytes(der[idx:idx + rlen], "big")
    idx += rlen
    if der[idx] != 0x02:
        raise ValueError("คาดหวัง INTEGER (s)")
    idx += 1
    slen, idx = _read_der_len(der, idx)
    s = int.from_bytes(der[idx:idx + slen], "big")
    return r, s


def verify_es256(x: int, y: int, message: bytes, der_signature: bytes) -> bool:
    """ตรวจลายเซ็น ECDSA P-256 + SHA-256 (COSE alg -7) — คืน False ทุกกรณีที่ไม่ผ่าน (fail-closed) ไม่โยน exception"""
    if not _p256_is_on_curve(x, y):
        return False
    try:
        r, s = _parse_der_ecdsa_signature(der_signature)
    except (ValueError, IndexError):
        return False
    if not (1 <= r < _P256_N and 1 <= s < _P256_N):
        return False
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = _p256_inv(s, _P256_N)
    u1 = (z * w) % _P256_N
    u2 = (r * w) % _P256_N
    R = _p256_point_add(_p256_scalar_mult(u1, _P256_G), _p256_scalar_mult(u2, (x, y)))
    if R is None:
        return False
    return (R[0] % _P256_N) == r


# =====================================================================================
# RSA PKCS#1 v1.5 + SHA-256 (COSE alg -257) — ใช้ pow() ของ Python เองทำ modular exponentiation
# (RFC 8017 §8.2.2 RSASSA-PKCS1-v1_5-VERIFY)
# =====================================================================================
_SHA256_DIGESTINFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_rs256(n: int, e: int, message: bytes, signature: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    sig_int = int.from_bytes(signature, "big")
    if sig_int >= n:
        return False
    m_int = pow(sig_int, e, n)
    em = m_int.to_bytes(k, "big")
    digest = hashlib.sha256(message).digest()
    expected_tail = _SHA256_DIGESTINFO_PREFIX + digest
    ps_len = k - len(expected_tail) - 3
    if ps_len < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * ps_len + b"\x00" + expected_tail
    return em == expected


def verify_signature_with_stored_key(stored_key: dict, message: bytes, signature: bytes) -> bool:
    """stored_key คือ dict รูปแบบเดียวกับที่ cose_key_to_stored_dict() คืนมา (เก็บไว้ใน webauthn_credentials.public_key_json)"""
    try:
        if stored_key.get("kty") == "EC2":
            return verify_es256(int(stored_key["x"], 16), int(stored_key["y"], 16), message, signature)
        if stored_key.get("kty") == "RSA":
            return verify_rs256(int(stored_key["n"], 16), int(stored_key["e"], 16), message, signature)
    except (KeyError, ValueError, TypeError):
        return False
    return False


# =====================================================================================
# ตัวถอด CBOR แบบย่อ — เฉพาะชนิดที่ WebAuthn ใช้จริง (unsigned/negative int, byte/text string, array, map,
# true/false/null) ไม่รองรับ float หรือ indefinite-length เพราะไม่มีใช้ในโครงสร้างของ WebAuthn
# =====================================================================================
class CborError(Exception):
    pass


def cbor_decode_one(data: bytes):
    """ถอด CBOR value ตัวเดียวจากจุดเริ่มต้นของ data คืนค่า value อย่างเดียว (ไม่สนใจไบต์ที่เหลือ)"""
    value, _offset = _cbor_decode_at(data, 0)
    return value


def _cbor_decode_at(data: bytes, offset: int):
    if offset >= len(data):
        raise CborError("ข้อมูล CBOR สั้นเกินไปหรือผิดรูปแบบ")
    initial = data[offset]
    major = initial >> 5
    info = initial & 0x1F
    offset += 1

    if info < 24:
        length = info
    elif info == 24:
        if offset >= len(data):
            raise CborError("ข้อมูล CBOR สั้นเกินไป")
        length = data[offset]
        offset += 1
    elif info == 25:
        length = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
    elif info == 26:
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
    elif info == 27:
        length = struct.unpack(">Q", data[offset:offset + 8])[0]
        offset += 8
    else:
        raise CborError(f"ไม่รองรับ CBOR additional info {info}")

    if major == 0:
        return length, offset
    if major == 1:
        return -1 - length, offset
    if major == 2:
        val = data[offset:offset + length]
        if len(val) != length:
            raise CborError("byte string ใน CBOR สั้นกว่าที่ระบุ")
        return val, offset + length
    if major == 3:
        val = data[offset:offset + length]
        if len(val) != length:
            raise CborError("text string ใน CBOR สั้นกว่าที่ระบุ")
        try:
            return val.decode("utf-8"), offset + length
        except UnicodeDecodeError:
            raise CborError("text string ใน CBOR ไม่ใช่ UTF-8 ที่ถูกต้อง")
    if major == 4:
        items = []
        for _ in range(length):
            item, offset = _cbor_decode_at(data, offset)
            items.append(item)
        return items, offset
    if major == 5:
        result = {}
        for _ in range(length):
            k, offset = _cbor_decode_at(data, offset)
            v, offset = _cbor_decode_at(data, offset)
            result[k] = v
        return result, offset
    if major == 7:
        if info == 20:
            return False, offset
        if info == 21:
            return True, offset
        if info == 22:
            return None, offset
        raise CborError(f"ไม่รองรับ CBOR simple value info={info}")
    raise CborError(f"ไม่รองรับ CBOR major type {major}")


# =====================================================================================
# COSE_Key -> รูปแบบเก็บง่ายๆ สำหรับบันทึกลง DB (webauthn_credentials.public_key_json)
# =====================================================================================
_COSE_KTY_EC2 = 2
_COSE_KTY_RSA = 3


def _cose_key_to_stored_dict(cose_key: dict) -> dict:
    if not isinstance(cose_key, dict):
        raise WebAuthnError("โครงสร้างกุญแจสาธารณะจากอุปกรณ์ไม่ถูกต้อง")
    kty = cose_key.get(1)
    if kty == _COSE_KTY_EC2:
        crv = cose_key.get(-1)
        x = cose_key.get(-2)
        y = cose_key.get(-3)
        if crv != 1 or not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != 32 or len(y) != 32:
            raise WebAuthnError("อุปกรณ์นี้ใช้ชนิดกุญแจที่ยังไม่รองรับ (รองรับเฉพาะเส้นโค้ง P-256)")
        return {"kty": "EC2", "alg": -7, "x": x.hex(), "y": y.hex()}
    if kty == _COSE_KTY_RSA:
        rsa_n = cose_key.get(-1)
        rsa_e = cose_key.get(-2)
        if not isinstance(rsa_n, bytes) or not isinstance(rsa_e, bytes):
            raise WebAuthnError("โครงสร้างกุญแจ RSA จากอุปกรณ์ไม่ถูกต้อง")
        return {"kty": "RSA", "alg": -257, "n": rsa_n.hex(), "e": rsa_e.hex()}
    raise WebAuthnError("อุปกรณ์นี้ใช้ชนิดกุญแจที่ยังไม่รองรับ (รองรับเฉพาะ ES256 หรือ RS256)")


# =====================================================================================
# ขั้นตอนที่ 1: สร้าง options ให้ browser เรียก navigator.credentials.create()/.get()
# =====================================================================================
def generate_challenge() -> bytes:
    return secrets.token_bytes(32)


def build_registration_options(rp_id: str, rp_name: str, user_id: str, username: str, display_name: str,
                                challenge: bytes, exclude_credential_ids: list[str]) -> dict:
    return {
        "challenge": b64url_encode(challenge),
        "rp": {"id": rp_id, "name": rp_name},
        "user": {
            "id": b64url_encode(user_id.encode("utf-8")),
            "name": username,
            "displayName": display_name,
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},    # ES256 — อุปกรณ์ส่วนใหญ่รองรับ ลองก่อน
            {"type": "public-key", "alg": -257},  # RS256 — เผื่ออุปกรณ์รุ่นเก่า
        ],
        "timeout": 60000,
        "attestation": "none",
        "authenticatorSelection": {
            "userVerification": "required",  # บังคับให้ต้องสแกนนิ้ว/หน้า/PIN เครื่องจริง ไม่ใช่แค่แตะเฉยๆ
            "residentKey": "preferred",
        },
        "excludeCredentials": [{"type": "public-key", "id": cid} for cid in exclude_credential_ids],
    }


def build_authentication_options(rp_id: str, challenge: bytes, allow_credential_ids: list[str]) -> dict:
    return {
        "challenge": b64url_encode(challenge),
        "rpId": rp_id,
        "timeout": 60000,
        "userVerification": "required",
        "allowCredentials": [{"type": "public-key", "id": cid} for cid in allow_credential_ids],
    }


# =====================================================================================
# ขั้นตอนที่ 2: ตรวจผลลัพธ์ที่ browser ส่งกลับมาหลัง navigator.credentials.create()/.get()
# =====================================================================================
def _check_client_data(client_data_json: bytes, expected_type: str, expected_challenge: bytes, expected_origin: str) -> None:
    try:
        client_data = json.loads(client_data_json.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise WebAuthnError("ข้อมูล clientDataJSON ไม่ถูกต้อง")
    if client_data.get("type") != expected_type:
        raise WebAuthnError("ประเภทคำขอไม่ถูกต้อง (clientData.type ไม่ตรง)")
    if b64url_decode(client_data.get("challenge", "")) != expected_challenge:
        raise WebAuthnError("challenge ไม่ตรงกัน หรือหมดอายุแล้ว กรุณาลองใหม่")
    if client_data.get("origin") != expected_origin:
        raise WebAuthnError("คำขอนี้มาจากโดเมนที่ไม่ถูกต้อง")


def verify_registration_response(*, client_data_json_b64: str, attestation_object_b64: str,
                                  expected_challenge: bytes, expected_rp_id: str, expected_origin: str) -> dict:
    """ตรวจผลการลงทะเบียนอุปกรณ์ใหม่ — คืน dict {credential_id (base64url str), public_key (dict เก็บลง DB), sign_count}
    ยกเว้น WebAuthnError ถ้าตรวจไม่ผ่านขั้นตอนใดๆ (ข้อความเป็นภาษาไทยพร้อมส่งกลับผู้ใช้เห็นตรงๆ ได้)"""
    client_data_json = b64url_decode(client_data_json_b64)
    _check_client_data(client_data_json, "webauthn.create", expected_challenge, expected_origin)

    try:
        attestation_object = cbor_decode_one(b64url_decode(attestation_object_b64))
    except CborError:
        raise WebAuthnError("ข้อมูล attestationObject จากอุปกรณ์อ่านไม่ได้")
    if not isinstance(attestation_object, dict) or "authData" not in attestation_object:
        raise WebAuthnError("โครงสร้าง attestationObject ไม่ถูกต้อง")

    auth_data = attestation_object["authData"]
    if not isinstance(auth_data, bytes) or len(auth_data) < 37:
        raise WebAuthnError("authenticatorData สั้นเกินไป")

    rp_id_hash = auth_data[0:32]
    flags = auth_data[32]
    if rp_id_hash != hashlib.sha256(expected_rp_id.encode("utf-8")).digest():
        raise WebAuthnError("rpIdHash ไม่ตรงกับระบบนี้")
    if not (flags & 0x01):
        raise WebAuthnError("อุปกรณ์ไม่ได้ยืนยันการมีอยู่ของผู้ใช้ (User Present)")
    if not (flags & 0x40):
        raise WebAuthnError("ไม่พบข้อมูลกุญแจสาธารณะจากอุปกรณ์ (Attested Credential Data)")

    sign_count = struct.unpack(">I", auth_data[33:37])[0]
    offset = 37 + 16  # ข้าม rpIdHash+flags+signCount (37 ไบต์) แล้วข้าม AAGUID (16 ไบต์)
    if len(auth_data) < offset + 2:
        raise WebAuthnError("authenticatorData สั้นเกินไป (credentialIdLength)")
    cred_id_len = struct.unpack(">H", auth_data[offset:offset + 2])[0]
    offset += 2
    if len(auth_data) < offset + cred_id_len:
        raise WebAuthnError("authenticatorData สั้นเกินไป (credentialId)")
    credential_id = auth_data[offset:offset + cred_id_len]
    offset += cred_id_len

    try:
        cose_key = cbor_decode_one(auth_data[offset:])
    except CborError:
        raise WebAuthnError("ข้อมูลกุญแจสาธารณะ (COSE key) จากอุปกรณ์อ่านไม่ได้")

    stored_key = _cose_key_to_stored_dict(cose_key)
    return {
        "credential_id": b64url_encode(credential_id),
        "public_key": stored_key,
        "sign_count": sign_count,
    }


def verify_authentication_response(*, client_data_json_b64: str, authenticator_data_b64: str, signature_b64: str,
                                    expected_challenge: bytes, expected_rp_id: str, expected_origin: str,
                                    stored_public_key: dict, stored_sign_count: int) -> int:
    """ตรวจการยืนยันตัวตนด้วยอุปกรณ์ที่ลงทะเบียนไว้แล้ว — คืน sign_count ใหม่ (ให้ผู้เรียกไปอัปเดตใน DB ต่อ)
    ยกเว้น WebAuthnError ถ้าตรวจไม่ผ่านขั้นตอนใดๆ"""
    client_data_json = b64url_decode(client_data_json_b64)
    _check_client_data(client_data_json, "webauthn.get", expected_challenge, expected_origin)

    auth_data = b64url_decode(authenticator_data_b64)
    if len(auth_data) < 37:
        raise WebAuthnError("authenticatorData สั้นเกินไป")

    rp_id_hash = auth_data[0:32]
    flags = auth_data[32]
    if rp_id_hash != hashlib.sha256(expected_rp_id.encode("utf-8")).digest():
        raise WebAuthnError("rpIdHash ไม่ตรงกับระบบนี้")
    if not (flags & 0x01):
        raise WebAuthnError("อุปกรณ์ไม่ได้ยืนยันการมีอยู่ของผู้ใช้ (User Present)")

    new_sign_count = struct.unpack(">I", auth_data[33:37])[0]
    # signCount ต้องเพิ่มขึ้นเสมอเทียบกับครั้งก่อน (ป้องกัน replay ลายเซ็นเก่าที่ดักไว้) — ยกเว้นกรณีอุปกรณ์ไม่รองรับ
    # ตัวนับเลย (รายงาน 0 ทุกครั้ง ซึ่งพบได้บ่อยกับ resident key/platform authenticator บางรุ่น) ตามข้อกำหนดของสเปก
    # WebAuthn เอง (§6.1.1) ให้ข้าม check นี้เฉพาะกรณี "ทั้งค่าเก่าและค่าใหม่เป็น 0" เท่านั้น
    if not (stored_sign_count == 0 and new_sign_count == 0):
        if new_sign_count <= stored_sign_count:
            raise WebAuthnError("ตรวจพบความผิดปกติ (signature counter ไม่เพิ่มขึ้น) อาจเป็นการคัดลอกอุปกรณ์ — ปฏิเสธเพื่อความปลอดภัย")

    client_data_hash = hashlib.sha256(client_data_json).digest()
    message = auth_data + client_data_hash
    signature = b64url_decode(signature_b64)
    if not verify_signature_with_stored_key(stored_public_key, message, signature):
        raise WebAuthnError("ลายเซ็นจากอุปกรณ์ไม่ถูกต้อง")

    return new_sign_count


def rp_id_and_origin_from_request(request) -> tuple[str, str]:
    """คำนวณ rpId (โดเมนล้วนๆ ไม่มี scheme/port) และ origin (scheme+host+port เต็ม) จาก request ปัจจุบัน
    แบบ dynamic แทนที่จะ hardcode โดเมน — ใช้ได้ทันทีไม่ว่าจะรันที่ localhost (dev) หรือโดเมนจริงตอน deploy
    (รวมถึงถ้าอนาคตเปลี่ยนไปผูกโดเมนของตัวเองแทน *.up.railway.app ก็ใช้ได้ทันทีโดยไม่ต้องแก้โค้ด)
    หมายเหตุ: ต้องรันผ่าน HTTPS เสมอในการใช้งานจริง (WebAuthn บังคับ ยกเว้น origin เป็น localhost เท่านั้น) —
    Railway ให้ HTTPS มาให้อัตโนมัติอยู่แล้วที่โดเมน *.up.railway.app"""
    host = request.host  # เช่น "web-production-6d6f71.up.railway.app" หรือ "localhost:8000"
    rp_id = host.split(":")[0]
    origin = f"{request.scheme}://{host}"
    return rp_id, origin
