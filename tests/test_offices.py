"""ทดสอบหน้าใหม่: จัดการสำนักงาน (offices.html) ด้วย Playwright"""
import os
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"


def screenshot(page, name):
    os.makedirs("../screenshots", exist_ok=True)
    page.screenshot(path=f"../screenshots/{name}.png", full_page=True)


def login(page, username, password):
    page.goto(f"{BASE}/login.html")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda msg: errors.append(f"[console] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))

        # --- admin: nav link "สำนักงาน" exists and goes to offices.html ---
        login(page, "admin", "Admin@12345")
        page.wait_for_selector("#kpiGrid .kpi-card")
        nav_text = page.inner_text(".topbar nav")
        assert "สำนักงาน" in nav_text, "เมนู admin ควรมีลิงก์ 'สำนักงาน'"
        print("OK: เมนู admin มีลิงก์ 'สำนักงาน'")
        page.click('a[href="/offices.html"]')
        page.wait_for_url(f"{BASE}/offices.html")
        page.wait_for_selector("#officesBody tr")
        screenshot(page, "20_offices_list")
        body_text = page.inner_text("#officesBody")
        assert "LB01" in body_text and "ลพบุรี" in body_text, "ควรเห็นสำนักงานที่ดินลพบุรี (seed data) ในตาราง"
        print("OK: เห็นสำนักงานลพบุรีในตารางเดิม")

        # --- create a new office ---
        page.click("#newOfficeBtn")
        page.wait_for_selector("#officeModal", state="visible")
        page.fill("#o_code", "cm01")
        page.fill("#o_name", "สำนักงานที่ดินจังหวัดเชียงใหม่")
        page.fill("#o_province", "เชียงใหม่")
        page.fill("#o_district", "เมืองเชียงใหม่")
        page.click("#officeForm button[type=submit]")
        page.wait_for_timeout(600)
        screenshot(page, "21_offices_after_create")
        body_text2 = page.inner_text("#officesBody")
        assert "CM01" in body_text2 and "เชียงใหม่" in body_text2, "ควรเห็นสำนักงานใหม่ (รหัสถูกแปลงเป็นตัวพิมพ์ใหญ่)"
        print("OK: เพิ่มสำนักงานใหม่สำเร็จ รหัสถูกแปลงเป็นตัวพิมพ์ใหญ่ (CM01)")

        # --- new office should now be selectable when adding a surveyor (multi-office ready) ---
        page.goto(f"{BASE}/surveyors.html")
        page.wait_for_selector("#surveyorsBody tr")
        page.click("#newSurveyorBtn")
        page.wait_for_selector("#surveyorModal", state="visible")
        office_options = page.locator("#s_office_id option").all_inner_texts()
        assert any("เชียงใหม่" in o for o in office_options), "สำนักงานใหม่ควรเลือกได้ในฟอร์มเพิ่มช่างรังวัดทันที"
        print("OK: สำนักงานใหม่เลือกได้ในฟอร์มเพิ่มช่างรังวัดทันที (multi-office พร้อมใช้งานจริง)")
        page.click("button:has-text('ยกเลิก')")

        # --- edit: code field is locked, toggle is_active off then back on ---
        page.goto(f"{BASE}/offices.html")
        page.wait_for_selector("#officesBody tr")
        page.click("tr:has-text('เชียงใหม่') button[title='แก้ไข']")
        page.wait_for_selector("#officeModal", state="visible")
        code_disabled = page.locator("#o_code").is_disabled()
        assert code_disabled, "ตอนแก้ไข ห้ามแก้รหัสสำนักงานได้ (ต้องเป็น disabled)"
        print("OK: ตอนแก้ไข ช่องรหัสสำนักงานถูกล็อกไว้ถูกต้อง")
        page.uncheck("#o_is_active")
        page.click("#officeForm button[type=submit]")
        page.wait_for_timeout(600)
        screenshot(page, "22_offices_after_deactivate")
        body_text3 = page.inner_text("#officesBody")
        assert "ปิดใช้งาน" in body_text3
        print("OK: ปิดใช้งานสำนักงานสำเร็จ (ยังเห็นในหน้าจัดการสำนักงาน)")

        # deactivated office should disappear from the surveyor-add office dropdown (only active offices offered)
        page.goto(f"{BASE}/surveyors.html")
        page.wait_for_selector("#surveyorsBody tr")
        page.click("#newSurveyorBtn")
        page.wait_for_selector("#surveyorModal", state="visible")
        office_options2 = page.locator("#s_office_id option").all_inner_texts()
        assert not any("เชียงใหม่" in o for o in office_options2), "สำนักงานที่ปิดใช้งานแล้วไม่ควรเลือกได้ในฟอร์มอื่นอีก"
        print("OK: สำนักงานที่ปิดใช้งานแล้วหายไปจาก dropdown ที่อื่นถูกต้อง")

        # --- non-admin (supervisor) must not see the nav link nor access the page ---
        page.click("button:has-text('ยกเลิก')")
        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")
        login(page, "supervisor1", "Supervisor@12345")
        nav_text2 = page.inner_text(".topbar nav")
        assert "สำนักงาน" not in nav_text2, "หัวหน้าช่างไม่ควรเห็นเมนู 'สำนักงาน'"
        page.goto(f"{BASE}/offices.html")
        page.wait_for_timeout(500)
        body_text4 = page.inner_text(".container")
        assert "เฉพาะผู้ดูแลระบบ" in body_text4, "หัวหน้าช่างเข้าหน้า offices.html ตรงๆ ต้องถูกกันสิทธิ์"
        print("OK: หัวหน้าช่างไม่เห็นเมนูและเข้าหน้าตรงๆ ถูกกันสิทธิ์ถูกต้อง")

        browser.close()

    print("\n--- Console/page errors ---")
    if errors:
        for e in errors:
            print("ERR:", e)
    else:
        print("(none)")


if __name__ == "__main__":
    main()
