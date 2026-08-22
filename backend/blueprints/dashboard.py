from datetime import datetime

from flask import Blueprint, g

from constants import CaseStatus, Role
from db import get_connection
from helpers import get_user_province, ok, scope_case_filter
from security import login_required

bp = Blueprint("dashboard", __name__, url_prefix="/api/v1/dashboard")


@bp.get("/summary")
@login_required
def dashboard_summary():
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        rows = conn.execute(f"SELECT status, due_date, updated_at FROM survey_cases WHERE {where_sql}", params).fetchall()

        now = datetime.now()
        now_iso = now.isoformat()
        total = len(rows)
        pending = len([r for r in rows if r["status"] not in CaseStatus.CLOSED_SET])
        overdue = len([r for r in rows if r["due_date"] and r["due_date"] < now_iso and r["status"] not in CaseStatus.CLOSED_SET])
        pending_review = len([r for r in rows if r["status"] == CaseStatus.PENDING_REVIEW])
        completed_this_month = len(
            [
                r
                for r in rows
                if r["status"] == CaseStatus.CLOSED
                and r["updated_at"]
                and r["updated_at"][:7] == now_iso[:7]
            ]
        )

        return ok(
            {
                "total_cases": total,
                "pending_cases": pending,
                "overdue_cases": overdue,
                "pending_review_cases": pending_review,
                "completed_this_month": completed_this_month,
            }
        )
    finally:
        conn.close()


@bp.get("/by-office")
@login_required
def dashboard_by_office():
    """สรุปจำนวนงานแยกตามสำนักงาน (สำหรับ dashboard แบบแยกจังหวัด/สาขา)
    - system_admin / administrator: เห็นทุกสำนักงานทุกจังหวัด
    - province_admin: เห็นเฉพาะสำนักงานทั้งหมดในจังหวัดตัวเอง
    - supervisor / branch_admin: เห็นเฉพาะสำนักงานตัวเอง (1 แถว)
    - surveyor / citizen: ไม่มีประโยชน์ต่อมุมมองนี้ คืนค่าว่าง ให้ frontend ซ่อนส่วนนี้ไปเลย
    """
    conn = get_connection()
    try:
        role = g.current_user["role"]
        if role not in (Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN):
            return ok([])

        offices = conn.execute(
            "SELECT id, name, province, district FROM offices WHERE is_active = 1 ORDER BY province, name"
        ).fetchall()
        if role == Role.PROVINCE_ADMIN:
            province = get_user_province(conn, g.current_user)
            offices = [o for o in offices if o["province"] == province]
        elif role in (Role.SUPERVISOR, Role.BRANCH_ADMIN):
            offices = [o for o in offices if o["id"] == g.current_user["office_id"]]

        now_iso = datetime.now().isoformat()
        result = []
        for o in offices:
            rows = conn.execute(
                "SELECT status, due_date FROM survey_cases WHERE office_id = ?", [o["id"]]
            ).fetchall()
            total = len(rows)
            pending = len([r for r in rows if r["status"] not in CaseStatus.CLOSED_SET])
            overdue = len(
                [r for r in rows if r["due_date"] and r["due_date"] < now_iso and r["status"] not in CaseStatus.CLOSED_SET]
            )
            result.append(
                {
                    "office_id": o["id"],
                    "office_name": o["name"],
                    "province": o["province"],
                    "district": o["district"],
                    "total_cases": total,
                    "pending_cases": pending,
                    "overdue_cases": overdue,
                    "completed_cases": total - pending,
                }
            )
        return ok(result)
    finally:
        conn.close()


@bp.get("/status-breakdown")
@login_required
def dashboard_status_breakdown():
    """นับจำนวนเรื่องแยกตามสถานะ (ตามสิทธิ์ที่มองเห็น) สำหรับวาดกราฟโดนัทสัดส่วนสถานะใน dashboard"""
    conn = get_connection()
    try:
        where_sql, params = scope_case_filter(conn, g.current_user)
        rows = conn.execute(
            f"SELECT status, COUNT(*) as c FROM survey_cases WHERE {where_sql} GROUP BY status", params
        ).fetchall()
        return ok([{"status": r["status"], "count": r["c"]} for r in rows])
    finally:
        conn.close()
