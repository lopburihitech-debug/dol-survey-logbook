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
from services.shapefile_reader import ShapefileParseError, extract_points_from_shapefile_zip
from services.shapefile_writer import build_marker_shapefile_zip
from services.thai_datum import indian1975_utm_to_wgs84

bp = Blueprint("boundary_markers", __name__, url_prefix="/api/v1")

MAX_MARKERS_PER_CASE = 100  # กันการเพิ่มหมุดผิดพลาดไม่จำกัดจนระบบช้า (เพียงพอสำหรับแปลงจริงทุกกรณี)

# ช่วงค่า Easting/Northing ที่สมเหตุสมผลสำหรับ UTM ในประเทศไทย (หน่วยเมตร) — ใช้ดักกรณีผู้ใช้แนบไฟล์ที่พิกัดเป็น
# ละติจูด/ลองจิจูด (องศา) มาผิดระบบ ไม่ใช่ขอบเขตที่แม่นยำของโซนใดโซนหนึ่งโดยเฉพาะ (กันผิดพลาดหยาบๆ เท่านั้น)
UTM_EASTING_MIN, UTM_EASTING_MAX = 100000.0, 900000.0
UTM_NORTHING_MIN, UTM_NORTHING_MAX = 0.0, 10000000.0


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
        # กลุ่มแปลง: ว่าง/ไม่ส่งมา = หมุดของแปลงหลัก, ถ้าส่งมาคือชื่อแปลงข้างเคียงที่หมุดจุดนี้เป็นส่วนหนึ่งของขอบเขต
        marker_group = (request.form.get("group") or "").strip() or None

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
                                            uploaded_by, created_at, sequence_no, label, marker_group)
               VALUES (?, ?, 'boundary_marker', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, case_id, file_url, lat, lng, ts, g.current_user["id"], ts, next_seq, label, marker_group),
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
        if "group" in payload:
            conn.execute("UPDATE case_documents SET marker_group = ? WHERE id = ?", ((payload["group"] or "").strip() or None, marker_id))

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


@bp.post("/survey-cases/<case_id>/markers/import-shapefile")
@login_required
@require_roles(Role.SYSTEM_ADMIN, Role.ADMINISTRATOR, Role.PROVINCE_ADMIN, Role.SUPERVISOR, Role.SURVEYOR)
def import_shapefile(case_id):
    """นำเข้าหมุดจากไฟล์ shapefile (.zip) ที่อ้างอิงระบบพิกัด Indian 1975 UTM (ดู services/shapefile_reader.py
    สำหรับขอบเขตการรองรับ และ services/thai_datum.py สำหรับการแปลงพิกัดกลับเป็น WGS84) — ไม่แนบภาพถ่ายให้หมุด
    ที่นำเข้าด้วยวิธีนี้ (file_url ว่าง) เพราะเป็นพิกัดที่มาจากไฟล์ ไม่ใช่การถ่ายภาพหน้างาน
    ไฟล์ Polygon ที่มีหลายแปลงในไฟล์เดียว (พบจริงในไฟล์ export จากโปรแกรม DOLCAD ของกรมที่ดิน ที่มักมีทั้งแปลงหลัก
    และแปลงข้างเคียง/เขตคลองอยู่ในไฟล์เดียวกัน) จะถูกแยกเป็นคนละกลุ่มอัตโนมัติ ไม่ปนกันเป็นเส้นขอบเขตเดียว"""
    conn = get_connection()
    try:
        if _case_visible(conn, case_id) is None:
            return err("ไม่พบเรื่องที่ระบุ หรือไม่มีสิทธิ์เข้าถึง", 404)

        file = request.files.get("file")
        if not file or not file.filename:
            return err("ต้องแนบไฟล์ shapefile แบบ .zip (field name: file)")
        if not file.filename.lower().endswith(".zip"):
            return err("ไฟล์ที่แนบต้องเป็น .zip ที่รวม .shp/.shx/.dbf ไว้ภายใน")

        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        file.stream.seek(0)
        if size > MAX_FILE_SIZE_BYTES:
            return err(f"ไฟล์ใหญ่เกินไป (จำกัดไม่เกิน {MAX_FILE_SIZE_BYTES // (1024*1024)}MB ต่อไฟล์)")
        zip_bytes = file.read()

        try:
            parsed = extract_points_from_shapefile_zip(zip_bytes)
        except ShapefileParseError as e:
            return err(str(e))

        zone_param = (request.form.get("zone") or "").strip()
        zone_number = None
        if zone_param:
            try:
                zone_number = int(zone_param)
            except ValueError:
                return err("โซน UTM ต้องเป็นตัวเลข 47 หรือ 48 เท่านั้น")
        if zone_number is None:
            zone_number = parsed["detected_zone"]
        if zone_number not in (47, 48):
            return err(
                "ไม่สามารถระบุโซน UTM จากไฟล์ .prj ในไฟล์ได้อัตโนมัติ กรุณาระบุโซนเอง "
                "(47 สำหรับพื้นที่ลองจิจูด < 102°E, 48 สำหรับพื้นที่ลองจิจูด ≥ 102°E)"
            )

        point_groups = parsed["point_groups"]
        total_points = sum(len(g) for g in point_groups)
        for group_points in point_groups:
            for easting, northing in group_points:
                if not (UTM_EASTING_MIN <= easting <= UTM_EASTING_MAX and UTM_NORTHING_MIN <= northing <= UTM_NORTHING_MAX):
                    return err(
                        "ค่าพิกัดในไฟล์ดูผิดปกติสำหรับระบบ UTM (Easting/Northing หน่วยเมตร) กรุณาตรวจสอบว่าไฟล์นี้เป็น "
                        "ระบบพิกัด Indian 1975 UTM จริง ไม่ใช่ latitude/longitude หรือระบบพิกัดอื่น"
                    )

        base_group = (request.form.get("group") or "").strip()
        # ไฟล์มีกลุ่มเดียว (ไฟล์ Point ทั่วไป หรือไฟล์ Polygon ที่มีแปลงเดียว) -> ใช้กลุ่มที่ผู้ใช้เลือกตรงๆ
        # (ว่าง = แปลงหลัก) ไฟล์ Polygon ที่มีหลายแปลงในไฟล์เดียว (เช่นไฟล์จาก DOLCAD ที่มักมีทั้งแปลงหลัก+ข้างเคียง
        # อยู่ด้วยกัน) -> ตั้งชื่อแยกอัตโนมัติทีละแปลง ไม่รวมเป็นกลุ่มเดียวกัน (ไม่งั้นเส้นขอบเขตจะปนกันมั่ว)
        if len(point_groups) == 1:
            group_labels = [base_group or None]
        else:
            prefix = base_group or "แปลงจากไฟล์"
            group_labels = [f"{prefix} {i + 1}" for i in range(len(point_groups))]

        existing_count = conn.execute(
            "SELECT COUNT(*) AS c FROM case_documents WHERE case_id = ? AND document_type = 'boundary_marker'",
            (case_id,),
        ).fetchone()["c"]
        if existing_count + total_points > MAX_MARKERS_PER_CASE:
            return err(
                f"นำเข้าไม่ได้ — จะทำให้จำนวนหมุดรวมเกิน {MAX_MARKERS_PER_CASE} จุดต่อเรื่อง "
                f"(ปัจจุบันมี {existing_count} จุด ไฟล์นี้มี {total_points} จุด)",
                409,
            )

        next_seq = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS n FROM case_documents WHERE case_id = ? AND document_type = 'boundary_marker'",
            (case_id,),
        ).fetchone()["n"]

        ts = now_iso()
        created_ids = []
        seq = next_seq
        for group_points, marker_group in zip(point_groups, group_labels):
            for easting, northing in group_points:
                lat, lng = indian1975_utm_to_wgs84(easting, northing, zone_number)
                doc_id = new_id()
                conn.execute(
                    """INSERT INTO case_documents (id, case_id, document_type, file_url, geo_lat, geo_lng, taken_at,
                                                    uploaded_by, created_at, sequence_no, label, marker_group)
                       VALUES (?, ?, 'boundary_marker', '', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, case_id, lat, lng, ts, g.current_user["id"], ts, seq, f"นำเข้าจากไฟล์ (จุดที่ {seq})", marker_group),
                )
                created_ids.append(doc_id)
                seq += 1
        conn.commit()

        rows = conn.execute(
            f"SELECT * FROM case_documents WHERE id IN ({','.join('?' for _ in created_ids)}) ORDER BY sequence_no",
            created_ids,
        ).fetchall()
        return ok(
            {
                "imported_count": len(created_ids),
                "group_count": len(point_groups),
                "zone_used": zone_number,
                "zone_auto_detected": bool(parsed["detected_zone"]) and not zone_param,
                "shape_type": "point" if parsed["shape_type"] == 1 else "polygon",
                "source_record_count": parsed["record_count"],
                "markers": [dict(r) for r in rows],
            },
            201,
        )
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
            """SELECT sequence_no, label, geo_lat AS lat, geo_lng AS lng, marker_group AS "group"
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
