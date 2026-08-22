"""ทดสอบฟีเจอร์ใหม่: นำเข้างานค้างจากไฟล์ CSV (cases-import.html) ด้วย Playwright"""
import os

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"

CSV_CONTENT = (
    "รว.19,ชื่อผู้ขอรังวัด,ประเภท,วันที่รับเรื่อง,วันที่นัดรังวัด,เบอร์ติดต่อ/หมายเหตุ,สถานะเริ่มต้น\r\n"
    "1494/2566,นางสาวสุรีรัตน์ ปานทอง,ออกโฉนดที่ดิน,,2025-12-12,,\r\n"
    "426/2566,นายอนุชาติ ยวงอักษร,ออกโฉนดที่ดิน,,12 ธ.ค. 2568,,\r\n"
    "999/2566,ทดสอบ ประเภทผิด,ประเภทไม่มีจริง,,2025-12-01,,\r\n"
    "998/2566,,ออกโฉนดที่ดิน,,2025-12-01,,\r\n"
    "400/2569,นางสาวประสมทรัพย์ วงษ์ยันต์,สอบเขตโฉนดที่ดิน,2026-03-01,2026-03-12,,ถอนจ่ายแล้ว\r\n"
)


def screenshot(page, name):
    os.makedirs("../screenshots", exist_ok=True)
    page.screenshot(path=f"../screenshots/{name}.png", full_page=True)


def main():
    csv_path = "test_import_tmp.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(CSV_CONTENT)

    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.on("console", lambda msg: errors.append(f"[console] {msg.text}") if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))

            page.goto(f"{BASE}/login.html")
            page.fill("#username", "admin")
            page.fill("#password", "Admin@12345")
            page.click("button[type=submit]")
            page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)

            nav_text = page.inner_text(".topbar nav")
            assert "นำเข้างาน" in nav_text, "เมนู admin ควรมีลิงก์ 'นำเข้างาน'"
            print("OK: เห็นเมนู 'นำเข้างาน'")

            page.click('a[href="/cases-import.html"]')
            page.wait_for_url(f"{BASE}/cases-import.html")
            page.wait_for_function("document.querySelectorAll('#ci_office_id option').length > 0")
            page.wait_for_timeout(300)

            # เลือกช่างรังวัดคนแรกในดรอปดาวน์ (ไม่นับ "ไม่ระบุ") -> สำนักงานต้องถูกล็อกอัตโนมัติตามช่างคนนั้น
            page.select_option("#ci_surveyor_id", index=1)
            page.wait_for_timeout(200)
            assert page.locator("#ci_office_id").is_disabled(), "เลือกช่างรังวัดแล้วสำนักงานควรถูกล็อกอัตโนมัติ"
            print("OK: เลือกช่างรังวัดแล้วสำนักงานถูกล็อกอัตโนมัติ")
            surveyor_label = page.locator("#ci_surveyor_id option:checked").inner_text()

            page.set_input_files("#ci_file", csv_path)
            page.click("#importBtn")
            page.wait_for_selector("#resultCard", state="visible", timeout=8000)
            page.wait_for_timeout(300)

            summary_text = page.inner_text("#resultSummary")
            assert "นำเข้าสำเร็จ 3 รายการ" in summary_text, f"ควรนำเข้าสำเร็จ 3 รายการ (2 แถวผิดถูกข้าม) แต่ได้: {summary_text}"
            assert "ข้าม 2 รายการ" in summary_text
            print("OK: นำเข้าสำเร็จ 3 รายการ ข้าม 2 รายการ ตรงตามที่ออกแบบไว้ (แถวประเภทงานผิด + แถวไม่มีชื่อผู้ขอ)")

            skipped_text = page.inner_text("#skippedBody")
            assert "ไม่พบประเภทงาน" in skipped_text
            assert "ไม่มีชื่อผู้ขอรังวัด" in skipped_text
            print("OK: ข้อความเหตุผลของแถวที่ข้ามถูกต้อง อ่านแล้วรู้ว่าต้องไปแก้อะไร")
            screenshot(page, "40_import_result")

            # เคสที่นำเข้าไปจริงต้องโผล่ในหน้ารายการงาน และผูกกับช่างที่เลือกไว้ตอนนำเข้า
            page.goto(f"{BASE}/cases.html")
            page.wait_for_selector("table")
            page.wait_for_timeout(500)
            body_text = page.inner_text(".container")
            assert "1494/2566" in body_text and "426/2566" in body_text
            print("OK: เห็นเคสที่นำเข้าไปในหน้ารายการงาน")

            # เคสที่ระบุ "สถานะเริ่มต้น" เป็น "ถอนจ่ายแล้ว" ในไฟล์ ต้องถูกตั้งสถานะ CLOSED ให้ทันที (ไม่ใช่สถานะเริ่มต้นปกติ)
            token = page.evaluate("localStorage.getItem('dol_access_token')")
            resp = page.request.get(
                f"{BASE}/api/v1/survey-cases?search=400%2F2569",
                headers={"Authorization": f"Bearer {token}"},
            ).json()
            assert resp and resp[0]["status"] == "CLOSED", "แถวที่ระบุสถานะเริ่มต้น='ถอนจ่ายแล้ว' ต้องถูกตั้งเป็น CLOSED"
            assert resp[0]["assigned_surveyor"] is not None, "เคสที่นำเข้าต้องผูกกับช่างรังวัดที่เลือกไว้ตอนนำเข้า"
            print(f"OK: เคส 400/2569 ถูกตั้งสถานะ CLOSED และมอบหมายให้ '{resp[0]['assigned_surveyor']['full_name']}' ถูกต้อง")

            browser.close()
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)

    print("\n--- Console/page errors ---")
    if errors:
        for e in errors:
            print("ERR:", e)
    else:
        print("(none)")

    print("\nALL CASES-IMPORT CHECKS PASSED")


if __name__ == "__main__":
    main()
