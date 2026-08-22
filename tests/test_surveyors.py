"""ทดสอบหน้าใหม่: ข้อมูลช่างรังวัด (admin), งานของฉัน (surveyor) และโปรไฟล์ช่างรังวัด (surveyor-profile.html) ด้วย Playwright"""
import base64
import os
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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
    test_img_path = "/tmp/test_surveyor_photo.png"
    with open(test_img_path, "wb") as f:
        f.write(TINY_PNG)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda msg: errors.append(f"[admin-page] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"[admin-page] {exc}"))

        # --- Admin: nav link exists and goes to surveyors.html ---
        login(page, "admin", "Admin@12345")
        page.wait_for_selector("#kpiGrid .kpi-card")
        page.click('a[href="/surveyors.html"]')
        page.wait_for_url(f"{BASE}/surveyors.html")
        page.wait_for_selector("#surveyorsBody tr")
        screenshot(page, "10_surveyors_list")
        rows = page.locator("#surveyorsBody tr").count()
        print("Surveyor table rows:", rows)

        # เก็บ id ของช่างแต่ละคนไว้ใช้ทดสอบสิทธิ์การเข้าถึงโปรไฟล์ในภายหลัง (surveyor1 ดูโปรไฟล์ของ surveyor2 ไม่ได้)
        surveyor_rows = page.evaluate(
            "() => window._surveyorRows.map(s => ({id: s.id, employee_code: s.employee_code, total: s.total_case_count, active: s.active_case_count, completed: s.completed_case_count}))"
        )
        surveyor2_id = next((s["id"] for s in surveyor_rows if s["employee_code"] == "SV-002"), None)
        assert surveyor2_id, "ไม่พบช่างรังวัด SV-002 จากข้อมูลตั้งต้น"

        # คอลัมน์ "งานทั้งหมด/งานค้าง/งานเสร็จ" ต้องอยู่ในตาราง และค่าเริ่มต้นของ SV-001 (มีเรื่องตั้งต้น 1 เรื่อง
        # จากข้อมูลตั้งต้น ยังไม่ปิด) ต้องเป็น ทั้งหมด=1, ค้าง=1, เสร็จ=0 ตามข้อมูลตั้งต้นของระบบ
        header_text = page.locator("table thead").first.inner_text()
        assert "งานทั้งหมด" in header_text and "งานค้าง" in header_text and "งานเสร็จ" in header_text
        sv1 = next((s for s in surveyor_rows if s["employee_code"] == "SV-001"), None)
        assert sv1 and sv1["total"] == 1 and sv1["active"] == 1 and sv1["completed"] == 0, f"ตัวเลขงานของ SV-001 ไม่ตรงกับที่คาดไว้: {sv1}"
        print("OK: คอลัมน์งานทั้งหมด/งานค้าง/งานเสร็จ แสดงตัวเลขถูกต้อง")

        # คลิกชื่อช่างคนแรก -> ต้องไปหน้าโปรไฟล์ช่างรังวัด แสดงแดชบอร์ดสรุปผลงาน
        first_name_text = page.locator("#surveyorsBody td.clickable").nth(1).inner_text()
        page.click("#surveyorsBody td.clickable >> nth=1")
        page.wait_for_url(f"{BASE}/surveyor-profile.html?id=*", timeout=5000)
        page.wait_for_selector(".kpi-grid .kpi-card")
        screenshot(page, "11_surveyor_profile")
        profile_text = page.inner_text(".container")
        assert first_name_text.split(" (")[0] in profile_text, "หน้าโปรไฟล์ควรแสดงชื่อช่างที่คลิกเข้ามา"
        assert "งานทั้งหมด" in profile_text and "เกินนัดรังวัด 30 วัน" in profile_text and "เช็คลิสต์ความคืบหน้ารวม" in profile_text
        print("OK: คลิกชื่อช่างจากหน้ารายชื่อไปหน้าโปรไฟล์ได้ถูกต้อง พร้อมแดชบอร์ดสรุปผลงาน")

        # ปุ่มย้อนกลับต้องพากลับไปหน้าข้อมูลช่างรังวัด (บทบาทที่ไม่ใช่ช่างรังวัด)
        page.click("#backLink")
        page.wait_for_url(f"{BASE}/surveyors.html")
        print("OK: ปุ่มย้อนกลับจากหน้าโปรไฟล์ (admin) กลับไปหน้าข้อมูลช่างรังวัดถูกต้อง")

        # create a new surveyor via modal
        page.click("#newSurveyorBtn")
        page.wait_for_selector("#surveyorModal", state="visible")
        page.fill("#s_username", "surveyor_pw_test")
        page.fill("#s_password", "Surveyor@12345")
        page.fill("#s_full_name", "นางสาวทดสอบ เพลย์ไรท์")
        # หมายเหตุ: ไม่กรอก #s_employee_code แล้ว — ช่องนี้ถูกซ่อนตอนเพิ่มช่างรังวัดใหม่ เพราะระบบออกรหัสให้อัตโนมัติ (SV-XXX)
        page.fill("#s_nickname", "เทส")
        page.set_input_files("#s_photo", test_img_path)
        page.click("#surveyorForm button[type=submit]")
        page.wait_for_timeout(800)
        screenshot(page, "12_surveyors_after_create")
        new_count = page.locator("#surveyorsBody tr").count()
        print("Rows after create:", new_count)
        body_text = page.inner_text("#surveyorsBody")
        assert "นางสาวทดสอบ เพลย์ไรท์" in body_text, "ไม่พบช่างรังวัดที่เพิ่งสร้างในตาราง"
        print("OK: พบช่างรังวัดที่สร้างใหม่ในตาราง")

        # ตรวจว่ารูปถ่ายที่อัปโหลดตอนสร้างแสดงเป็น avatar ในตารางแล้ว
        page.wait_for_timeout(500)
        row = page.locator("tr:has-text('นางสาวทดสอบ เพลย์ไรท์')")
        avatar_img_count = row.locator(".avatar-circle img").count()
        print("OK: avatar รูปถ่ายแสดงในตารางหลังสร้าง:", avatar_img_count >= 1)
        assert avatar_img_count >= 1, "ไม่พบรูปถ่ายช่างรังวัดที่เพิ่งอัปโหลดในตาราง"

        # edit the newly created surveyor — ต้องเห็น preview รูปเดิมในฟอร์ม (ปุ่มแก้ไขเป็นไอคอนดินสอ ไม่มีข้อความแล้ว)
        page.click("tr:has-text('นางสาวทดสอบ เพลย์ไรท์') button[title='แก้ไข']")
        page.wait_for_selector("#surveyorModal", state="visible")
        page.wait_for_selector("#s_photo_preview[style*='block']", timeout=5000)
        print("OK: ฟอร์มแก้ไขแสดง preview รูปถ่ายเดิม")
        page.fill("#s_position", "นายช่างรังวัดทดสอบ")
        page.click("#surveyorForm button[type=submit]")
        page.wait_for_timeout(800)
        body_text2 = page.inner_text("#surveyorsBody")
        assert "นางสาวทดสอบ เพลย์ไรท์" in body_text2
        print("OK: แก้ไขข้อมูลช่างรังวัดสำเร็จ")
        screenshot(page, "13_surveyors_after_edit")

        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")

        # --- Surveyor: nav link is "งานของฉัน", card view works ---
        page.on("console", lambda msg: errors.append(f"[surveyor-page] {msg.text}") if msg.type == "error" else None)
        login(page, "surveyor1", "Surveyor@12345")
        nav_text = page.inner_text(".topbar nav")
        print("Surveyor nav text:", nav_text.replace("\n", " | "))
        assert "งานของฉัน" in nav_text
        assert "งานรังวัด" not in nav_text
        print("OK: เมนูของช่างรังวัดแสดง 'งานของฉัน' แทน 'งานรังวัด'")

        page.click('a[href="/my-work.html"]')
        page.wait_for_url(f"{BASE}/my-work.html")
        page.wait_for_selector("#workCards .card")
        screenshot(page, "14_my_work_surveyor")
        card_count = page.locator("#workCards .card").count()
        print("My-work cards:", card_count)
        assert card_count >= 1

        # click a card -> should go to case detail
        page.click("#workCards .card >> nth=0")
        page.wait_for_url("**/case.html?id=*", timeout=5000)
        print("OK: คลิกการ์ดในหน้างานของฉันไปหน้ารายละเอียดงานได้ถูกต้อง")

        # --- ช่างรังวัดดูโปรไฟล์ของตัวเองได้ ผ่านปุ่ม "ดูสรุปผลงานของฉัน" ในหน้างานของฉัน ---
        page.goto(f"{BASE}/my-work.html")
        page.wait_for_selector("#myProfileBtn", state="visible", timeout=5000)
        page.click("#myProfileBtn")
        page.wait_for_url(f"{BASE}/surveyor-profile.html?id=*", timeout=5000)
        page.wait_for_selector(".kpi-grid .kpi-card")
        own_profile_text = page.inner_text(".container")
        assert "งานทั้งหมด" in own_profile_text
        print("OK: ช่างรังวัดกดปุ่ม 'ดูสรุปผลงานของฉัน' แล้วเห็นโปรไฟล์ตัวเองพร้อมแดชบอร์ด")

        # ปุ่มย้อนกลับของช่างรังวัดต้องพากลับไปหน้า "งานของฉัน" (ต่างจาก admin/supervisor ที่กลับไปหน้าข้อมูลช่างรังวัด)
        page.click("#backLink")
        page.wait_for_url(f"{BASE}/my-work.html")
        print("OK: ปุ่มย้อนกลับจากหน้าโปรไฟล์ (ช่างรังวัด) กลับไปหน้างานของฉันถูกต้อง")

        # --- ช่างรังวัด (surveyor1) พยายามดูโปรไฟล์ของช่างคนอื่น (surveyor2) ต้องถูกกันสิทธิ์ ---
        page.goto(f"{BASE}/surveyor-profile.html?id={surveyor2_id}")
        page.wait_for_timeout(600)
        blocked_text = page.inner_text(".container")
        assert "ไม่มีสิทธิ์" in blocked_text, "ช่างรังวัดไม่ควรดูโปรไฟล์ของช่างคนอื่นได้"
        print("OK: ช่างรังวัดพยายามดูโปรไฟล์ของช่างคนอื่นถูกกันสิทธิ์ถูกต้อง")

        # --- direct access to surveyors.html as surveyor role should be blocked (frontend-level) ---
        page.goto(f"{BASE}/surveyors.html")
        page.wait_for_timeout(500)
        body_text3 = page.inner_text(".container")
        assert "ใช้ได้เฉพาะ" in body_text3
        print("OK: ช่างรังวัดเข้าหน้า surveyors.html ตรงๆ ถูกกันสิทธิ์")
        screenshot(page, "15_surveyors_blocked_for_surveyor")

        browser.close()

    print("\n--- Console/page errors ---")
    if errors:
        for e in errors:
            print("ERR:", e)
    else:
        print("(none)")


if __name__ == "__main__":
    main()
