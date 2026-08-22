"""ทดสอบหน้า "รายการงานรังวัด" (cases.html) 3 ส่วน:

1. คอลัมน์ "เกินนัดรังวัด" (นับจากวันนัดรังวัด แสดงป้าย "เกิน 30 วัน" / "เกิน 60 วัน") — สร้างเรื่องทดสอบผ่าน API
   โดยตรง (กำหนด appointment_date แบบเจาะจงย้อนหลังกี่วันได้ง่ายกว่าทำผ่าน UI) แล้วตรวจสอบว่าแต่ละแถวแสดงผล
   ตามเงื่อนไขที่ตกลงกับผู้ใช้ไว้ถูกต้อง:
     - ยังไม่เกิน 30 วัน หรือไม่มีวันนัดรังวัด -> แสดง "-"
     - เกิน 30 วันขึ้นไป (แต่ไม่ถึง 60) -> ป้าย "เกิน 30 วัน"
     - เกิน 60 วันขึ้นไป -> ป้าย "เกิน 60 วัน"
     - งานที่จบแล้ว (ถอนจ่ายแล้ว/ยกเลิก/งดรังวัด/นัดตรวจสอบใหม่/เลื่อนรังวัด) -> แสดง "-" เสมอ แม้จะเกินนัดมานานแค่ไหน

2. ตัวกรองสถานะ (#statusFilter) ต้องมีเฉพาะ 9 สถานะที่เรื่องในระบบนี้เป็นได้จริงตาม ALLOWED_TRANSITIONS ฝั่ง
   backend ไม่รวมสถานะเก่าจาก System Blueprint เดิมที่ตัดออกจาก workflow แล้ว (เช่น กำลังปฏิบัติงาน, รอตรวจ QC,
   เสร็จสิ้น ฯลฯ) เพราะเลือกแล้วจะไม่มีทางพบเรื่องใดๆ เลย

3. ตัวกรองช่างรังวัด (#surveyorFilter) — เลือกช่างคนใดคนหนึ่งแล้วต้องเห็นเฉพาะเรื่องที่ช่างคนนั้นรับผิดชอบอยู่
   (is_active = 1 ใน case_assignments) เลือก "ยังไม่มอบหมาย" แล้วต้องเห็นเฉพาะเรื่องที่ไม่มีช่างรับผิดชอบ

4. ตัวกรองประเภทงาน (#typeFilter) — เลือกประเภทงานใดประเภทหนึ่งแล้วต้องเห็นเฉพาะเรื่องที่เป็นประเภทงานนั้น

5. ปุ่ม "ล้างตัวกรอง" — ต้องรีเซ็ตคำค้นหาและตัวกรองสถานะ/ประเภทงาน/ช่างรังวัดทั้งหมดกลับเป็นค่าเริ่มต้นในคลิกเดียว
"""
import json
import random
import urllib.request
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
API = f"{BASE}/api/v1"


def days_ago_iso(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


def _request(method, path, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read().decode("utf-8"))


def login_api(username, password):
    data = _request("POST", "/auth/login", payload={"username": username, "password": password})
    return data["access_token"]


def create_case(token, office_id, type_id, code, appointment_date=None, surveyor_id=None):
    payload = {
        "office_id": office_id,
        "case_code": code,
        "survey_type_id": type_id,
        "requester_name": "ทดสอบคอลัมน์เกินนัด",
        "received_date": "2026-08-01",
    }
    if appointment_date:
        payload["appointment_date"] = appointment_date
    if surveyor_id:
        payload["surveyor_id"] = surveyor_id
    return _request("POST", "/survey-cases", token=token, payload=payload)


def set_status(token, case_id, new_status):
    return _request(
        "PATCH", f"/survey-cases/{case_id}/status", token=token,
        payload={"new_status": new_status, "reason": "ทดสอบ"},
    )


def login_ui(page, username, password):
    page.goto(f"{BASE}/login.html")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#loginForm button[type=submit]")
    page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)


def main():
    token = login_api("admin", "Admin@12345")
    office_id = _request("GET", "/offices", token=token)[0]["id"]
    all_types = _request("GET", "/survey-types", token=token)
    type_id = all_types[0]["id"]
    type_id_2 = all_types[1]["id"]
    type_name_2 = all_types[1]["name"]
    surveyors = _request("GET", "/surveyors", token=token)
    surveyor = surveyors[0]

    suffix = random.randint(1000, 9999)
    prefix = f"OD{suffix}"

    case_a = create_case(token, office_id, type_id, f"{prefix}-A", days_ago_iso(5))       # ยังไม่เกิน 30 วัน
    case_b = create_case(token, office_id, type_id, f"{prefix}-B", days_ago_iso(35))      # เกิน 30 วัน
    case_c = create_case(token, office_id, type_id, f"{prefix}-C", days_ago_iso(65))      # เกิน 60 วัน
    case_d = create_case(token, office_id, type_id, f"{prefix}-D", None)                  # ไม่มีวันนัดรังวัด
    case_e = create_case(token, office_id, type_id, f"{prefix}-E", days_ago_iso(90))      # เกินนัดมาก แต่จะยกเลิก
    set_status(token, case_e["id"], "CANCELLED")

    sv_suffix = random.randint(1000, 9999)
    sv_prefix = f"SV{sv_suffix}"
    case_f = create_case(token, office_id, type_id, f"{sv_prefix}-F", surveyor_id=surveyor["id"])  # มีช่างรับผิดชอบ
    case_g = create_case(token, office_id, type_id, f"{sv_prefix}-G")                              # ยังไม่มอบหมาย

    tf_suffix = random.randint(1000, 9999)
    tf_prefix = f"TF{tf_suffix}"
    case_h = create_case(token, office_id, type_id, f"{tf_prefix}-H")     # ประเภทงาน type_id (ตัวแรก)
    case_i = create_case(token, office_id, type_id_2, f"{tf_prefix}-I")   # ประเภทงาน type_id_2 (ตัวที่สอง)

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda msg: errors.append(f"[console] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))

        login_ui(page, "admin", "Admin@12345")
        page.goto(f"{BASE}/cases.html")
        page.fill("#searchInput", prefix)
        page.click("button:has-text('ค้นหา')")
        page.wait_for_timeout(800)

        expectations = [
            (f"{prefix}-A", "-", None),
            (f"{prefix}-B", "เกิน 30 วัน", "status-ASSIGNED"),
            (f"{prefix}-C", "เกิน 60 วัน", "status-CANCELLED"),
            (f"{prefix}-D", "-", None),
            (f"{prefix}-E", "-", None),
        ]
        for code, expected_text, expected_class in expectations:
            row = page.locator(f"tr:has-text('{code}')")
            row_text = row.inner_text()
            print(f"{code}: {row_text.replace(chr(10), ' | ')}")
            assert expected_text in row_text, f"{code}: คาดว่าเจอ '{expected_text}' แต่ไม่เจอ"
            if expected_class:
                # คอลัมน์: รว.19(1) ผู้ขอ(2) ประเภทงาน(3) ช่างรับผิดชอบ(4) วันรับเรื่อง(5) วันนัดรังวัด(6) เกินนัดรังวัด(7) เช็คลิสต์(8) สถานะ(9)
                overdue_cell = row.locator("td:nth-child(7)")
                badge_class = overdue_cell.locator(".badge").get_attribute("class")
                assert expected_class in badge_class, f"{code}: คาดว่า badge class มี '{expected_class}' แต่ได้ {badge_class}"

        # ตัวกรองสถานะต้องมีเฉพาะสถานะที่เรื่องในระบบนี้เป็นได้จริง (9 สถานะ) ไม่รวมสถานะเก่าจาก Blueprint เดิม
        # ที่ตัดออกจาก workflow แล้ว (เช่น กำลังปฏิบัติงาน, รอตรวจ QC, เสร็จสิ้น ฯลฯ) เพราะเลือกแล้วจะไม่พบเรื่องใดๆ เลย
        options = page.locator("#statusFilter option").all_inner_texts()
        print("Filter options:", options)
        expected_options = [
            "ทุกสถานะ", "รับเรื่องแล้ว", "รอมอบหมาย", "มอบหมายแล้ว", "รอการรังวัด",
            "งดรังวัด", "นัดตรวจสอบใหม่", "เลื่อนรังวัด", "ถอนจ่ายแล้ว", "ยกเลิก",
        ]
        assert options == expected_options, f"ตัวกรองสถานะไม่ตรงกับที่คาดไว้: {options}"
        stale_statuses = ["กำลังปฏิบัติงาน", "รอเอกสาร", "รอปิดประกาศ", "รอตรวจ QC", "รออนุมัติถอนจ่าย", "เสร็จสิ้น", "พักงาน", "ต้องแก้ไข/รังวัดซ้ำ"]
        for s in stale_statuses:
            assert s not in options, f"ตัวกรองสถานะไม่ควรมีสถานะเก่าที่ไม่ได้ใช้แล้ว: {s}"
        print("OK: ตัวกรองสถานะแสดงเฉพาะสถานะที่เรื่องในระบบเป็นได้จริง")

        # ตัวกรองช่างรังวัด: ต้องมีตัวเลือก "ทั้งหมด" / "ยังไม่มอบหมาย" และรายชื่อช่างจริงจาก /surveyors
        surveyor_option_text = surveyor["full_name"] + (f" ({surveyor['nickname']})" if surveyor.get("nickname") else "")
        surveyor_options = page.locator("#surveyorFilter option").all_inner_texts()
        print("Surveyor filter options:", surveyor_options)
        assert "ช่างรังวัดทั้งหมด" in surveyor_options
        assert "ยังไม่มอบหมาย" in surveyor_options
        assert surveyor_option_text in surveyor_options, f"ไม่พบตัวเลือกช่าง '{surveyor_option_text}' ในตัวกรอง"

        # เลือกช่างคนนี้ -> ต้องเห็นเฉพาะเรื่องที่ช่างคนนี้รับผิดชอบ (F) ไม่เห็นเรื่องที่ยังไม่มอบหมาย (G)
        page.fill("#searchInput", sv_prefix)
        page.select_option("#surveyorFilter", surveyor["id"])
        page.click("button:has-text('ค้นหา')")
        page.wait_for_timeout(800)
        rows_text = page.locator("#casesBody").inner_text()
        assert f"{sv_prefix}-F" in rows_text, f"คาดว่าเจอ {sv_prefix}-F เมื่อกรองตามช่างที่รับผิดชอบ"
        assert f"{sv_prefix}-G" not in rows_text, f"ไม่ควรเจอ {sv_prefix}-G เมื่อกรองตามช่างที่รับผิดชอบ"
        print("OK: กรองตามช่างรังวัดเจาะจงถูกต้อง")

        # เลือก "ยังไม่มอบหมาย" -> ต้องเห็นเฉพาะเรื่องที่ไม่มีช่างรับผิดชอบ (G) ไม่เห็นเรื่องที่มอบหมายแล้ว (F)
        page.select_option("#surveyorFilter", "unassigned")
        page.click("button:has-text('ค้นหา')")
        page.wait_for_timeout(800)
        rows_text = page.locator("#casesBody").inner_text()
        assert f"{sv_prefix}-G" in rows_text, f"คาดว่าเจอ {sv_prefix}-G เมื่อกรอง 'ยังไม่มอบหมาย'"
        assert f"{sv_prefix}-F" not in rows_text, f"ไม่ควรเจอ {sv_prefix}-F เมื่อกรอง 'ยังไม่มอบหมาย'"
        print("OK: กรอง 'ยังไม่มอบหมาย' ถูกต้อง")

        # ล้างตัวกรองช่างรังวัดก่อนทดสอบตัวกรองประเภทงาน (มิฉะนั้นจะกรองซ้อนกันจนไม่พบเรื่องที่สร้างใหม่)
        page.select_option("#surveyorFilter", "")

        # ตัวกรองประเภทงาน: ต้องมีตัวเลือกประเภทงานจริงจาก /survey-types
        type_options = page.locator("#typeFilter option").all_inner_texts()
        assert "ทุกประเภทงาน" in type_options
        assert type_name_2 in type_options, f"ไม่พบตัวเลือกประเภทงาน '{type_name_2}' ในตัวกรอง"

        # เลือกประเภทงาน type_id_2 -> ต้องเห็นเฉพาะเรื่อง I (ประเภท type_id_2) ไม่เห็นเรื่อง H (ประเภท type_id)
        page.fill("#searchInput", tf_prefix)
        page.select_option("#typeFilter", type_id_2)
        page.click("button:has-text('ค้นหา')")
        page.wait_for_timeout(800)
        rows_text = page.locator("#casesBody").inner_text()
        assert f"{tf_prefix}-I" in rows_text, f"คาดว่าเจอ {tf_prefix}-I เมื่อกรองตามประเภทงาน"
        assert f"{tf_prefix}-H" not in rows_text, f"ไม่ควรเจอ {tf_prefix}-H เมื่อกรองตามประเภทงานอื่น"
        print("OK: กรองตามประเภทงานถูกต้อง")

        # ปุ่ม "ล้างตัวกรอง": ตอนนี้มีทั้งคำค้นหาและตัวกรองประเภทงานตั้งค่าอยู่ กดแล้วต้องรีเซ็ตทุกช่องกลับเป็นค่าเริ่มต้น
        page.select_option("#statusFilter", "CANCELLED")  # ตั้งตัวกรองสถานะไว้ด้วยเพื่อให้ครบทุกช่อง
        page.click("button:has-text('ล้างตัวกรอง')")
        page.wait_for_timeout(800)
        assert page.input_value("#searchInput") == "", "คำค้นหาควรว่างหลังกดล้างตัวกรอง"
        assert page.input_value("#statusFilter") == "", "ตัวกรองสถานะควรกลับเป็น 'ทุกสถานะ' หลังกดล้างตัวกรอง"
        assert page.input_value("#typeFilter") == "", "ตัวกรองประเภทงานควรกลับเป็น 'ทุกประเภทงาน' หลังกดล้างตัวกรอง"
        assert page.input_value("#surveyorFilter") == "", "ตัวกรองช่างรังวัดควรกลับเป็น 'ช่างรังวัดทั้งหมด' หลังกดล้างตัวกรอง"
        # ค้นหา tf_prefix ใหม่อีกครั้ง (ไม่ตั้งตัวกรองประเภทงาน) -> ต้องเห็นทั้ง H และ I เพราะตัวกรองประเภทงานถูกล้างแล้ว
        page.fill("#searchInput", tf_prefix)
        page.click("button:has-text('ค้นหา')")
        page.wait_for_timeout(800)
        rows_text = page.locator("#casesBody").inner_text()
        assert f"{tf_prefix}-H" in rows_text and f"{tf_prefix}-I" in rows_text, "หลังล้างตัวกรอง ควรเห็นทั้งสองประเภทงานอีกครั้ง"
        print("OK: ปุ่ม 'ล้างตัวกรอง' รีเซ็ตทุกตัวกรองถูกต้อง")

        browser.close()

    print("\n--- Console/page errors ---")
    if errors:
        for e in errors:
            print("ERR:", e)
    else:
        print("(none)")
    print("\nALL OVERDUE-APPOINTMENT COLUMN CHECKS PASSED")


if __name__ == "__main__":
    main()
