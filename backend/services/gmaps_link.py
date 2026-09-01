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

ข้อจำกัดที่เคยพบและแก้ไว้แล้วด้านล่าง: บางลิงก์ย่อ redirect ไปเจอ "หน้ายืนยันความยินยอม" (consent.google.com) ของ
Google ก่อนถึงหน้าจริง (พบมากถ้า IP ของเซิร์ฟเวอร์ที่รันโฮสต์อยู่ถูกจัดว่าอยู่ในภูมิภาคที่ Google บังคับให้ยืนยันก่อน)
หรือ redirect ไปเจอหน้าพรีวิวของแอป (สำหรับ user-agent ที่ไม่ใช่เบราว์เซอร์ทั่วไป) ซึ่งไม่มี pattern พิกัดตรงๆ ใน URL
— แก้โดย (1) แนบ cookie ที่บอกว่า "ยืนยันแล้ว" ไปกับคำขอตั้งแต่แรกเพื่อข้ามหน้ายืนยันไปเลยถ้าทำได้ (2) ถ้ายังเจอหน้า
ยืนยันอยู่ดี ให้ดึงพิกัดจากพารามิเตอร์ continue= ของหน้านั้นแทน (Google ฝัง URL ปลายทางจริงไว้ในพารามิเตอร์นี้) และ
(3) ถ้ายังไม่เจอพิกัดใน URL เลย ให้ลองค้นในเนื้อหา HTML ของหน้าปลายทางด้วย (พิกัดมักฝังอยู่ในสคริปต์/แท็กเมตาของหน้า
แม้ตัว URL เองจะไม่มี) กรณีที่ยังดึงไม่ได้จริงๆ (เช่นเครือข่ายเซิร์ฟเวอร์ถูกบล็อกทั้งหมด) ผู้ใช้ยังแก้ไขเองได้โดยเปิด
ลิงก์ในเบราว์เซอร์แล้ววาง URL เต็มที่ได้จาก address bar แทนลิงก์แบบย่อ
"""
import re
import urllib.error
import urllib.parse
import urllib.request

_SHORT_LINK_HOSTS = ("goo.gl", "maps.app.goo.gl")

# ลำดับความสำคัญจากแม่นยำมากไปน้อย: !3d..!4d.. คือพิกัดของหมุดที่ปักจริงในลิงก์แบบ /maps/place/...
# (แม่นยำกว่า @lat,lng ซึ่งเป็นแค่จุดกึ่งกลางมุมมองแผนที่ตอนแชร์ลิงก์ อาจไม่ตรงหมุดเป๊ะๆ ถ้าผู้ใช้เลื่อนแผนที่ก่อนแชร์)
# สองรายการสุดท้าย (center=, "lat":.."lng":..) ไว้จับพิกัดที่ฝังอยู่ในเนื้อหา HTML ของหน้าปลายทาง (เช่น URL รูปแผนที่
# static หรือ JSON ภายในหน้า) กรณีตัว URL เองไม่มีพิกัดฝังอยู่ตรงๆ — ดู _try_regex ที่เรียกกับทั้ง URL และเนื้อหาหน้า
_PATTERNS = [
    re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)"),
    re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"[?&]q=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"[?&]ll=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"[?&]center=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)", re.IGNORECASE),
    re.compile(r'"lat"\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*"lng"\s*:\s*(-?\d{1,3}\.\d+)'),
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


def _resolve_short_link(url: str, timeout: float = 6.0):
    """ตามลิงก์แบบย่อไปดู URL ปลายทางจริง — คืน (final_url, body_text) หรือ (None, None) เมื่อล้มเหลวไม่ว่าด้วย
    สาเหตุใด (timeout/บล็อก/รูปแบบเปลี่ยนไป) เพราะนี่เป็นทางเลือกเสริมเท่านั้น
    - ใช้ User-Agent ของเบราว์เซอร์เดสก์ท็อปจริง (ลด​โอกาสโดน Google มองว่าเป็นบอทแล้วปฏิเสธ/ตอบหน้าพรีวิวแทน)
    - แนบ cookie CONSENT=YES+1 (ค่าที่ใช้กันทั่วไปเพื่อบอก Google ว่า "ยืนยันเงื่อนไขแล้ว") พยายามข้ามหน้ายืนยันความ
      ยินยอมไปตั้งแต่คำขอแรกโดยไม่ต้องยื่นฟอร์มยืนยันจริง
    - อ่านเนื้อหาหน้าปลายทางกลับมาด้วย (จำกัดไม่เกิน ~300KB) เผื่อพิกัดไม่ได้ฝังอยู่ใน URL ตรงๆ แต่ฝังอยู่ในเนื้อหา
      หน้า (เช่น URL รูปแผนที่ static หรือ JSON ภายในหน้า) — ดู extract_coords_from_maps_url ที่ค้นทั้งสองที่"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Cookie": "CONSENT=YES+1",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            body = resp.read(300_000).decode("utf-8", errors="ignore")
            return final_url, body
    except Exception:
        return None, None


def _extract_consent_continue(url: str):
    """ถ้า URL ปลายทางที่ตามไปเจอ เป็นหน้ายืนยันความยินยอมของ Google (consent.google.com) แทนที่จะเป็นหน้าจริง —
    ดึงพารามิเตอร์ continue= ออกมาแทน เพราะ Google ฝัง URL ปลายทางจริง (ซึ่งมีพิกัดที่ต้องการอยู่) ไว้ในพารามิเตอร์
    นี้อยู่แล้ว ไม่ต้องยื่นฟอร์มยืนยันจริงๆ ก็ดึงพิกัดได้ — คืน None ถ้าไม่ใช่หน้ายืนยัน หรือไม่มีพารามิเตอร์นี้"""
    try:
        parsed = urllib.parse.urlparse(url)
        if "consent.google" not in parsed.netloc.lower():
            return None
        qs = urllib.parse.parse_qs(parsed.query)
        return qs.get("continue", [None])[0]
    except Exception:
        return None


def extract_coords_from_maps_url(url: str):
    """คืน (lat, lng) หรือ (None, None) ถ้าดึงไม่ได้ — ไม่โยน exception ออกไปเด็ดขาด (เรียกใช้จาก upsert_parcel
    ตอนบันทึกฟอร์ม ห้ามทำให้การบันทึกล้มเหลวเพราะฟังก์ชันนี้)
    ลำดับที่ลอง: (1) regex ตรงจาก URL ที่กรอกมา (2) ถ้าเป็นลิงก์ย่อ ตามไปดู URL ปลายทางแล้วลอง regex อีกครั้ง
    (3) ถ้าปลายทางเป็นหน้ายืนยันความยินยอม ลองดึงจากพารามิเตอร์ continue= (4) ถ้ายังไม่เจอ ลองค้นในเนื้อหา HTML ของ
    หน้าปลายทางแทน — แต่ละขั้นล้มเหลวแบบเงียบๆ แล้วลองขั้นถัดไปเรื่อยๆ จนกว่าจะหมดทางเลือก"""
    if not url:
        return None, None
    url = url.strip()
    try:
        lat, lng = _try_regex(url)
        if lat is not None:
            return lat, lng
        if not _is_short_link(url):
            return None, None

        final_url, body = _resolve_short_link(url)
        if final_url:
            lat, lng = _try_regex(final_url)
            if lat is not None:
                return lat, lng

            continue_url = _extract_consent_continue(final_url)
            if continue_url:
                lat, lng = _try_regex(continue_url)
                if lat is not None:
                    return lat, lng

        if body:
            lat, lng = _try_regex(body)
            if lat is not None:
                return lat, lng
    except Exception:
        pass
    return None, None
