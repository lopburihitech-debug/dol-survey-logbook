"""อ่านไฟล์ shapefile (.zip ที่รวม .shp/.shx/.dbf/.prj) แบบ pure-Python ล้วน ไม่พึ่งไลบรารีภายนอก — สำหรับ
"นำเข้า" (import) ตำแหน่งหมุดหลักเขตที่ผู้ใช้ export มาจากโปรแกรม GIS อื่น (หรือไฟล์ที่ระบบนี้เอง export ออกไป
ก่อนหน้า ดู shapefile_writer.py) กลับเข้ามาเป็นหมุดในระบบ

รองรับเฉพาะ shape type Point (1) และ Polygon (5) — ชนิดเดียวกับที่ระบบนี้เขียนออก ซึ่งครอบคลุมกรณีใช้งานจริง
ส่วนใหญ่ของ shapefile ขอบเขตแปลงที่ดิน (ทดสอบกับไฟล์จริงที่ export จากโปรแกรม DOLCAD ของกรมที่ดินแล้ว)
ไม่รองรับ PointZ/PolygonZ/PolygonM หรือ MultiPatch (แจ้ง error ชัดเจนถ้าเจอชนิดอื่น แทนที่จะเดาโครงสร้างและ
ให้ผลลัพธ์ผิดเงียบๆ)

ขอบเขตของการรองรับ (เพื่อความปลอดภัย/เรียบง่าย — ไม่พยายามอ่าน .dbf attribute ใดๆ เลย เพราะชื่อฟิลด์ไม่มีมาตรฐาน
กลางระหว่างโปรแกรม และไฟล์จริงจากโปรแกรม DOLCAD ก็เก็บข้อความภาษาไทยด้วย encoding แบบเก่า (TIS-620/cp874)
ไม่ใช่ UTF-8 — ใช้เฉพาะพิกัดจากตัว geometry (.shp) เท่านั้น ผู้ใช้ตั้งชื่อ/กลุ่มของหมุดที่นำเข้าเองในฟอร์ม):
  - ไฟล์ชนิด Point: ใช้ "ทุกจุด" ทุก record เรียงตามลำดับที่ปรากฏในไฟล์ รวมเป็นกลุ่มเดียว (1 กลุ่มที่นำเข้า)
  - ไฟล์ชนิด Polygon: แต่ละ ring (part) ของแต่ละ record ถือเป็นแปลง/กลุ่มแยกกัน 1 กลุ่ม — รองรับไฟล์ที่มีหลายแปลง
    ในไฟล์เดียว (เช่นไฟล์ export จาก DOLCAD ที่มักมีทั้งแปลงหลัก + แปลงข้างเคียง/เขตคลองอยู่ในไฟล์เดียวกัน)
    ไม่รองรับ ring ที่เป็น "รู" (hole) ภายใน part เดียวกัน — ถ้าเจอจะถือเป็นกลุ่มแยกเหมือนกันหมด (ระบบนี้ใช้ ring
    เพียงเพื่อวาดตำแหน่งหมุดรอบขอบเขต ไม่ได้ใช้เป็น GIS เต็มรูปแบบที่ต้องแยกแยะ fill/hole)
"""
import io
import math
import re
import struct
import zipfile

SHAPE_TYPE_POINT = 1
SHAPE_TYPE_POLYGON = 5
_SUPPORTED_TYPES = {SHAPE_TYPE_POINT, SHAPE_TYPE_POLYGON}


class ShapefileParseError(Exception):
    """ข้อผิดพลาดจากการอ่านไฟล์ shapefile ที่แนบมา — ข้อความเป็นภาษาไทยพร้อมส่งกลับให้ผู้ใช้เห็นตรงๆ ได้เลย"""


def _order_points_by_angle(points):
    """เรียงจุดใหม่ตามมุม (angle) รอบจุดศูนย์ถ่วง (centroid) ของกลุ่ม เพื่อให้เส้นที่ลากเชื่อมจุดตามลำดับ
    (ดู frontend/case.html renderMarkerMap และ services/shapefile_writer.py) กลายเป็นเส้นขอบเขตปิดที่ไม่ตัดกันไปมา

    ใช้เฉพาะกับไฟล์ shapefile ชนิด Point เท่านั้น — แต่ละจุดในไฟล์ชนิดนี้คือ record อิสระของตัวเอง ลำดับที่ปรากฏใน
    ไฟล์จึงเป็นแค่ลำดับที่โปรแกรมต้นทาง (เช่น GPS logger หรือโปรแกรม GIS) บันทึก/ export ออกมา ไม่ได้รับประกันว่า
    เป็นลำดับ "เดินรอบขอบเขตแปลง" จริง — ถ้านำมาเชื่อมเส้นตามลำดับเดิมตรงๆ จึงมักได้เส้นไขว้กันไปมาแบบดาวกระจาย
    (ปัญหาที่ผู้ใช้แจ้งเข้ามา) ต่างจากไฟล์ชนิด Polygon ที่ตามสเปกมาตรฐานของ shapefile ลำดับจุดใน ring ต้องเป็น
    ลำดับเดินรอบขอบเขตอยู่แล้วเสมอ (_read_shp_shapes จึงคงลำดับเดิมไว้ ไม่เรียงซ้ำ)

    วิธีนี้ใช้ได้ผลดีกับแปลงที่ดินทั่วไปซึ่งเป็นรูปหลายเหลี่ยมนูนหรือใกล้นูน (พบมากที่สุดในทางปฏิบัติ) — สำหรับแปลง
    รูปเว้ามาก (concave) ผลลัพธ์อาจไม่ตรงกับขอบเขตจริง 100% แต่ยังคงดีกว่าลำดับสุ่มจากไฟล์มาก และผู้ใช้ยังแก้ไข
    ลำดับ/ตำแหน่งหมุดแต่ละจุดในหน้าเคสได้เองในภายหลังอยู่แล้ว"""
    if len(points) < 3:
        return points
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def _dedupe_closing_point(ring):
    if len(ring) >= 2 and ring[0] == ring[-1]:  # shapefile ปิดวงไว้เสมอ ตัดจุดปิดวงซ้ำทิ้ง
        return ring[:-1]
    return ring


def _read_shp_shapes(shp_bytes: bytes):
    """คืน (shape_type, record_count, point_groups) — point_groups คือ list ของ list จุด (easting, northing)
    หนึ่งรายการต่อหนึ่ง "กลุ่ม" ที่จะนำเข้า (ดูขอบเขตการรองรับด้านบนของไฟล์นี้ว่าไฟล์ Point vs Polygon แบ่งกลุ่ม
    ต่างกันอย่างไร) record_count คือจำนวน record จริงในไฟล์ (ไว้แจ้งผู้ใช้ ไม่ใช่จำนวนกลุ่มเสมอไป)"""
    if len(shp_bytes) < 100:
        raise ShapefileParseError("ไฟล์ .shp สั้นเกินไป หรือไม่ใช่ไฟล์ shapefile ที่ถูกต้อง")
    file_code = struct.unpack(">i", shp_bytes[0:4])[0]
    if file_code != 9994:
        raise ShapefileParseError("ไม่พบ signature ของไฟล์ .shp (ไฟล์อาจเสียหาย หรือไม่ใช่ shapefile จริง)")
    shape_type = struct.unpack("<i", shp_bytes[32:36])[0]
    if shape_type not in _SUPPORTED_TYPES:
        raise ShapefileParseError(
            f"รองรับเฉพาะ shapefile ชนิด Point หรือ Polygon ธรรมดา (ไฟล์นี้เป็นชนิดรหัส {shape_type} "
            "ซึ่งอาจเป็นชนิด Z/M หรือ MultiPatch ที่ยังไม่รองรับ)"
        )

    record_count = 0
    point_records = []       # ไฟล์ชนิด Point: จุดทุกจุดสะสมไว้ที่นี่ (รวมเป็นกลุ่มเดียวตอนท้าย)
    polygon_ring_groups = [] # ไฟล์ชนิด Polygon: หนึ่ง ring (part) ต่อหนึ่งกลุ่ม

    offset = 100
    n = len(shp_bytes)
    while offset + 8 <= n:
        content_words = struct.unpack(">i", shp_bytes[offset + 4:offset + 8])[0]
        content_start = offset + 8
        content_len = content_words * 2
        content_end = content_start + content_len
        if content_words <= 0 or content_end > n:
            break
        content = shp_bytes[content_start:content_end]
        rec_shape_type = struct.unpack("<i", content[0:4])[0]

        if rec_shape_type == SHAPE_TYPE_POINT and len(content) >= 20:
            x, y = struct.unpack("<dd", content[4:20])
            point_records.append((x, y))
            record_count += 1
        elif rec_shape_type == SHAPE_TYPE_POLYGON and len(content) >= 44:
            num_parts, num_points = struct.unpack("<ii", content[36:44])
            parts_start = 44
            parts_end = parts_start + 4 * num_parts
            points_start = parts_end
            points_end = points_start + 16 * num_points
            if num_parts >= 1 and num_points >= 1 and points_end <= len(content):
                parts = struct.unpack(f"<{num_parts}i", content[parts_start:parts_end])
                all_points = [
                    struct.unpack("<dd", content[points_start + 16 * i:points_start + 16 * i + 16])
                    for i in range(num_points)
                ]
                # แบ่งเป็น ring ตาม part แต่ละ ring ถือเป็นกลุ่มแยกกัน (รองรับไฟล์ที่มีหลายแปลงในไฟล์เดียว)
                for pi in range(num_parts):
                    start = parts[pi]
                    end = parts[pi + 1] if pi + 1 < num_parts else num_points
                    ring = _dedupe_closing_point(all_points[start:end])
                    if ring:
                        polygon_ring_groups.append(ring)
                record_count += 1
        # ชนิดปนกันในไฟล์เดียว หรือ record สั้นผิดปกติ — ข้าม record นี้ไปเงียบๆ (ไม่ควรเกิดตามสเปกปกติ)

        offset = content_end

    if shape_type == SHAPE_TYPE_POINT:
        point_groups = [point_records] if point_records else []
    else:
        point_groups = polygon_ring_groups
    return shape_type, record_count, point_groups


def _detect_zone_from_prj(prj_text):
    """เดา zone number (47 หรือ 48) จากข้อความใน .prj — คืน None ถ้าเดาไม่ได้ (ให้ผู้ใช้ระบุโซนเอง)"""
    if not prj_text:
        return None
    if re.search(r"zone[_ ]?47", prj_text, re.IGNORECASE):
        return 47
    if re.search(r"zone[_ ]?48", prj_text, re.IGNORECASE):
        return 48
    m = re.search(r'Central_Meridian["\']?\s*,\s*(-?\d+(?:\.\d+)?)', prj_text, re.IGNORECASE)
    if m:
        cm = float(m.group(1))
        if abs(cm - 99.0) < 1.0:
            return 47
        if abs(cm - 105.0) < 1.0:
            return 48
    return None


def extract_points_from_shapefile_zip(zip_bytes: bytes) -> dict:
    """คืน dict {point_groups: [[(easting, northing), ...], ...], shape_type, record_count, detected_zone}
    point_groups: รายการกลุ่มหมุดที่จะนำเข้า (ดูขอบเขตการรองรับด้านบนของไฟล์นี้ว่าไฟล์ Point vs Polygon แบ่งกลุ่ม
    ต่างกันอย่างไร) — ไฟล์ Point ปกติจะได้กลุ่มเดียว ไฟล์ Polygon ที่มีหลายแปลงจะได้หลายกลุ่ม (1 ต่อ 1 แปลง)
    ยกเว้น ShapefileParseError ถ้าอ่านไฟล์ไม่ได้ (ข้อความพร้อมส่งกลับให้ผู้ใช้เห็นตรงๆ)"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise ShapefileParseError("ไฟล์ที่แนบมาไม่ใช่ไฟล์ .zip ที่ถูกต้อง")

    shp_name = next((n for n in zf.namelist() if n.lower().endswith(".shp")), None)
    if shp_name is None:
        raise ShapefileParseError(
            "ไม่พบไฟล์ .shp ภายในไฟล์ .zip ที่แนบมา (ต้องรวม .shp/.shx/.dbf ไว้ในไฟล์ zip เดียวกัน)"
        )

    shp_bytes = zf.read(shp_name)
    shape_type, record_count, point_groups = _read_shp_shapes(shp_bytes)
    point_groups = [g for g in point_groups if g]  # กันกลุ่มว่าง (ไม่ควรเกิด แต่กันไว้)
    if not point_groups:
        raise ShapefileParseError("ไม่พบข้อมูลจุดพิกัดใดๆ ในไฟล์ shapefile ที่แนบมา")
    if shape_type == SHAPE_TYPE_POINT:
        # ดูเหตุผลใน _order_points_by_angle — เฉพาะไฟล์ชนิด Point เท่านั้นที่ต้องเรียงลำดับใหม่
        point_groups = [_order_points_by_angle(g) for g in point_groups]

    prj_name = next((n for n in zf.namelist() if n.lower().endswith(".prj")), None)
    prj_text = zf.read(prj_name).decode("utf-8", errors="ignore") if prj_name else None
    detected_zone = _detect_zone_from_prj(prj_text)

    return {
        "point_groups": point_groups,
        "shape_type": shape_type,
        "record_count": record_count,
        "detected_zone": detected_zone,
    }
