"""แยกพิกัด (lat/lng) จากลิงก์ Google Maps ที่ช่างรังวัดวางไว้ในช่อง "ตำแหน่งของที่ดิน" (parcels.location_url) —
ให้ระบบนำไปปักหมุดในหน้าแผนที่ช่างรังวัด (field-map.html) ได้เองโดยอัตโนมัติ โดยไม่ต้องให้ผู้ใช้กรอกพิกัดซ้ำอีก
ช่องหนึ่ง หรือต้องนำเข้า Shapefile สำหรับทุกเรื่อง (ดู upsert_parcel ใน blueprints/survey_cases.py ที่เรียกใช้)

วิธีทำงาน 2 ขั้น:
1. ลองอ่านพิกัดจากตัว URL ตรงๆ ก่อนด้วย regex (เร็ว ไม่ต้องต่ออินเทอร์เน็ต) — ใช้ได้กับลิงก์แบบเต็มที่คัดลอกจาก
   address bar เช่น .../@13.7563,100.5018,17z/... หรือ ?q=13.7563,100.5018
2. ถ้าเป็นลิงก์แบบย่อ (maps.app.goo.gl / goo.gl/maps ที่แอป Google Maps มือถือมักสร้างให้ตอนกด "แชร์") ซึ่งไม่มี
   พิกัดฝังอยู่ในตัวลิงก์เอง ค่อยลองตามลิงก์ไปดู URL ปลายทางจริง (ต้องต่ออินเทอร์เน็ต — เป็นจุดเดียวในระบบทั้งหมดที่
   ฝั่ง backend ต้องยิง request ออกไปหาบริการภายนอก) แล้วอ่านพิกัดจาก URL ปลายทางนั้นแทน

ใช้ urllib ในตัว Python เท่านั้น ไม่เพิ่มไลบรารีภายนอกอย่าง requests (ตามหลักการเดิมของระบบใน requirements.txt ที่
ตั้งใจใช้เฉพาะไลบรารีน้ำหนักเบา) และล้มเหลวแบบเงียบๆ เสมอ (คืน (None, None)) — ไม่ปล่อยให้ exception ใดๆ หลุดออก
จากไฟล์นี้ไปกระทบการบันทึกข้อมูลแปลงส่วนอื่น เพราะนี่เป็นแค่ความสะดวกเสริม ไม่ใช่ข้อมูลที่ต้องมีเพื่อบันทึกได้สำเร็จ

ข้อจำกัดที่ทราบอยู่แล้ว: บางลิงก์ย่ออาจ redirect ไปเจอหน้ายืนยันความยินยอม (consent) ของ Google ก่อนถึงหน้าจริง
(พบมากในบางภูมิภาค) ซึ่งฟังก์ชันนี้ไม่ได้ทำ flow ยืนยันความยินยอมให้ — กรณีนั้นจะดึงพิกัดไม่ได้ (คืน None) ผู้ใช้
ยังสามารถแก้ปัญหาเองได้โดยเปิดลิงก์ในเบราว์เซอร์แล้ววาง URL เต็มที่ได้จาก address bar แทนลิงก์แบบย่อ
"""
import re
import urllib.error
import urllib.parse
import urllib.request

_SHORT_LINK_HOSTS = ("goo.gl", "maps.app.goo.gl")

# ลำดับความสำคัญจากแม่นยำมากไปน้อย: !3d..!4d.. คือพิกัดของหมุดที่ปักจริงในลิงก์แบบ /maps/place/...
# (แม่นยำกว่า @lat,lng ซึ่งเป็นแค่จุดกึ่งกลางมุมมองแผนที่ตอนแชร์ลิงก์ อาจไม่ตรงหมุดเป๊ะๆ ถ้าผู้ใช้เลื่อนแผนที่ก่อนแชร์)
_PATTERNS = [
    re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)"),
    re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"[?&]q=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"[?&]ll=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)"),
]


def _try_regex(url: str):
    decoded = urllib.parse.unquote(url)
    for pattern in _PATTERNS:
        m = pattern.search(decoded)
        if m:
            try:
                lat, lng = float(m.group(1)), float(m.group(2))
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    return lat, lng
            except ValueError:
                continue
    return None, None


def _is_short_link(url: str) -> bool:
    try:
        host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/")[0].lower()
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in _SHORT_LINK_HOSTS)


def _resolve_short_link(url: str, timeout: float = 4.0):
    """ตามลิงก์แบบย่อไปดู URL ปลายทางจริง — Google ตอบกลับด้วย HTTP redirect ธรรมดา ไม่ต้องรัน JavaScript จึงยัง
    ใช้ urllib ธรรมดาได้ ต้องใส่ User-Agent เหมือนเบราว์เซอร์จริง ไม่งั้น Google บางกรณีปฏิเสธคำขอ — คืน None เสมอ
    เมื่อล้มเหลวไม่ว่าด้วยสาเหตุใด (timeout/บล็อก/รูปแบบเปลี่ยนไป) เพราะนี่เป็นทางเลือกเสริมเท่านั้น"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DOLSurveyLogbook/1.0)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except Exception:
        return None


def extract_coords_from_maps_url(url: str):
    """คืน (lat, lng) หรือ (None, None) ถ้าดึงไม่ได้ — ไม่โยน exception ออกไปเด็ดขาด (เรียกใช้จาก upsert_parcel
    ตอนบันทึกฟอร์ม ห้ามทำให้การบันทึกล้มเหลวเพราะฟังก์ชันนี้)"""
    if not url:
        return None, None
    url = url.strip()
    try:
        lat, lng = _try_regex(url)
        if lat is not None:
            return lat, lng
        if _is_short_link(url):
            resolved = _resolve_short_link(url)
            if resolved:
                return _try_regex(resolved)
    except Exception:
        pass
    return None, None
