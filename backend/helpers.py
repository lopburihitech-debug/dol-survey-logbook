"""ฟังก์ชันช่วยเหลือที่ใช้ร่วมกันหลาย blueprint: RBAC scoping, บันทึกประวัติสถานะ, response helper"""
import uuid
from datetime import datetime, timezone

from flask import jsonify

from constants import Role


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def err(message: str, status: int = 400):
    return jsonify({"error": {"message": message}}), status


def ok(data, status: int = 200):
    return jsonify(data), status


def get_surveyor_profile(conn, user_id: str):
    return conn.execute("SELECT * FROM surveyors WHERE user_id = ?", (user_id,)).fetchone()


def get_user_province(conn, current_user: dict) -> str | None:
    """คืนจังหวัดของ province_admin คนนี้ (ดูจากจังหวัดของสำนักงานที่บัญชีตัวเองสังกัดอยู่ — office_id ของ
    province_admin ถือเป็นแค่ 'สำนักงานตัวแทน' เพื่อระบุว่าอยู่จังหวัดไหน ไม่ได้จำกัดแค่สำนักงานเดียวแบบ supervisor)
    คืน None ถ้าไม่มี office_id ผูกไว้ (ตั้งค่าไม่ครบ) — ผู้เรียกควรถือว่าไม่มีขอบเขตอะไรให้เห็นเลยในกรณีนี้
    """
    if not current_user.get("office_id"):
        return None
    row = conn.execute("SELECT province FROM offices WHERE id = ?", (current_user["office_id"],)).fetchone()
    return row["province"] if row else None


def province_office_ids(conn, province: str) -> list[str]:
    """คืนรายการ office id ทั้งหมด (รวมที่ปิดใช้งานแล้วด้วย เพื่อไม่ให้ของเก่าหายไปจากประวัติ) ที่อยู่ในจังหวัดนี้"""
    rows = conn.execute("SELECT id FROM offices WHERE province = ?", (province,)).fetchall()
    return [r["id"] for r in rows]


def is_office_in_user_scope(conn, current_user: dict, office_id: str) -> bool:
    """เช็คว่า office_id ที่ระบุ อยู่ในขอบเขตที่ current_user คนนี้ควรแตะต้องได้ไหม — ใช้ตรวจสอบระดับ 'รายการเดียว'
    (เช่นก่อนแก้ไข/ย้ายช่างรังวัดหรือเรื่องไปสำนักงานหนึ่ง) ซึ่ง require_roles อย่างเดียวตรวจไม่ได้เพราะเช็คแค่บทบาท
    ไม่เช็คว่ารายการนั้นอยู่ในขอบเขตของผู้ใช้จริงไหม — system_admin/administrator ไม่มีขอบเขต (เห็น/แก้ได้ทุกสำนักงาน)
    """
    role = current_user["role"]
    if role in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR):
        return True
    if role == Role.PROVINCE_ADMIN:
        province = get_user_province(conn, current_user)
        if province is None:
            return False
        return office_id in province_office_ids(conn, province)
    if role in (Role.SUPERVISOR, Role.BRANCH_ADMIN):
        return office_id == current_user.get("office_id")
    return False


def scope_case_filter(conn, current_user: dict):
    """คืนค่า (where_sql, params) สำหรับจำกัดผลลัพธ์ survey_cases ตามบทบาทผู้ใช้
    ตาม Role & Permission Matrix หัวข้อ 3 ของ Blueprint
    """
    role = current_user["role"]
    if role in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR):
        return "1=1", []
    if role == Role.PROVINCE_ADMIN:
        province = get_user_province(conn, current_user)
        if province is None:
            return "1=0", []
        office_ids = province_office_ids(conn, province)
        if not office_ids:
            return "1=0", []
        placeholders = ", ".join("?" for _ in office_ids)
        return f"office_id IN ({placeholders})", office_ids
    if role in (Role.SUPERVISOR, Role.BRANCH_ADMIN):
        return "office_id = ?", [current_user["office_id"]]
    if role == Role.SURVEYOR:
        surveyor = get_surveyor_profile(conn, current_user["id"])
        if surveyor is None:
            return "1=0", []
        return (
            "id IN (SELECT case_id FROM case_assignments WHERE surveyor_id = ? AND is_active = 1)",
            [surveyor["id"]],
        )
    return "1=0", []


def record_status_change(conn, case_id: str, previous_status: str, new_status: str, changed_by: str, reason: str | None = None):
    conn.execute(
        """INSERT INTO case_status_history (id, case_id, previous_status, new_status, changed_by, reason, changed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (new_id(), case_id, previous_status, new_status, changed_by, reason, now_iso()),
    )
    conn.execute(
        "UPDATE survey_cases SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, now_iso(), case_id),
    )
