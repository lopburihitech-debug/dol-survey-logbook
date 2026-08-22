"""ทดสอบหน้าใหม่: จัดการผู้ใช้งาน (users.html)"""
import random
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"


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

        # --- admin เห็นเมนู "ผู้ใช้งาน" และเข้าหน้าได้ ---
        login(page, "admin", "Admin@12345")
        nav_text = page.inner_text(".topbar nav")
        print("Admin nav text:", nav_text.replace("\n", " | "))
        assert "ผู้ใช้งาน" in nav_text
        page.click('a[href="/users.html"]')
        page.wait_for_url(f"{BASE}/users.html")
        page.wait_for_selector("#usersBody tr")
        rows_before = page.locator("#usersBody tr").count()
        print("Users rows before create:", rows_before)

        # --- สร้างผู้ใช้งานใหม่ ---
        suffix = random.randint(1000, 9999)
        username = f"testuser{suffix}"
        page.click("#newUserBtn")
        page.wait_for_selector("#userModal", state="visible")
        page.fill("#u_username", username)
        page.fill("#u_password", "InitPass@2026")
        page.fill("#u_full_name", "ผู้ใช้ทดสอบ ระบบ")
        page.select_option("#u_role", "supervisor")
        hint_visible = page.eval_on_selector("#u_surveyor_hint", "el => getComputedStyle(el).display") == "none"
        print("Hint ไม่โชว์ตอนเลือก role=supervisor:", hint_visible)
        page.select_option("#u_role", "surveyor")
        hint_visible2 = page.eval_on_selector("#u_surveyor_hint", "el => getComputedStyle(el).display") == "block"
        print("Hint โชว์ตอนเลือก role=surveyor:", hint_visible2)
        page.select_option("#u_role", "supervisor")
        page.click("#userForm button[type=submit]")
        page.wait_for_timeout(700)
        body_text = page.inner_text("#usersBody")
        assert username in body_text, "ไม่พบผู้ใช้ที่เพิ่งสร้าง"
        print("OK: พบผู้ใช้งานที่สร้างใหม่ในตาราง")

        # --- ล็อกอินด้วยบัญชีที่เพิ่งสร้าง เพื่อยืนยันว่าใช้งานได้จริง ---
        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")
        login(page, username, "InitPass@2026")
        print("OK: ล็อกอินด้วยบัญชีที่เพิ่งสร้างสำเร็จ")
        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")

        # --- กลับมาแก้ไข: ปิดใช้งาน + ตั้งรหัสผ่านใหม่ ---
        login(page, "admin", "Admin@12345")
        page.goto(f"{BASE}/users.html")
        page.wait_for_selector("#usersBody tr")
        page.click(f"tr:has-text('{username}') button:has-text('แก้ไข')")
        page.wait_for_selector("#userModal", state="visible")
        page.uncheck("#u_is_active")
        page.click("#userForm button[type=submit]")
        page.wait_for_timeout(700)
        row_text = page.inner_text(f"tr:has-text('{username}')")
        print("สถานะหลังปิดใช้งาน:", "ปิดใช้งาน" in row_text)
        assert "ปิดใช้งาน" in row_text

        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")
        page.goto(f"{BASE}/login.html")
        page.fill("#username", username)
        page.fill("#password", "InitPass@2026")
        page.click("button[type=submit]")
        page.wait_for_timeout(700)
        blocked_text = page.inner_text("body")
        print("บัญชีที่ปิดใช้งานล็อกอินไม่ได้:", "ปิดใช้งาน" in blocked_text or page.url.endswith("/login.html"))
        assert page.url.endswith("/login.html")

        # --- เปิดใช้งานอีกครั้ง + ตั้งรหัสผ่านใหม่ ---
        login(page, "admin", "Admin@12345")
        page.goto(f"{BASE}/users.html")
        page.wait_for_selector("#usersBody tr")
        page.click(f"tr:has-text('{username}') button:has-text('แก้ไข')")
        page.wait_for_selector("#userModal", state="visible")
        page.check("#u_is_active")
        page.click("#userForm button[type=submit]")
        page.wait_for_timeout(700)

        page.click(f"tr:has-text('{username}') button:has-text('ตั้งรหัสผ่านใหม่')")
        page.wait_for_selector("#pwModal", state="visible")
        page.fill("#pw_new", "short")
        page.fill("#pw_confirm", "short")
        page.click("#pwForm button[type=submit]")
        page.wait_for_timeout(300)
        pw_error = page.inner_text("#pwFormError")
        print("Error สั้นเกินไป:", pw_error)
        assert "8 ตัวอักษร" in pw_error

        page.fill("#pw_new", "SecondPass@2026")
        page.fill("#pw_confirm", "DifferentPass@2026")
        page.click("#pwForm button[type=submit]")
        page.wait_for_timeout(300)
        pw_error2 = page.inner_text("#pwFormError")
        print("Error ไม่ตรงกัน:", pw_error2)
        assert "ไม่ตรงกัน" in pw_error2

        page.fill("#pw_new", "SecondPass@2026")
        page.fill("#pw_confirm", "SecondPass@2026")
        page.once("dialog", lambda d: d.accept())
        page.click("#pwForm button[type=submit]")
        page.wait_for_timeout(500)
        pw_modal_display = page.eval_on_selector("#pwModal", "el => getComputedStyle(el).display")
        print("Modal ปิดหลังตั้งรหัสผ่านสำเร็จ:", pw_modal_display == "none")

        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")
        login(page, username, "SecondPass@2026")
        print("OK: ล็อกอินด้วยรหัสผ่านใหม่สำเร็จหลังตั้งรหัสผ่านใหม่")
        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")

        # --- admin ต้องไม่เห็นตัวเลือกปิดใช้งาน/เปลี่ยนบทบาทของตัวเอง ---
        login(page, "admin", "Admin@12345")
        page.goto(f"{BASE}/users.html")
        page.wait_for_selector("#usersBody tr")
        page.click("tr:has-text('admin') button:has-text('แก้ไข')")
        page.wait_for_selector("#userModal", state="visible")
        is_active_disabled = page.eval_on_selector("#u_is_active", "el => el.disabled")
        role_disabled = page.eval_on_selector("#u_role", "el => el.disabled")
        self_hint_visible = page.eval_on_selector("#u_self_hint", "el => getComputedStyle(el).display") == "block"
        print("ช่องปิดใช้งานตัวเอง disabled:", is_active_disabled, "| ช่องบทบาทตัวเอง disabled:", role_disabled, "| hint แสดง:", self_hint_visible)
        assert is_active_disabled and role_disabled and self_hint_visible

        # --- supervisor (ไม่ใช่ system_admin) เข้าหน้า users.html ตรงๆ ต้องถูกกัน ---
        page.click("button:has-text('ยกเลิก')")
        page.click("button:has-text('ออกจากระบบ')")
        page.wait_for_url(f"{BASE}/login.html")
        login(page, "supervisor1", "Supervisor@12345")
        nav_text_sup = page.inner_text(".topbar nav")
        print("Supervisor ไม่เห็นเมนู 'ผู้ใช้งาน':", "ผู้ใช้งาน" not in nav_text_sup)
        assert "ผู้ใช้งาน" not in nav_text_sup
        page.goto(f"{BASE}/users.html")
        page.wait_for_timeout(500)
        body_text_blocked = page.inner_text(".container")
        print("Supervisor เข้าตรงๆ ถูกกันสิทธิ์:", "ใช้ได้เฉพาะ" in body_text_blocked)
        assert "ใช้ได้เฉพาะ" in body_text_blocked

        browser.close()

    print("\n--- Console/page errors ---")
    if errors:
        for e in errors:
            print("ERR:", e)
    else:
        print("(none)")
    print("\nALL USERS-PAGE CHECKS PASSED")


if __name__ == "__main__":
    main()
