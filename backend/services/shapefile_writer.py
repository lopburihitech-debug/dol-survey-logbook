"""เขียนไฟล์ shapefile (ESRI Shapefile: .shp/.shx/.dbf/.prj/.cpg) แบบ pure-Python ล้วน ไม่พึ่งไลบรารีภายนอก
(อ้างอิงตาม ESRI Shapefile Technical Description, 1998 — รูปแบบไฟล์คงที่มาตั้งแต่นั้น ไม่มีการเปลี่ยนแปลง)
รองรับเฉพาะ Point (shape type 1) และ Polygon (shape type 5) ซึ่งเพียงพอสำหรับระบบนี้:
จุดหมุดหลักเขตแต่ละจุด (ทั้งแปลงหลักและแปลงข้างเคียง) + รูปปิดขอบเขตของแต่ละแปลงที่ลากเชื่อมหมุดตามลำดับ

พิกัดที่รับเข้ามาเป็น WGS84 (ละติจูด/ลองจิจูด องศาทศนิยม) ตรงกับพิกัดจาก GPS มือถือ/เบราว์เซอร์โดยตรง — ก่อนเขียน
ลงไฟล์จะถูกแปลงเป็นพิกัด UTM ระบบ "Indian 1975" (ดู services/thai_datum.py) เพื่อให้ import/export เข้ากับโปรแกรม
GIS ของหน่วยงานที่ยังอ้างอิงระบบพิกัดนี้อยู่ได้ (ตามที่ผู้ใช้ระบบร้องขอ) — เก็บพิกัด WGS84 ดั้งเดิมไว้เป็น attribute
อ้างอิงในตาราง .dbf ด้วยเผื่อต้องตรวจสอบย้อนหลัง
"""
import io
import struct
import zipfile

from services.thai_datum import prj_wkt_for_zone, wgs84_to_indian1975_utm

SHAPE_TYPE_POINT = 1
SHAPE_TYPE_POLYGON = 5


def _encode_field_text(value, length_bytes):
    """เข้ารหัส UTF-8 แล้วตัด/เติม space ให้พอดี length_bytes โดยไม่ตัดกลาง multi-byte character"""
    raw = str(value).encode("utf-8")
    if len(raw) > length_bytes:
        # ตัดทีละ byte จากท้ายจนกว่าจะ decode UTF-8 ได้สมบูรณ์ (กันตัดกลางตัวอักษรไทยที่เป็น multi-byte)
        raw = raw[:length_bytes]
        while raw:
            try:
                raw.decode("utf-8")
                break
            except UnicodeDecodeError:
                raw = raw[:-1]
    return raw.ljust(length_bytes, b" ")


def _dbf_bytes(fields, records):
    """fields: list of (name, type, length, decimals) — type 'C' (ข้อความ, UTF-8) หรือ 'N' (ตัวเลข)
    records: list of dict {field_name: value}
    """
    header_size = 32 + 32 * len(fields) + 1
    record_size = 1 + sum(f[2] for f in fields)
    buf = io.BytesIO()
    buf.write(bytes([0x03]))          # dBase III, ไม่มี memo
    buf.write(bytes([26, 1, 1]))      # วันที่แก้ไขล่าสุด (YY MM DD) — placeholder ไม่กระทบการอ่านข้อมูล
    buf.write(struct.pack("<I", len(records)))
    buf.write(struct.pack("<H", header_size))
    buf.write(struct.pack("<H", record_size))
    buf.write(b"\x00" * 20)
    for name, ftype, length, decimals in fields:
        buf.write(name.encode("ascii")[:10].ljust(11, b"\x00"))
        buf.write(ftype.encode("ascii"))
        buf.write(struct.pack("<I", 0))
        buf.write(bytes([length]))
        buf.write(bytes([decimals]))
        buf.write(b"\x00" * 14)
    buf.write(b"\x0d")  # ตัวคั่นจบส่วน field descriptor

    for rec in records:
        buf.write(b" ")  # deletion flag: เว้นวรรค = ยังไม่ถูกลบ
        for name, ftype, length, decimals in fields:
            value = rec.get(name, "")
            if ftype == "N":
                text = f"{float(value):.{decimals}f}" if decimals > 0 else str(int(value))
                buf.write(text[:length].rjust(length).encode("ascii"))
            else:
                buf.write(_encode_field_text(value, length))
    buf.write(b"\x1a")  # EOF marker
    return buf.getvalue()


def _shp_shx_bytes(shape_type, shapes):
    """shapes: list of {"record": bytes, "shx_entry": bytes, "xs": [...], "ys": [...]}
    คืนค่า (shp_bytes, shx_bytes)"""
    xs, ys = [], []
    for s in shapes:
        xs.extend(s["xs"])
        ys.extend(s["ys"])
    xmin, xmax = (min(xs), max(xs)) if xs else (0.0, 0.0)
    ymin, ymax = (min(ys), max(ys)) if ys else (0.0, 0.0)

    def header(file_len_words):
        h = struct.pack(">i", 9994) + b"\x00" * 20 + struct.pack(">i", file_len_words)
        h += struct.pack("<i", 1000) + struct.pack("<i", shape_type)
        h += struct.pack("<dddd", xmin, ymin, xmax, ymax)
        h += struct.pack("<dddd", 0.0, 0.0, 0.0, 0.0)
        return h

    shp_records = b"".join(s["record"] for s in shapes)
    shp_bytes = header((100 + len(shp_records)) // 2) + shp_records

    shx_records = b"".join(s["shx_entry"] for s in shapes)
    shx_bytes = header((100 + len(shx_records)) // 2) + shx_records

    return shp_bytes, shx_bytes


def _build_point_shapes(points):
    """points: list of (x, y) — คืน list ของ shape dict พร้อม offset (หน่วย word) ที่คำนวณเรียบร้อยแล้ว"""
    shapes = []
    offset_words = 50  # header .shp ยาว 100 byte = 50 word
    for i, (x, y) in enumerate(points, start=1):
        content = struct.pack("<i", SHAPE_TYPE_POINT) + struct.pack("<dd", x, y)
        content_words = len(content) // 2
        record = struct.pack(">ii", i, content_words) + content
        shx_entry = struct.pack(">ii", offset_words, content_words)
        shapes.append({"record": record, "shx_entry": shx_entry, "xs": [x], "ys": [y]})
        offset_words += 4 + content_words  # 4 word = ความยาว record header (8 byte)
    return shapes


def _ring_signed_area2(points):
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total


def _dedupe_consecutive(points):
    out = []
    for p in points:
        if not out or out[-1] != p:
            out.append(p)
    return out


def _build_polygon_shape(ring_points, record_id, offset_words):
    """ring_points: list of (x, y) จุดหมุดตามลำดับ (ยังไม่ปิดวง) — ฟังก์ชันนี้จะปิดวงและจัดทิศทาง
    ตามเข็มนาฬิกาให้อัตโนมัติ (ข้อกำหนดของ ESRI สำหรับเส้นรอบนอกของ polygon)
    record_id/offset_words: ให้ผู้เรียกกำหนดเอง เพราะ shapefile หนึ่งไฟล์อาจมีหลาย polygon (แปลงหลัก + แปลงข้างเคียง
    แต่ละแปลง) เรียงต่อกัน ไม่ใช่แค่ record เดียวเหมือนเดิม"""
    pts = _dedupe_consecutive(list(ring_points))
    if _ring_signed_area2(pts) > 0:  # บวก = ทวนเข็ม -> กลับทิศทางให้เป็นตามเข็ม
        pts.reverse()
    closed = pts + [pts[0]]

    xs = [p[0] for p in closed]
    ys = [p[1] for p in closed]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)

    content = struct.pack("<i", SHAPE_TYPE_POLYGON)
    content += struct.pack("<dddd", xmin, ymin, xmax, ymax)
    content += struct.pack("<i", 1)            # NumParts (มีวงเดียว ไม่มีรู)
    content += struct.pack("<i", len(closed))  # NumPoints
    content += struct.pack("<i", 0)            # Parts[0] = index เริ่มต้นของวงแรก
    for x, y in closed:
        content += struct.pack("<dd", x, y)

    content_words = len(content) // 2
    record = struct.pack(">ii", record_id, content_words) + content
    shx_entry = struct.pack(">ii", offset_words, content_words)
    return {"record": record, "shx_entry": shx_entry, "xs": xs, "ys": ys}


def build_marker_shapefile_zip(markers, prefix):
    """markers: list of dict {sequence_no, label, lat, lng, group} เรียงตามลำดับหมุดแล้ว (group=None หรือ "" คือ
    แปลงหลัก, ค่าอื่นคือชื่อแปลงข้างเคียงแต่ละแปลง — หมุดที่ group เดียวกันจะถูกลากปิดขอบเขตเป็นแปลงเดียวกัน)
    prefix: ชื่อไฟล์นำหน้า (ASCII เท่านั้น เช่น เลข รว.12 ที่ตัดอักขระพิเศษออกแล้ว)
    คืนค่า bytes ของไฟล์ .zip ที่รวม (พิกัดทุกจุดแปลงเป็น UTM ระบบ Indian 1975 แล้ว ดู services/thai_datum.py):
      - <prefix>_points.(shp|shx|dbf|prj|cpg) — จุดหมุดทุกจุด ทุกแปลง (หลัก + ข้างเคียง)
      - <prefix>_boundary.(shp|shx|dbf|prj|cpg) — รูปปิดขอบเขต 1 record ต่อ 1 แปลง (สร้างเฉพาะแปลงที่มีหมุด >= 3 จุด)
    """
    # ใช้ลองจิจูดเฉลี่ยของหมุดทั้งหมด (ทุกแปลง) ตัดสินใจเลือกโซน UTM เดียวกันตลอดทั้งไฟล์ — ปกติหมุดทั้งหมดของ
    # เรื่องเดียวกันอยู่ในพื้นที่เดียวกัน จึงไม่ควรมีบางจุดอยู่โซน 47 บางจุดอยู่โซน 48 ปะปนกันในไฟล์เดียว
    avg_lon = sum(m["lng"] for m in markers) / len(markers)
    projected = []
    zone_number = None
    for m in markers:
        easting, northing, zone_number, epsg_code = wgs84_to_indian1975_utm(m["lat"], m["lng"], zone_lon_deg=avg_lon)
        projected.append({**m, "easting": easting, "northing": northing})
    prj_wkt = prj_wkt_for_zone(zone_number)

    def group_label(m):
        g = (m.get("group") or "").strip()
        return g if g else "แปลงหลัก"

    point_shapes = _build_point_shapes([(m["easting"], m["northing"]) for m in projected])
    points_shp, points_shx = _shp_shx_bytes(SHAPE_TYPE_POINT, point_shapes)
    points_dbf = _dbf_bytes(
        [
            ("id", "N", 10, 0), ("label", "C", 60, 0), ("grp", "C", 60, 0),
            ("lat", "N", 18, 8), ("lng", "N", 18, 8),
            ("easting", "N", 18, 3), ("northing", "N", 18, 3),
        ],
        [
            {
                "id": m["sequence_no"],
                "label": m.get("label") or f"หมุด {m['sequence_no']}",
                "grp": group_label(m),
                "lat": m["lat"], "lng": m["lng"],
                "easting": m["easting"], "northing": m["northing"],
            }
            for m in projected
        ],
    )

    # จัดกลุ่มตามแปลง (คงลำดับการปรากฏครั้งแรกของแต่ละกลุ่มไว้ — แปลงหลักมักถูกเพิ่มก่อนจึงมักอยู่ลำดับแรกอยู่แล้ว)
    groups = {}
    for m in projected:
        groups.setdefault(group_label(m), []).append(m)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{prefix}_points.shp", points_shp)
        zf.writestr(f"{prefix}_points.shx", points_shx)
        zf.writestr(f"{prefix}_points.dbf", points_dbf)
        zf.writestr(f"{prefix}_points.prj", prj_wkt)
        zf.writestr(f"{prefix}_points.cpg", "UTF-8")

        polygon_shapes = []
        polygon_records = []
        offset_words = 50
        record_id = 1
        for label, pts in groups.items():
            if len(pts) < 3:
                continue
            ring = [(p["easting"], p["northing"]) for p in pts]
            shape = _build_polygon_shape(ring, record_id, offset_words)
            polygon_shapes.append(shape)
            _prev_offset, content_words = struct.unpack(">ii", shape["shx_entry"])  # content_words มาจาก shx_entry ตรงๆ
            offset_words += 4 + content_words  # 4 word = ความยาว record header (8 byte) ก่อนหน้า content ของ record นี้
            polygon_records.append(
                {
                    "id": record_id,
                    "case_code": prefix,
                    "grp": label,
                    "role": "MAIN" if label == "แปลงหลัก" else "NEIGHBOR",
                    "num_pts": len(pts),
                }
            )
            record_id += 1

        if polygon_shapes:
            polygon_shp, polygon_shx = _shp_shx_bytes(SHAPE_TYPE_POLYGON, polygon_shapes)
            polygon_dbf = _dbf_bytes(
                [
                    ("id", "N", 10, 0), ("case_code", "C", 40, 0), ("grp", "C", 60, 0),
                    ("role", "C", 10, 0), ("num_pts", "N", 10, 0),
                ],
                polygon_records,
            )
            zf.writestr(f"{prefix}_boundary.shp", polygon_shp)
            zf.writestr(f"{prefix}_boundary.shx", polygon_shx)
            zf.writestr(f"{prefix}_boundary.dbf", polygon_dbf)
            zf.writestr(f"{prefix}_boundary.prj", prj_wkt)
            zf.writestr(f"{prefix}_boundary.cpg", "UTF-8")

    return buf.getvalue()
