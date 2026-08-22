"""อัปโหลด/แสดง/ลบรูปภาพประกอบข้อมูลแปลงที่ดิน (case_documents) — จำกัดไม่เกิน 3 รูปต่อเรื่อง
เก็บไฟล์ไว้ในดิสก์ของเครื่องที่รันเซิร์ฟเวอร์ (backend/data/uploads/<case_id>/) และเสิร์ฟผ่าน endpoint ที่ต้อง login
"""
import os
from pathlib import Path

from flask import Blueprint, g, request, send_from_directory

from constants import Role
from db import get_connection
from helpers import err, new_id, now_iso, ok, scope_case_filter
from security import login_required, require_roles

bp = Blueprint("case_documents", __name__, url_prefix="/api/v1")

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
MAX_PHOTOS_PER_CASE = 3
MAX_FILE_SIZE_BYTES = 12 * 1024 * 1024  # 12MB ต่อไฟล์
# กันเฉพาะนามสกุลที่อันตรายชัดเจน (รันโค้ดได้) — นอกนั้นรับไฟล์รูปภาพทุกประเภทตามที่ขอ
BLOCKED_EXTENSIONS = {".exe", ".php", ".php3", ".php4", ".php5", ".phtml", ".sh", ".bat", ".cmd", ".js", ".html", ".htm", ".svg"}


def _case_visible(conn, case_id):
    where_sql, params = scope_case_filter(conn, g.current_user)
    return conn.execute(f"SELECT 1 FROM survey_cases WHERE id = ? AND {where_sql}", [case_id] + params).fetchone()


@bp.get("/survey-cases/<case_id>/documents")
@login_required
def list_documents(case_id):
    conn = get_connection()
    try:
        if _case_visible(conn, case_id) is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)
        rows = conn.execute(
            "SELECT * FROM case_documents WHERE case_id = ? AND document_type = 'parcel_photo' ORDER BY created_at",
            (case_id,),
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.post("/survey-cases/<case_id>/documents")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.SURVEYOR)
def upload_document(case_id):
    conn = get_connection()
    try:
        if _case_visible(conn, case_id) is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)

        file = request.files.get("file")
        if not file or not file.filename:
            return err("ต้องแนบไฟล์รูปภาพ (field name: file)")

        existing_count = conn.execute(
            "SELECT COUNT(*) AS c FROM case_documents WHERE case_id = ? AND document_type = 'parcel_photo'",
            (case_id,),
        ).fetchone()["c"]
        if existing_count >= MAX_PHOTOS_PER_CASE:
            return err(f"เพิ่มรูปภาพได้ไม่เกิน {MAX_PHOTOS_PER_CASE} รูปต่อเรื่อง กรุณาลบรูปเดิมก่อนเพิ่มใหม่", 409)

        ext = Path(file.filename).suffix.lower()
        if ext in BLOCKED_EXTENSIONS:
            return err("ไม่รองรับไฟล์ประเภทนี้")

        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        file.stream.seek(0)
        if size > MAX_FILE_SIZE_BYTES:
            return err(f"ไฟล์ใหญ่เกินไป (จำกัดไม่เกิน {MAX_FILE_SIZE_BYTES // (1024*1024)}MB ต่อไฟล์)")

        doc_id = new_id()
        case_dir = UPLOAD_DIR / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{doc_id}{ext}"
        file.save(str(case_dir / stored_name))

        ts = now_iso()
        file_url = f"/api/v1/uploads/{case_id}/{stored_name}"
        conn.execute(
            """INSERT INTO case_documents (id, case_id, document_type, file_url, uploaded_by, created_at)
               VALUES (?, ?, 'parcel_photo', ?, ?, ?)""",
            (doc_id, case_id, file_url, g.current_user["id"], ts),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM case_documents WHERE id = ?", (doc_id,)).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()


@bp.delete("/case-documents/<doc_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.BRANCH_ADMIN, Role.SURVEYOR)
def delete_document(doc_id):
    conn = get_connection()
    try:
        doc = conn.execute("SELECT * FROM case_documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            return err("ไม่พบไฟล์ที่ระบุ", 404)
        if _case_visible(conn, doc["case_id"]) is None:
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)

        conn.execute("DELETE FROM case_documents WHERE id = ?", (doc_id,))
        conn.commit()

        try:
            stored_name = doc["file_url"].rsplit("/", 1)[-1]
            file_path = UPLOAD_DIR / doc["case_id"] / stored_name
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass  # ลบ record ในฐานข้อมูลสำเร็จแล้วถือว่าใช้งานได้ ไม่ต้อง fail ถ้าลบไฟล์จริงไม่สำเร็จ

        return ok({"deleted": True})
    finally:
        conn.close()


@bp.get("/uploads/<case_id>/<filename>")
@login_required
def serve_upload(case_id, filename):
    conn = get_connection()
    try:
        if _case_visible(conn, case_id) is None:
            return err("ไม่พบไฟล์ที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)
    finally:
        conn.close()
    return send_from_directory(str(UPLOAD_DIR / case_id), filename)
