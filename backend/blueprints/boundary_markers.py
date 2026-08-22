"""แผนที่หมุดหลักเขต — ช่างรังวัดถ่ายภาพ + บันทึกพิกัด GPS ของหมุดแต่ละจุดในสนาม เพิ่มไปเรื่อยๆ จนครบ
เก็บเป็น case_documents ที่ document_type = 'boundary_marker' (ใช้คอลัมน์ geo_lat/geo_lng ที่มีอยู่แล้ว
บวก sequence_no/label ที่เพิ่มเข้ามา) และสร้างไฟล์ shapefile (จุดหมุด + รูปปิดขอบเขต) ให้ดาวน์โหลดได้ทันที
"""
import os
import re
from pathlib import Path

from flask import Blueprint, g, request, send_from_directory, Response

from blueprints.case_documents import BLOCKED_EXTENSIONS, MAX_FILE_SIZE_BYTES, UPLOAD_DIR, _case_visible
from constants import Role
from db import get_connection
from helpers import err, new_id, now_iso, ok, scope_case_filter
from security import login_required, require_roles
from services.shapefile_writer import build_marker_shapefile_zip

bp = Blueprint("boundary_markers", __name__, url_prefix="/api/v1")

MAX_MARKERS_PER_CASE = 100  # กันการเพิ่มหมุดผิดพลาดไม่จำกัดจนระบบช้า (เพียงพอสำหรับแปลงจริงทุกกรณี)


@bp.get("/survey-cases/<case_id>/markers")
@login_required
def list_markers(case_id):
    conn = get_connection()
    try:
        if _case_visible(conn, case_id) is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)
        rows = conn.execute(
            """SELECT * FROM case_documents WHERE case_id = ? AND document_type = 'boundary_marker'
               ORDER BY sequence_no""",
            (case_id,),
        ).fetchall()
        return ok([dict(r) for r in rows])
    finally:
        conn.close()


@bp.post("/survey-cases/<case_id>/markers")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.SURVEYOR)
def create_marker(case_id):
    conn = get_connection()
    try:
        if _case_visible(conn, case_id) is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)

        file = request.files.get("file")
        if not file or not file.filename:
            return err("ต้องแนบภาพถ่ายหมุด (field name: file)")

        try:
            lat = float(request.form.get("lat"))
            lng = float(request.form.get("lng"))
        except (TypeError, ValueError):
            return err("ต้องระบุพิกัด lat และ lng เป็นตัวเลข")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return err("พิกัด lat/lng อยู่นอกช่วงที่ถูกต้อง")

        label = (request.form.get("label") or "").strip() or None

        existing_count = conn.execute(
            "SELECT COUNT(*) AS c FROM case_documents WHERE case_id = ? AND document_type = 'boundary_marker'",
            (case_id,),
        ).fetchone()["c"]
        if existing_count >= MAX_MARKERS_PER_CASE:
            return err(f"เพิ่มหมุดได้ไม่เกิน {MAX_MARKERS_PER_CASE} จุดต่อเรื่อง", 409)

        ext = Path(file.filename).suffix.lower()
        if ext in BLOCKED_EXTENSIONS:
            return err("ไม่รองรับไฟล์ประเภทนี้")

        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        file.stream.seek(0)
        if size > MAX_FILE_SIZE_BYTES:
            return err(f"ไฟล์ใหญ่เกินไป (จำกัดไม่เกิน {MAX_FILE_SIZE_BYTES // (1024*1024)}MB ต่อไฟล์)")

        next_seq = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS n FROM case_documents WHERE case_id = ? AND document_type = 'boundary_marker'",
            (case_id,),
        ).fetchone()["n"]

        doc_id = new_id()
        case_dir = UPLOAD_DIR / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{doc_id}{ext}"
        file.save(str(case_dir / stored_name))

        ts = now_iso()
        file_url = f"/api/v1/uploads/{case_id}/{stored_name}"
        conn.execute(
            """INSERT INTO case_documents (id, case_id, document_type, file_url, geo_lat, geo_lng, taken_at,
                                            uploaded_by, created_at, sequence_no, label)
               VALUES (?, ?, 'boundary_marker', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, case_id, file_url, lat, lng, ts, g.current_user["id"], ts, next_seq, label),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM case_documents WHERE id = ?", (doc_id,)).fetchone()
        return ok(dict(row), 201)
    finally:
        conn.close()


UPDATABLE_MARKER_FIELDS = {"lat": "geo_lat", "lng": "geo_lng", "label": "label"}


@bp.patch("/markers/<marker_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.SURVEYOR)
def update_marker(marker_id):
    payload = request.get_json(silent=True) or {}
    conn = get_connection()
    try:
        marker = conn.execute(
            "SELECT * FROM case_documents WHERE id = ? AND document_type = 'boundary_marker'", (marker_id,)
        ).fetchone()
        if marker is None:
            return err("ไม่พบหมุดที่ระบุ", 404)
        if _case_visible(conn, marker["case_id"]) is None:
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)

        if "lat" in payload or "lng" in payload:
            try:
                lat = float(payload["lat"]) if "lat" in payload else marker["geo_lat"]
                lng = float(payload["lng"]) if "lng" in payload else marker["geo_lng"]
            except (TypeError, ValueError):
                return err("พิกัด lat/lng ต้องเป็นตัวเลข")
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                return err("พิกัด lat/lng อยู่นอกช่วงที่ถูกต้อง")
            conn.execute("UPDATE case_documents SET geo_lat = ?, geo_lng = ? WHERE id = ?", (lat, lng, marker_id))
        if "label" in payload:
            conn.execute("UPDATE case_documents SET label = ? WHERE id = ?", ((payload["label"] or "").strip() or None, marker_id))

        conn.commit()
        row = conn.execute("SELECT * FROM case_documents WHERE id = ?", (marker_id,)).fetchone()
        return ok(dict(row))
    finally:
        conn.close()


@bp.delete("/markers/<marker_id>")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.SURVEYOR)
def delete_marker(marker_id):
    conn = get_connection()
    try:
        marker = conn.execute(
            "SELECT * FROM case_documents WHERE id = ? AND document_type = 'boundary_marker'", (marker_id,)
        ).fetchone()
        if marker is None:
            return err("ไม่พบหมุดที่ระบุ", 404)
        if _case_visible(conn, marker["case_id"]) is None:
            return err("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้", 403)

        conn.execute("DELETE FROM case_documents WHERE id = ?", (marker_id,))
        conn.commit()

        try:
            stored_name = marker["file_url"].rsplit("/", 1)[-1]
            file_path = UPLOAD_DIR / marker["case_id"] / stored_name
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass

        return ok({"deleted": True})
    finally:
        conn.close()


@bp.get("/survey-cases/<case_id>/markers/shapefile")
@login_required
def download_shapefile(case_id):
    conn = get_connection()
    try:
        case = None
        where_sql, params = scope_case_filter(conn, g.current_user)
        row = conn.execute(f"SELECT * FROM survey_cases WHERE id = ? AND {where_sql}", [case_id] + params).fetchone()
        if row is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)
        case = dict(row)

        markers = conn.execute(
            """SELECT sequence_no, label, geo_lat AS lat, geo_lng AS lng
               FROM case_documents WHERE case_id = ? AND document_type = 'boundary_marker'
               ORDER BY sequence_no""",
            (case_id,),
        ).fetchall()
        if not markers:
            return err("ยังไม่มีหมุดหลักเขตที่บันทึกไว้ ไม่สามารถสร้าง shapefile ได้", 400)

        prefix = re.sub(r"[^A-Za-z0-9_-]", "-", case["case_code"]) or "boundary"
        zip_bytes = build_marker_shapefile_zip([dict(m) for m in markers], prefix)

        return Response(
            zip_bytes,
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{prefix}_shapefile.zip"'},
        )
    finally:
        conn.close()
