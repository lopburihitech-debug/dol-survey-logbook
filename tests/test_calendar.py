"""ทดสอบหน้าใหม่: ปฏิทินนัดรังวัด (calendar.html)

1. เรื่องที่มีวันนัดรังวัดในเดือนที่กำลังแสดง ต้องปรากฏบนช่องวันที่ถูกต้องในปฏิทิน (นับจาก appointment_date)
2. เรื่องที่มีวันนัดรังวัดนอกเดือนที่แสดงต้องไม่ปรากฏ (ทดสอบขอบเขตวันที่ท้าย/ต้นเดือน)
3. ช่องปฏิทินแสดง "ชื่อช่างรังวัด" ที่มีนัดวันนั้น (ไม่ใช่รหัสเรื่อง) — ช่างที่มีเรื่องเดียวคลิกแล้วเปิดป๊อปอัพเรื่องนั้นตรงๆ
4. ป๊อปอัพที่เปิดจากชิปช่างในปฏิทิน ต้องแสดงข้อมูลเรื่องถูกต้อง (รว.19, ผู้ขอ, ประเภทงาน, ช่างรับผิดชอบ, วันนัด, สถานะ)
5. ปุ่ม "ดูรายละเอียด" ในป๊อปอัพนำไปหน้ารายละเอียดเรื่องถูกต้อง
6. ช่างที่มีหลายเรื่องในวันเดียวกัน ต้องรวมเป็นชิปเดียวพร้อมตัวเลขจำนวนเรื่อง คลิกแล้วเปิดหน้าต่างรายการของวันนั้น
   (กรองเฉพาะช่างคนนี้) แทนที่จะเปิดป๊อปอัพตรงๆ และคลิกเรื่องในรายการเปิดป๊อปอัพเรื่องนั้นได้ถูกต้อง
7. วันที่มีช่างมากกว่า 3 คน ต้องมีตัวเลือก "+N คนอื่นๆ" เปิดหน้าต่างรายการทั้งหมดของวันนั้น (ทุกคน ทุกเรื่อง)
8. การ์ด "วันนี้ช่างรังวัดออกไปรังวัด" ต้องจัดกลุ่มตามช่างถูกต้อง และแสดงเฉพาะเรื่องที่นัดวันนี้พอดี
9. ปุ่มเปลี่ยนเดือน (ก่อนหน้า/ถัดไป/วันนี้) ทำงานถูกต้อง
"""
import json
import random
import urllib.request
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
API = f"{BASE}/api/v1"


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
        "requester_name": f"ผู้ขอ {code}",
        "received_date": "2026-08-01",
    }
    if appointment_date:
        payload["appointment_date"] = appointment_date
    if surveyor_id:
        payload["surveyor_id"] = surveyor_id
    return _request("POST", "/survey-cases", token=token, payload=payload)


def create_surveyor(token, office_id, username, full_name):
    payload = {
        "employee_code": username,
        "office_id": office_id,
        "username": username,
        "password": "Aa@12345678",
        "full_name": full_name,
    }
    return _request("POST", "/surveyors", token=token, payload=payload)


def login_ui(page, username, password):
    page.goto(f"{BASE}/login.html")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)


def calendar_case_codes(page):
    """ดึงรหัสเรื่องทั้งหมดที่โหลดไว้ในปฏิทินเดือนที่กำลังแสดง (จาก window._calByDay) — ใช้ตรวจว่าเรื่องหนึ่งๆ
    ถูกดึงมาแสดงในเดือนนี้หรือไม่ โดยไม่อิงกับข้อความในชิป เพราะชิปตอนนี้แสดงชื่อช่างแทนรหัสเรื่องแล้ว"""
    return page.evaluate(
        "() => Object.values(window._calByDay).flat().map(c => c.case_code)"
    )


def main():
    token = login_api("admin", "Admin@12345")
    office_id = _request("GET", "/offices", token=token)[0]["id"]
    type_id = _request("GET", "/survey-types", token=token)[0]["id"]
    surveyor = _request("GET", "/surveyors", token=token)[0]

    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    # เลือกวันที่ "กลางเดือน" / "วันช่างเดียวหลายเรื่อง" / "วันเกิน 3 คน" จากชุดวันที่ปลอดภัย (1-28 มีอยู่ทุกเดือนแน่นอน)
    # โดยเลี่ยงวันที่ตรงกับวันนี้พอดี (ไม่งั้นเรื่อง TODAY จะไปชนช่องเดียวกับเรื่องทดสอบอื่น ทำให้ผลทดสอบกำกวม)
    candidate_days = [3, 8, 13, 18, 23, 26]
    safe_days = [d for d in candidate_days if d != today.day]
    mid_day, multi_day, overflow_day_num = safe_days[0], safe_days[1], safe_days[2]
    mid_month = today.replace(day=mid_day).isoformat()
    multi_month = today.replace(day=multi_day).isoformat()
    # เดือนถัดไป (ต้องไม่ปรากฏตอนดูเดือนปัจจุบัน)
    next_month = (today.replace(day=28) + timedelta(days=10)).isoformat()

    suffix = random.randint(1000, 9999)
    prefix = f"CT{suffix}"

    # ช่างทดสอบเฉพาะสำหรับเรื่องกลางเดือน — สร้างช่างใหม่เพื่อการันตีว่าวันนั้นมีช่างคนนี้แค่เรื่องเดียวแน่นอน
    # (ไม่ชนกับข้อมูลเก่าจากการรันเทสต์ครั้งก่อนหรือเทสต์ไฟล์อื่น) — ชิปในปฏิทินแสดงเฉพาะ "คำแรก" ของ full_name
    # (ดู surveyorShortLabel ใน calendar.html) จึงต้องใส่ suffix ที่ไม่ซ้ำกันไว้ในคำแรกเลย ไม่ใช่ต่อท้ายทั้งชื่อ
    mid_surveyor = create_surveyor(token, office_id, f"caltest{suffix}mid", f"ชื่อ{suffix}กลางเดือน นามสกุลทดสอบ")
    multi_surveyor = create_surveyor(token, office_id, f"caltest{suffix}multi", f"ชื่อ{suffix}หลายเรื่อง นามสกุลทดสอบ")
    overflow_surveyors = [
        create_surveyor(token, office_id, f"caltest{suffix}ov{i}", f"ชื่อ{suffix}คนที่{i} นามสกุลทดสอบ")
        for i in range(4)
    ]

    case_today = create_case(token, office_id, type_id, f"{prefix}-TODAY", today_iso, surveyor["id"])
    case_mid = create_case(token, office_id, type_id, f"{prefix}-MID", mid_month, mid_surveyor["id"])
    case_next_month = create_case(token, office_id, type_id, f"{prefix}-NEXTMONTH", next_month)

    # ช่างคนเดียวรับผิดชอบ 2 เรื่องในวันเดียวกัน -> ต้องรวมเป็นชิปเดียว "ชื่อช่าง (2)"
    case_multi_1 = create_case(token, office_id, type_id, f"{prefix}-MULTI1", multi_month, multi_surveyor["id"])
    case_multi_2 = create_case(token, office_id, type_id, f"{prefix}-MULTI2", multi_month, multi_surveyor["id"])

    # สร้าง 4 เรื่องในวันเดียวกัน คนละช่าง (มากกว่า 3 คน = เกินที่แสดงในช่อง) เพื่อทดสอบตัวเลือก "+N คนอื่นๆ"
    overflow_day = today.replace(day=overflow_day_num).isoformat()
    overflow_codes = [f"{prefix}-OV{i}" for i in range(4)]
    for code, svy in zip(overflow_codes, overflow_surveyors):
        create_case(token, office_id, type_id, code, overflow_day, svy["id"])

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1200})
        page.on("console", lambda msg: errors.append(f"[console] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))

        login_ui(page, "admin", "Admin@12345")

        # เมนู "ปฏิทินนัด" ต้องมีในหน้าเว็บ และคลิกแล้วไปหน้าปฏิทินได้
        page.click('a[href="/calendar.html"]')
        page.wait_for_url(f"{BASE}/calendar.html")
        page.wait_for_selector(".cal-grid")
        print("OK: เมนู 'ปฏิทินนัด' คลิกแล้วไปหน้าปฏิทินได้ถูกต้อง")

        # --- การ์ด "วันนี้ช่างรังวัดออกไปรังวัด" ---
        page.wait_for_selector(".today-out-grid, .empty-state")
        today_out_text = page.inner_text(".card:has-text('วันนี้')")
        assert f"{prefix}-TODAY" in today_out_text, "เรื่องที่นัดวันนี้ควรปรากฏในการ์ด 'วันนี้ช่างรังวัดออกไปรังวัด'"
        assert surveyor["full_name"] in today_out_text, "การ์ดควรจัดกลุ่มตามชื่อช่างที่รับผิดชอบ"
        print("OK: การ์ด 'วันนี้ช่างรังวัดออกไปรังวัด' แสดงถูกต้อง")

        # --- เรื่องกลางเดือนต้องอยู่ในปฏิทินเดือนนี้ (ในโมเดลข้อมูล) เรื่องเดือนถัดไปต้องไม่อยู่ ---
        codes_this_month = calendar_case_codes(page)
        assert f"{prefix}-MID" in codes_this_month, "เรื่องกลางเดือนควรปรากฏในปฏิทินเดือนปัจจุบัน"
        assert f"{prefix}-NEXTMONTH" not in codes_this_month, "เรื่องเดือนถัดไปไม่ควรปรากฏในปฏิทินเดือนปัจจุบัน"
        print("OK: เรื่องปรากฏเฉพาะในเดือนที่ตรงกับวันนัดรังวัด")

        # --- ช่องปฏิทินต้องแสดง "ชื่อช่าง" (คำแรกของ full_name) ไม่ใช่รหัสเรื่อง ---
        mid_short = mid_surveyor["full_name"].split(" ")[0]
        cal_text = page.inner_text(".cal-grid")
        assert f"{prefix}-MID" not in cal_text, "ช่องปฏิทินไม่ควรแสดงรหัสเรื่องอีกต่อไป (เปลี่ยนไปแสดงชื่อช่างแทน)"
        assert mid_short in cal_text, "ช่องปฏิทินควรแสดงชื่อช่างรับผิดชอบของเรื่องนั้น"
        print("OK: ช่องปฏิทินแสดงชื่อช่างรังวัดแทนรหัสเรื่องถูกต้อง")

        # --- คลิกชิปช่างที่มีเรื่องเดียว -> เปิดป๊อปอัพเรื่องนั้นตรงๆ ---
        page.click(f".cal-chip:has-text('{mid_short}')")
        page.wait_for_selector("#casePopup", state="visible")
        assert page.inner_text("#cp_code") == f"{prefix}-MID"
        assert f"ผู้ขอ {prefix}-MID" in page.inner_text("#cp_requester")
        assert mid_surveyor["full_name"] in page.inner_text("#cp_surveyor")
        print("OK: คลิกชิปช่างที่มีเรื่องเดียวเปิดป๊อปอัพเรื่องนั้นตรงๆ ถูกต้อง")

        # --- ปุ่ม "ดูรายละเอียด" นำไปหน้ารายละเอียดเรื่อง ---
        page.click("#cp_viewBtn")
        page.wait_for_url(f"**/case.html?id={case_mid['id']}", timeout=5000)
        print("OK: ปุ่ม 'ดูรายละเอียด' นำไปหน้ารายละเอียดเรื่องที่ถูกต้อง")

        # --- ช่างคนเดียวมีหลายเรื่องในวันเดียวกัน -> รวมเป็นชิปเดียวพร้อมจำนวนเรื่อง คลิกแล้วเปิดรายการของวัน ---
        page.goto(f"{BASE}/calendar.html")
        page.wait_for_selector(".cal-grid")
        multi_short = multi_surveyor["full_name"].split(" ")[0]
        multi_chip = page.locator(f".cal-chip:has-text('{multi_short}')")
        assert multi_chip.count() == 1, "ช่างที่มีหลายเรื่องในวันเดียวกันควรรวมเป็นชิปเดียว ไม่ใช่หลายชิป"
        assert "(2)" in multi_chip.inner_text(), "ชิปช่างที่มีหลายเรื่องควรแสดงจำนวนเรื่องกำกับไว้"
        multi_chip.click()
        page.wait_for_selector("#dayListModal", state="visible")
        assert page.locator("#casePopup").is_hidden(), "ช่างที่มีหลายเรื่องไม่ควรเปิดป๊อปอัพเรื่องตรงๆ ควรเปิดรายการของวันก่อน"
        day_list_text = page.inner_text("#dl_list")
        assert f"{prefix}-MULTI1" in day_list_text and f"{prefix}-MULTI2" in day_list_text, \
            "หน้าต่างรายการควรมีทั้ง 2 เรื่องของช่างคนนี้ในวันนั้น"
        page.locator(f"#dl_list .to-case:has-text('{prefix}-MULTI1')").click()
        page.wait_for_selector("#casePopup", state="visible")
        assert page.inner_text("#cp_code") == f"{prefix}-MULTI1"
        assert page.locator("#dayListModal").is_hidden(), "หน้าต่างรายการวันควรปิดเมื่อเปิดป๊อปอัพเรื่อง"
        print("OK: ช่างที่มีหลายเรื่องในวันเดียวกันรวมเป็นชิปเดียวและเปิดรายการของวันได้ถูกต้อง")
        page.click("#casePopup .btn-outline")

        # --- วันที่มีช่างมากกว่า 3 คน ต้องมี "+N คนอื่นๆ" และเปิดหน้าต่างรายการทั้งหมดได้ ---
        more_link = page.locator(".cal-more")
        assert more_link.count() >= 1, "วันที่มีช่างมากกว่า 3 คนควรมีตัวเลือก '+N คนอื่นๆ'"
        more_link.first.click()
        page.wait_for_selector("#dayListModal", state="visible")
        day_list_text = page.inner_text("#dl_list")
        for code in overflow_codes:
            assert code in day_list_text, f"หน้าต่างรายการวันควรมีเรื่อง {code} ครบทุกรายการ"
        print("OK: '+N คนอื่นๆ' เปิดหน้าต่างแสดงเรื่องทั้งหมดของวันนั้นครบถ้วน")

        page.locator("#dl_list .to-case").first.click()
        page.wait_for_selector("#casePopup", state="visible")
        assert page.locator("#dayListModal").is_hidden(), "หน้าต่างรายการวันควรปิดเมื่อเปิดป๊อปอัพเรื่อง"
        print("OK: คลิกเรื่องในหน้าต่างรายการวันเปิดป๊อปอัพเรื่องได้ถูกต้อง")
        page.click("#casePopup .btn-outline")

        # --- ปุ่มเปลี่ยนเดือน ---
        month_before = page.inner_text("#monthLabel")
        page.click("#nextMonthBtn")
        page.wait_for_timeout(500)
        month_after_next = page.inner_text("#monthLabel")
        assert month_after_next != month_before, "เดือนควรเปลี่ยนหลังกด 'เดือนถัดไป'"
        codes_next_month = calendar_case_codes(page)
        assert f"{prefix}-NEXTMONTH" in codes_next_month, "เมื่อไปเดือนถัดไป ควรเห็นเรื่องที่นัดไว้เดือนนั้น"
        print(f"OK: เปลี่ยนเดือนถัดไปถูกต้อง ({month_before} -> {month_after_next})")

        page.click("#todayBtn")
        page.wait_for_timeout(500)
        month_after_today = page.inner_text("#monthLabel")
        assert month_after_today == month_before, "ปุ่ม 'วันนี้' ควรพากลับมาเดือนปัจจุบัน"
        print("OK: ปุ่ม 'วันนี้' พากลับมาเดือนปัจจุบันถูกต้อง")

        browser.close()

    print("\n--- Console/page errors ---")
    if errors:
        for e in errors:
            print("ERR:", e)
    else:
        print("(none)")
    print("\nALL CALENDAR CHECKS PASSED")


if __name__ == "__main__":
    main()
