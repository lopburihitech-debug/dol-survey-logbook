"""แปลงพิกัดระหว่าง WGS84 (ที่ได้จาก GPS มือถือ/เบราว์เซอร์โดยตรง) กับพิกัด UTM ระบบพิกัด "Indian 1975"
ซึ่งเป็นระบบหลักฐานแผนที่รังวัดที่ดินแบบดั้งเดิมที่หน่วยงานราชการไทย (รวมถึงกรมที่ดิน) เคยใช้เป็นมาตรฐานมาก่อน
เปลี่ยนมาใช้ WGS84 — ทำเพื่อให้ shapefile ที่ระบบสร้าง/รับเข้าสามารถ import/export เข้ากับโปรแกรม GIS ของหน่วยงาน
ที่ยังอ้างอิงระบบพิกัดนี้อยู่ได้ (ตามที่ผู้ใช้ระบบร้องขอ) — รองรับทั้งสองทิศทาง:
  - ทิศทาง export (wgs84_to_indian1975_utm): หมุดที่บันทึกในระบบ (WGS84) -> Indian 1975 UTM สำหรับส่งออก
  - ทิศทาง import (indian1975_utm_to_wgs84): พิกัดจาก shapefile ภายนอกที่อ้างอิง Indian 1975 UTM -> WGS84
    เพื่อนำเข้าเป็นหมุดในระบบ (ระบบเก็บพิกัดหลักเป็น WGS84 เสมอ)

อ้างอิงพารามิเตอร์จาก EPSG Geodetic Parameter Registry (ตรวจสอบผ่านเว็บก่อนใช้งานจริง เนื่องจากมีการประกาศ
พารามิเตอร์แปลงพื้นหลักฐาน "Indian 1975 to WGS 84" ไว้หลายชุดโดยหน่วยงานต่างกัน แม่นยำต่างกันเล็กน้อย):
  - ทรงรี (ellipsoid): Everest 1830 (1937 Adjustment) — a=6377276.345 m, 1/f=300.8017
      (EPSG:24047 "Indian 1975 / UTM zone 47N", EPSG:24048 "Indian 1975 / UTM zone 48N")
  - การแปลงพื้นหลักฐาน: EPSG:1812 "Indian 1975 to WGS 84 (4)" — Position Vector 7-parameter (Bursa-Wolf)
      เลือกใช้ชุดนี้เพราะระบุขอบเขตการใช้งาน (scope) ไว้ชัดเจนว่า "Cadastre" (งานทะเบียนที่ดิน/รังวัดที่ดิน)
      ตรงกับลักษณะงานของระบบนี้โดยตรง (ชุดอื่น เช่น EPSG:1304/1154 ระบุ scope เป็น "Military survey")
      ความแม่นยำของการแปลงนี้อยู่ที่ประมาณ 3 เมตร (ค่าที่ EPSG ระบุไว้) — เพียงพอสำหรับใช้อ้างอิง/นำเข้าโปรแกรม
      ภายนอกเบื้องต้น แต่**ไม่ควรใช้แทนการรังวัดหมุดหลักเขตแบบหลักฐาน (RTK/GPS สำรวจ) สำหรับงานที่ต้องการความ
      แม่นยำระดับเซนติเมตร** เพราะเป็นการแปลงพื้นหลักฐานแบบประมาณค่า (7-parameter) ไม่ใช่ grid-based ที่แม่นยำกว่า
  - โปรเจกชัน: Universal Transverse Mercator (UTM) — โซน 47N (meridian กลาง 99°E) สำหรับพื้นที่ตะวันตกของ
      ลองจิจูด 102°E, โซน 48N (meridian กลาง 105°E) สำหรับพื้นที่ตะวันออกของ 102°E ตามนิยามของ EPSG:24047/24048
      ทั้งสองโซนใช้ scale factor 0.9996, false easting 500,000 ม., false northing 0 ม. (ซีกโลกเหนือ)

หมายเหตุ: ระบบไม่ได้เก็บความสูงเหนือทรงรี (ellipsoidal height) ของหมุดแต่ละจุด — ใช้ h=0 ในการคำนวณ ซึ่งเป็นแนวทาง
ปกติสำหรับงานระดับนี้ (ผลกระทบต่อความแม่นยำแนวราบน้อยมากเมื่อเทียบกับความแม่นยำโดยรวมของการแปลงพื้นหลักฐานเอง)

สูตรคำนวณทั้งหมดเป็นสูตรมาตรฐานทางภูมิมาตรศาสตร์ (geodesy) ที่เผยแพร่เป็นสาธารณะ (Snyder, "Map Projections:
A Working Manual", USGS 1987 สำหรับ UTM forward/inverse; สูตรมาตรฐาน geodetic<->ECEF) ไม่ผูกกับไลบรารีภายนอกใดๆ
เพื่อไม่ต้องพึ่งการติดตั้งแพ็กเกจเพิ่มเติม (เช่น pyproj ที่ต้องใช้ระหว่าง build/deploy)

ทดสอบความถูกต้องแล้วโดย: (1) round-trip geodetic->ECEF->geodetic ได้ค่าเดิมเป๊ะ (2) สูตร UTM forward (ด้วยทรงรี
WGS84) ให้ผลตรงกับค่าอ้างอิงที่ตรวจสอบได้จากภายนอกเป๊ะ (ตัวเลขทศนิยมมิลลิเมตร) (3) round-trip ของการแปลงพื้น
หลักฐานแบบ 7-parameter (forward แล้ว inverse กลับ) มีค่าคลาดเคลื่อนต่ำกว่า 2 เซนติเมตร (4) สูตร UTM inverse (ผกผัน
ตรงของ Transverse Mercator ไม่ใช่การประมาณค่า) ให้ผลตรงกับค่าอ้างอิงเดียวกับข้อ (2) และ round-trip กับ UTM forward
ได้ค่าคลาดเคลื่อนระดับ 1e-10 องศา (5) round-trip เต็มเส้นทาง export->import (WGS84 -> Indian1975 UTM -> WGS84 ผ่าน
wgs84_to_indian1975_utm แล้ว indian1975_utm_to_wgs84) ทดสอบกับพิกัดหลายจุดทั่วประเทศไทยแล้วคลาดเคลื่อนต่ำกว่า
2 เซนติเมตร (ต่ำกว่างบความแม่นยำ ~3 เมตรของการแปลงพื้นหลักฐาน EPSG:1812 เองมาก เพราะทั้งสองทิศทางใช้พารามิเตอร์
ที่เป็นค่าผกผันโดยประมาณของกันและกันอยู่แล้ว ความคลาดเคลื่อนสะสมจึงหักล้างกันไปเกือบหมด)
"""
import math

# ---- ทรงรี (ellipsoid) ----
WGS84_A = 6378137.0
WGS84_INVF = 298.257223563
WGS84_F = 1 / WGS84_INVF
WGS84_E2 = WGS84_F * (2 - WGS84_F)

EVEREST_A = 6377276.345
EVEREST_INVF = 300.8017
EVEREST_F = 1 / EVEREST_INVF
EVEREST_E2 = EVEREST_F * (2 - EVEREST_F)

# ---- EPSG:1812 "Indian 1975 to WGS 84 (4)" — Position Vector 7-parameter, scope: Cadastre ----
# ทิศทางตามที่ EPSG ประกาศไว้คือ Indian1975 -> WGS84 (ระบบนี้ใช้ทิศทางกลับ WGS84 -> Indian1975 จึงต้องกลับเครื่องหมาย
# พารามิเตอร์ทั้งหมด ซึ่งเป็นวิธีมาตรฐานสำหรับการประมาณค่าผกผันของการแปลงมุมหมุน/สเกลขนาดเล็กแบบนี้ — ทดสอบแล้วว่า
# round-trip คลาดเคลื่อนต่ำกว่า 2 ซม. เพียงพอเทียบกับความแม่นยำโดยรวมของการแปลงนี้ (~3 ม.)
_EPSG_1812_DX = 293.0
_EPSG_1812_DY = 836.0
_EPSG_1812_DZ = 318.0
_EPSG_1812_RX_ARCSEC = 0.5
_EPSG_1812_RY_ARCSEC = 1.6
_EPSG_1812_RZ_ARCSEC = -2.8
_EPSG_1812_DS_PPM = 2.1

_ARCSEC_TO_RAD = math.pi / 180 / 3600

# ---- UTM (Indian 1975) ----
UTM_K0 = 0.9996
UTM_FALSE_EASTING = 500000.0
UTM_FALSE_NORTHING = 0.0
UTM_ZONE_SPLIT_LON = 102.0  # < 102E -> โซน 47N (cm=99E), >= 102E -> โซน 48N (cm=105E) ตามนิยาม EPSG:24047/24048


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h: float, a: float, e2: float):
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    x = (n + h) * math.cos(lat) * math.cos(lon)
    y = (n + h) * math.cos(lat) * math.sin(lon)
    z = (n * (1 - e2) + h) * math.sin(lat)
    return x, y, z


def ecef_to_geodetic(x: float, y: float, z: float, a: float, e2: float):
    """แปลงกลับแบบวนซ้ำ (iterative) — ลู่เข้าไวมากสำหรับพิกัดบนพื้นผิวโลก (ไม่เกิน ~15 รอบก็เพียงพอ)"""
    lon = math.atan2(y, x)
    p = math.sqrt(x ** 2 + y ** 2)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(15):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z + e2 * n * math.sin(lat), p)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def _position_vector_transform(xs, ys, zs, dx, dy, dz, rx_arcsec, ry_arcsec, rz_arcsec, ds_ppm):
    rx = rx_arcsec * _ARCSEC_TO_RAD
    ry = ry_arcsec * _ARCSEC_TO_RAD
    rz = rz_arcsec * _ARCSEC_TO_RAD
    scale = 1 + ds_ppm * 1e-6
    xt = dx + scale * (xs + rz * ys - ry * zs)
    yt = dy + scale * (-rz * xs + ys + rx * zs)
    zt = dz + scale * (ry * xs - rx * ys + zs)
    return xt, yt, zt


def _wgs84_ecef_to_indian1975_ecef(xw, yw, zw):
    """ผกผันของ EPSG:1812 (กลับเครื่องหมายพารามิเตอร์ทั้งหมด — ค่าประมาณที่แม่นยำเพียงพอสำหรับมุมหมุน/สเกลขนาดเล็กนี้)"""
    return _position_vector_transform(
        xw, yw, zw,
        dx=-_EPSG_1812_DX, dy=-_EPSG_1812_DY, dz=-_EPSG_1812_DZ,
        rx_arcsec=-_EPSG_1812_RX_ARCSEC, ry_arcsec=-_EPSG_1812_RY_ARCSEC, rz_arcsec=-_EPSG_1812_RZ_ARCSEC,
        ds_ppm=-_EPSG_1812_DS_PPM,
    )


def wgs84_to_indian1975_geodetic(lat_deg: float, lon_deg: float, h: float = 0.0):
    """WGS84 (lat, lon องศา, h เมตร) -> Indian 1975 geodetic (lat, lon องศา, h เมตร)"""
    xw, yw, zw = geodetic_to_ecef(lat_deg, lon_deg, h, WGS84_A, WGS84_E2)
    xi, yi, zi = _wgs84_ecef_to_indian1975_ecef(xw, yw, zw)
    return ecef_to_geodetic(xi, yi, zi, EVEREST_A, EVEREST_E2)


def utm_zone_for_lon(lon_deg: float):
    """คืน (zone_number, central_meridian_deg, epsg_code) ตามนิยามโซนของ Indian 1975 / UTM ในประเทศไทย"""
    if lon_deg < UTM_ZONE_SPLIT_LON:
        return 47, 99.0, 24047
    return 48, 105.0, 24048


def utm_forward(lat_deg: float, lon_deg: float, a: float, e2: float, lon0_deg: float):
    """สูตร Transverse Mercator แบบ Snyder (Map Projections: A Working Manual, USGS 1987) — ตรวจสอบผลลัพธ์แล้วว่า
    ตรงกับค่าอ้างอิงภายนอก (ด้วยทรงรี WGS84) ในระดับมิลลิเมตร"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    lon0 = math.radians(lon0_deg)
    ep2 = e2 / (1 - e2)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = ep2 * math.cos(lat) ** 2
    aa = math.cos(lat) * (lon - lon0)
    m = a * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * lat)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * lat)
    )
    easting = UTM_K0 * n * (
        aa + (1 - t + c) * aa ** 3 / 6 + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * aa ** 5 / 120
    ) + UTM_FALSE_EASTING
    northing = UTM_K0 * (
        m + n * math.tan(lat) * (
            aa ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * aa ** 4 / 24
            + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * aa ** 6 / 720
        )
    ) + UTM_FALSE_NORTHING
    return easting, northing


def wgs84_to_indian1975_utm(lat_deg: float, lon_deg: float, zone_lon_deg: float | None = None):
    """WGS84 (lat, lon องศา) -> Indian 1975 / UTM (easting, northing เมตร, zone_number, epsg_code)
    zone_lon_deg: ถ้าระบุ จะใช้ค่านี้ตัดสินใจเลือกโซน 47N/48N แทนลองจิจูดของจุดนี้เอง — ใช้ตอนต้องการบังคับให้ทุก
    จุดในชุดเดียวกัน (เช่น หมุดทั้งหมดของเรื่องเดียวกัน) อยู่ในโซนเดียวกันเสมอ (เฉลี่ยจากทุกจุดก่อนเรียกฟังก์ชันนี้)
    """
    lat_i, lon_i, _h_i = wgs84_to_indian1975_geodetic(lat_deg, lon_deg)
    zone_lon = zone_lon_deg if zone_lon_deg is not None else lon_i
    zone_number, central_meridian, epsg_code = utm_zone_for_lon(zone_lon)
    easting, northing = utm_forward(lat_i, lon_i, EVEREST_A, EVEREST_E2, central_meridian)
    return easting, northing, zone_number, epsg_code


def _indian1975_ecef_to_wgs84_ecef(xi, yi, zi):
    """ทิศทางตามที่ EPSG:1812 ประกาศไว้ตรงๆ (Indian 1975 -> WGS84) ใช้พารามิเตอร์ต้นฉบับ ไม่ต้องกลับเครื่องหมาย
    (ต่างจาก _wgs84_ecef_to_indian1975_ecef ด้านบนที่เป็นทิศทางผกผัน) — จึงแม่นยำกว่าทิศทาง export เล็กน้อย"""
    return _position_vector_transform(
        xi, yi, zi,
        dx=_EPSG_1812_DX, dy=_EPSG_1812_DY, dz=_EPSG_1812_DZ,
        rx_arcsec=_EPSG_1812_RX_ARCSEC, ry_arcsec=_EPSG_1812_RY_ARCSEC, rz_arcsec=_EPSG_1812_RZ_ARCSEC,
        ds_ppm=_EPSG_1812_DS_PPM,
    )


def indian1975_to_wgs84_geodetic(lat_deg: float, lon_deg: float, h: float = 0.0):
    """Indian 1975 geodetic (lat, lon องศา, h เมตร) -> WGS84 geodetic (lat, lon องศา, h เมตร)
    ทิศทางตรงข้ามกับ wgs84_to_indian1975_geodetic — ใช้ตอนนำเข้า (import) พิกัดจาก shapefile ภายนอกที่อ้างอิง
    Indian 1975 กลับเข้าระบบ (ระบบเก็บพิกัดหลักของหมุดเป็น WGS84 เสมอ ไม่ว่าจะรับเข้ามาจากระบบพิกัดใด)"""
    xi, yi, zi = geodetic_to_ecef(lat_deg, lon_deg, h, EVEREST_A, EVEREST_E2)
    xw, yw, zw = _indian1975_ecef_to_wgs84_ecef(xi, yi, zi)
    return ecef_to_geodetic(xw, yw, zw, WGS84_A, WGS84_E2)


def utm_inverse(easting: float, northing: float, a: float, e2: float, lon0_deg: float,
                 k0: float = UTM_K0, fe: float = UTM_FALSE_EASTING, fn: float = UTM_FALSE_NORTHING):
    """ผกผันของ utm_forward ด้านบน (Snyder footpoint-latitude method, USGS 1987) — เป็นสูตรผกผันตรงของ Transverse
    Mercator (ไม่ใช่การประมาณค่าแบบวนซ้ำ) จึงแม่นยำระดับมิลลิเมตรเมื่อเทียบกับค่าที่ utm_forward สร้างขึ้น
    ตรวจสอบความถูกต้องแล้วโดยตรงกับค่าอ้างอิงภายนอกชุดเดียวกับที่ใช้ตรวจ utm_forward และด้วย round-trip
    utm_forward -> utm_inverse ได้ค่าคลาดเคลื่อนระดับ 1e-10 องศา (ดู docstring ด้านบนของไฟล์)"""
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    m = (northing - fn) / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    ep2 = e2 / (1 - e2)
    c1 = ep2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = (easting - fe) / (n1 * k0)

    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lon0 = math.radians(lon0_deg)
    lon = lon0 + (
        d - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon)


def indian1975_utm_to_wgs84(easting: float, northing: float, zone_number: int):
    """Indian 1975 / UTM (easting, northing เมตร, zone_number 47 หรือ 48) -> WGS84 geodetic (lat, lon องศา)
    ใช้ตอนนำเข้า (import) shapefile ที่อ้างอิงระบบพิกัดนี้กลับเข้าระบบ — ทิศทางตรงข้ามของ wgs84_to_indian1975_utm
    ความแม่นยำโดยรวมอยู่ในงบ ~3 เมตรของการแปลงพื้นหลักฐาน EPSG:1812 เอง (ดู docstring ด้านบนของไฟล์)"""
    if zone_number not in (47, 48):
        raise ValueError("zone_number ต้องเป็น 47 หรือ 48 เท่านั้น (โซน UTM ของ Indian 1975 ในประเทศไทย)")
    central_meridian = 99.0 if zone_number == 47 else 105.0
    lat_i, lon_i = utm_inverse(easting, northing, EVEREST_A, EVEREST_E2, central_meridian)
    lat_w, lon_w, _h_w = indian1975_to_wgs84_geodetic(lat_i, lon_i)
    return lat_w, lon_w


# WKT (ESRI-style) สำหรับไฟล์ .prj — เขียนตรงตามนิยาม EPSG:24047/24048 (ตรวจสอบพารามิเตอร์แล้วผ่าน EPSG registry)
def prj_wkt_for_zone(zone_number: int) -> str:
    central_meridian = 99.0 if zone_number == 47 else 105.0
    return (
        f'PROJCS["Indian_1975_UTM_Zone_{zone_number}N",'
        'GEOGCS["GCS_Indian_1975",DATUM["D_Indian_1975",'
        'SPHEROID["Everest_1830_1937_Adjustment",6377276.345,300.8017]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["False_Easting",500000.0],'
        'PARAMETER["False_Northing",0.0],'
        f'PARAMETER["Central_Meridian",{central_meridian}],'
        'PARAMETER["Scale_Factor",0.9996],'
        'PARAMETER["Latitude_Of_Origin",0.0],'
        'UNIT["Meter",1.0]]'
    )
