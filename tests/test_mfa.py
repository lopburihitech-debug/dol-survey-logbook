"""ทดสอบฟีเจอร์ใหม่: การยืนยันตัวตน 2 ชั้น (2FA/MFA)
ครอบคลุม: ตั้งค่า 2FA จาก account.html, ล็อกอิน 2 ขั้นตอน (รหัส TOTP + รหัสสำรอง), ปิดใช้งานเอง,
ผู้ดูแลระบบปิดใช้งานแทนกรณีเข้าระบบไม่ได้ (account recovery), และเปลี่ยนรหัสผ่านตัวเอง

หมายเหตุ: คำนวณรหัส TOTP ในไฟล์ทดสอบนี้ด้วย implementation ของ RFC 6238 ที่เขียนแยกต่างหาก (ไม่ import
จาก backend/services/totp.py) เพื่อจำลองสิ่งที่แอปยืนยันตัวตนจริง (เช่น Google Authenticator) จะคำนวณ
จาก secret เดียวกัน — เป็นการตรวจสอบข้ามว่า backend implement ตามมาตรฐานจริง ไม่ใช่แค่ทดสอบตัวเอง
"""
import base64
import hashlib
import hmac
import struct
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"


def totp_now(secret_b32: str, digits: int = 6, period: int = 30) -> str:
    padded = secret_b32.strip().upper()
    padded += "=" * ((8 - len(padded) % 8) % 8)
    key = base64.b32decode(padded)
    counter = int(time.time()) // period
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code_int).zfill(digits)


def login(page, username, password, expect_mfa=False):
    page.goto(f"{BASE}/login.html")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#loginForm button[type=submit]")
    if expect_mfa:
        page.wait_for_selector("#mfaForm", state="visible", timeout=5000)
    else:
        page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)


def logout(page):
    page.click("button:has-text('ออกจากระบบ')")
    page.wait_for_url(f"{BASE}/login.html")


def main():
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda msg: errors.append(f"[console] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))

        # --- 0. ทุกบทบาทเห็นเมนู "บัญชีของฉัน" ---
        login(page, "surveyor1", "Surveyor@12345")
        nav_text = page.inner_text(".topbar nav")
        print("Surveyor1 nav text:", nav_text.replace("\n", " | "))
        assert "บัญชีของฉัน" in nav_text
        page.click('a[href="/account.html"]')
        page.wait_for_url(f"{BASE}/account.html")

        # --- 1. เปลี่ยนรหัสผ่านตัวเอง ---
        page.fill("#cp_current", "wrong-password")
        page.fill("#cp_new", "NewSurveyor@2026")
        page.fill("#cp_confirm", "NewSurveyor@2026")
        page.click("#pwChangeForm button[type=submit]")
        page.wait_for_timeout(400)
        pw_err = page.inner_text("#pwChangeError")
        print("Error รหัสผ่านเดิมผิด:", pw_err)
        assert "รหัสผ่านเดิมไม่ถูกต้อง" in pw_err

        page.fill("#cp_current", "Surveyor@12345")
        page.fill("#cp_new", "short")
        page.fill("#cp_confirm", "short")
        page.click("#pwChangeForm button[type=submit]")
        page.wait_for_timeout(300)
        pw_err2 = page.inner_text("#pwChangeError")
        print("Error รหัสผ่านสั้นเกิน:", pw_err2)
        assert "8 ตัวอักษร" in pw_err2

        page.fill("#cp_current", "Surveyor@12345")
        page.fill("#cp_new", "NewSurveyor@2026")
        page.fill("#cp_confirm", "NewSurveyor@2026")
        page.click("#pwChangeForm button[type=submit]")
        page.wait_for_timeout(400)
        success_visible = page.eval_on_selector("#pwChangeSuccess", "el => getComputedStyle(el).display") == "block"
        print("เปลี่ยนรหัสผ่านสำเร็จ:", success_visible)
        assert success_visible

        logout(page)
        login(page, "surveyor1", "NewSurveyor@2026")
        print("OK: ล็อกอินด้วยรหัสผ่านใหม่สำเร็จ")
        page.goto(f"{BASE}/account.html")
        page.wait_for_selector("#mfaOffState", state="visible")

        # --- 2. ตั้งค่า 2FA ---
        page.click("#startMfaSetupBtn")
        page.wait_for_selector("#mfaSetupStep", state="visible")
        secret = page.inner_text("#mfaSecretText").strip()
        qr_src = page.get_attribute("#mfaQrImg", "src")
        print("Secret แสดงผล:", secret[:6] + "...", "| QR data URI length:", len(qr_src or ""))
        assert len(secret) >= 16
        assert qr_src.startswith("data:image/png;base64,")

        # กรอกรหัสผิดก่อน
        page.fill("#mfaEnableCode", "000000")
        page.click("#mfaEnableForm button[type=submit]")
        page.wait_for_timeout(400)
        enable_err = page.inner_text("#mfaEnableError")
        print("Error รหัสผิด:", enable_err)
        assert "ไม่ถูกต้อง" in enable_err

        code = totp_now(secret)
        page.fill("#mfaEnableCode", code)
        page.click("#mfaEnableForm button[type=submit]")
        page.wait_for_selector("#mfaBackupCodesStep", state="visible", timeout=5000)
        backup_chips = page.locator(".backup-code-chip").all_inner_texts()
        print("จำนวนรหัสสำรอง:", len(backup_chips), backup_chips)
        assert len(backup_chips) == 8
        backup_code = backup_chips[0].strip()

        page.click("#doneBackupCodesBtn")
        page.wait_for_selector("#mfaOnState", state="visible")
        print("OK: หน้าบัญชีแสดงสถานะเปิดใช้งาน 2FA แล้ว")

        # --- 3. ล็อกอินตอนนี้ต้องผ่าน 2 ขั้นตอน ---
        logout(page)
        login(page, "surveyor1", "NewSurveyor@2026", expect_mfa=True)
        print("OK: ล็อกอินขั้นที่ 1 ผ่านแล้ว ระบบขอรหัส 2FA ต่อ")

        # รหัสผิด
        page.fill("#mfaCode", "000000")
        page.click("#mfaForm button[type=submit]")
        page.wait_for_timeout(400)
        mfa_err = page.inner_text("#mfaErrorBox")
        print("Error รหัส 2FA ผิด:", mfa_err)
        assert "ไม่ถูกต้อง" in mfa_err

        # ปุ่มย้อนกลับ
        page.click("#mfaBackBtn")
        page.wait_for_selector("#loginForm", state="visible")
        loginform_display = page.eval_on_selector("#loginForm", "el => getComputedStyle(el).display")
        print("กลับไปหน้ากรอกรหัสผ่านสำเร็จ:", loginform_display != "none")

        # ลองใหม่ด้วยรหัสถูกต้อง
        login(page, "surveyor1", "NewSurveyor@2026", expect_mfa=True)
        code2 = totp_now(secret)
        page.fill("#mfaCode", code2)
        page.click("#mfaForm button[type=submit]")
        page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)
        print("OK: ล็อกอินด้วยรหัส TOTP ที่ถูกต้องสำเร็จ")

        # --- 4. ล็อกอินด้วยรหัสสำรอง (backup code) ---
        logout(page)
        login(page, "surveyor1", "NewSurveyor@2026", expect_mfa=True)
        page.fill("#mfaCode", backup_code)
        page.click("#mfaForm button[type=submit]")
        page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)
        print("OK: ล็อกอินด้วยรหัสสำรองสำเร็จ")

        # ใช้รหัสสำรองเดิมซ้ำ ต้องไม่ผ่าน (ใช้ได้ครั้งเดียว)
        logout(page)
        login(page, "surveyor1", "NewSurveyor@2026", expect_mfa=True)
        page.fill("#mfaCode", backup_code)
        page.click("#mfaForm button[type=submit]")
        page.wait_for_timeout(400)
        reuse_err = page.inner_text("#mfaErrorBox")
        print("Error ใช้รหัสสำรองซ้ำ:", reuse_err)
        assert "ไม่ถูกต้อง" in reuse_err
        assert page.url.endswith("/login.html")

        # เข้าระบบต่อให้จบด้วยรหัส TOTP ปกติ
        code3 = totp_now(secret)
        page.fill("#mfaCode", code3)
        page.click("#mfaForm button[type=submit]")
        page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)

        # --- 5. ปิดใช้งาน 2FA เอง (ต้องกรอกรหัสผ่านซ้ำ) ---
        page.goto(f"{BASE}/account.html")
        page.wait_for_selector("#mfaOnState", state="visible")
        page.click("#startMfaDisableBtn")
        page.wait_for_selector("#mfaDisableModal", state="visible")
        page.fill("#mfaDisablePassword", "wrong-password")
        page.click("#mfaDisableForm button[type=submit]")
        page.wait_for_timeout(400)
        disable_err = page.inner_text("#mfaDisableError")
        print("Error รหัสผ่านผิดตอนปิด 2FA:", disable_err)
        assert "ไม่ถูกต้อง" in disable_err

        page.fill("#mfaDisablePassword", "NewSurveyor@2026")
        page.click("#mfaDisableForm button[type=submit]")
        page.wait_for_selector("#mfaOffState", state="visible", timeout=5000)
        print("OK: ปิดใช้งาน 2FA เองสำเร็จ")

        logout(page)
        login(page, "surveyor1", "NewSurveyor@2026")
        print("OK: ล็อกอินตามปกติได้เลยหลังปิด 2FA แล้ว (ไม่ขอรหัส 2 ชั้นอีก)")

        # เปลี่ยนรหัสผ่านกลับเป็นค่าเดิม ไม่ให้กระทบ regression suite อื่น
        page.goto(f"{BASE}/account.html")
        page.wait_for_selector("#mfaOffState", state="visible")
        page.fill("#cp_current", "NewSurveyor@2026")
        page.fill("#cp_new", "Surveyor@12345")
        page.fill("#cp_confirm", "Surveyor@12345")
        page.click("#pwChangeForm button[type=submit]")
        page.wait_for_timeout(400)
        logout(page)

        # --- 6. ผู้ดูแลระบบปิดใช้งาน 2FA แทนกรณีเข้าระบบไม่ได้ (recovery) ---
        login(page, "surveyor1", "Surveyor@12345")
        page.goto(f"{BASE}/account.html")
        page.wait_for_selector("#mfaOffState", state="visible")
        page.click("#startMfaSetupBtn")
        page.wait_for_selector("#mfaSetupStep", state="visible")
        secret2 = page.inner_text("#mfaSecretText").strip()
        code4 = totp_now(secret2)
        page.fill("#mfaEnableCode", code4)
        page.click("#mfaEnableForm button[type=submit]")
        page.wait_for_selector("#mfaBackupCodesStep", state="visible", timeout=5000)
        page.click("#doneBackupCodesBtn")
        page.wait_for_selector("#mfaOnState", state="visible")
        logout(page)

        login(page, "admin", "Admin@12345")
        page.goto(f"{BASE}/users.html")
        page.wait_for_selector("#usersBody tr")
        row_text = page.inner_text("tr:has-text('surveyor1')")
        print("แถว surveyor1 แสดง badge 2FA เปิดใช้งาน:", "เปิดใช้งาน" in row_text)
        assert "เปิดใช้งาน" in row_text

        page.once("dialog", lambda d: d.accept())
        page.click("tr:has-text('surveyor1') button:has-text('ปิดการยืนยัน 2 ชั้น')")
        page.wait_for_timeout(700)
        row_text2 = page.inner_text("tr:has-text('surveyor1')")
        reset_btn_gone = "ปิดการยืนยัน 2 ชั้น" not in row_text2
        print("หลัง admin ปิด 2FA แล้ว ปุ่มรีเซ็ตหายไป (ไม่มี 2FA ให้ปิดอีก):", reset_btn_gone)
        assert reset_btn_gone
        logout(page)

        login(page, "surveyor1", "Surveyor@12345")
        print("OK: surveyor1 ล็อกอินได้ตามปกติหลังผู้ดูแลระบบปิด 2FA ให้ (recovery สำเร็จ)")
        logout(page)

        browser.close()

    print("\n--- Console/page errors ---")
    if errors:
        for e in errors:
            print("ERR:", e)
    else:
        print("(none)")
    print("\nALL MFA CHECKS PASSED")


if __name__ == "__main__":
    main()
