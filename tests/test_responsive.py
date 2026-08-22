"""ทดสอบ Responsive Design ที่ขนาดจอ มือถือ / แท็บเล็ต / เดสก์ท็อป
ตรวจสอบ: ไม่มี horizontal overflow, เมนูแฮมเบอร์เกอร์ทำงานได้บนจอเล็ก, ตาราง/ฟอร์ม/การ์ดปรับขนาดถูกต้อง
"""
import os
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"

VIEWPORTS = [
    ("mobile", 375, 667),
    ("tablet", 768, 1024),
    ("desktop", 1280, 900),
]


def screenshot(page, name):
    os.makedirs("../screenshots/responsive", exist_ok=True)
    page.screenshot(path=f"../screenshots/responsive/{name}.png", full_page=True)


def check_overflow(page, label):
    """คืนค่า True ถ้าไม่มี horizontal overflow (scrollWidth <= clientWidth + 1px tolerance)"""
    result = page.evaluate(
        """() => {
            const de = document.documentElement;
            return { scrollWidth: de.scrollWidth, clientWidth: de.clientWidth };
        }"""
    )
    overflow = result["scrollWidth"] - result["clientWidth"]
    status = "OK" if overflow <= 1 else f"OVERFLOW {overflow}px"
    print(f"  [{label}] scrollWidth={result['scrollWidth']} clientWidth={result['clientWidth']} -> {status}")
    return overflow <= 1


def login(page, username, password):
    page.goto(f"{BASE}/login.html")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/dashboard.html", timeout=5000)


def main():
    all_ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch()

        for label, w, h in VIEWPORTS:
            print(f"\n=== Viewport: {label} ({w}x{h}) ===")
            page = browser.new_page(viewport={"width": w, "height": h})
            errors = []
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            # --- Login page ---
            page.goto(f"{BASE}/login.html")
            page.wait_for_selector(".login-box")
            screenshot(page, f"{label}_01_login")
            ok = check_overflow(page, "login")
            all_ok = all_ok and ok

            # --- Login as admin, dashboard ---
            login(page, "admin", "Admin@12345")
            page.wait_for_selector("#kpiGrid .kpi-card")
            screenshot(page, f"{label}_02_dashboard")
            ok = check_overflow(page, "dashboard")
            all_ok = all_ok and ok

            # hamburger menu check on small screens
            if w <= 768:
                nav_toggle = page.locator("#navToggle")
                is_visible = nav_toggle.is_visible()
                print(f"  [{label}] nav-toggle visible: {is_visible}")
                if is_visible:
                    nav_toggle.click()
                    page.wait_for_timeout(200)
                    nav_visible = page.locator(".topbar nav").is_visible()
                    print(f"  [{label}] nav opened after click: {nav_visible}")
                    screenshot(page, f"{label}_02b_dashboard_nav_open")
                    all_ok = all_ok and nav_visible
            else:
                nav_toggle_visible = page.locator("#navToggle").is_visible()
                print(f"  [{label}] nav-toggle hidden on desktop as expected: {not nav_toggle_visible}")
                all_ok = all_ok and (not nav_toggle_visible)

            # --- Cases list page ---
            page.goto(f"{BASE}/cases.html")
            page.wait_for_selector("#casesBody tr")
            screenshot(page, f"{label}_03_cases")
            ok = check_overflow(page, "cases")
            all_ok = all_ok and ok

            # open "new case" modal to check modal responsiveness
            new_btn = page.locator("#newCaseBtn")
            if new_btn.is_visible():
                new_btn.click()
                page.wait_for_selector("#newCaseModal", state="visible")
                screenshot(page, f"{label}_04_new_case_modal")
                ok = check_overflow(page, "cases (modal open)")
                all_ok = all_ok and ok
                page.click("#newCaseModal button:has-text('ยกเลิก')")

            # --- Case detail page ---
            first_row = page.locator("#casesBody tr.clickable").first
            if first_row.count() > 0:
                first_row.click()
                page.wait_for_selector(".info-grid")
                screenshot(page, f"{label}_05_case_detail")
                ok = check_overflow(page, "case detail")
                all_ok = all_ok and ok

            # --- Surveyor profile page ---
            page.goto(f"{BASE}/surveyors.html")
            page.wait_for_selector("#surveyorsBody tr")
            sid = page.evaluate("() => window._surveyorRows[0].id")
            page.goto(f"{BASE}/surveyor-profile.html?id={sid}")
            page.wait_for_selector(".kpi-grid .kpi-card")
            screenshot(page, f"{label}_06_surveyor_profile")
            ok = check_overflow(page, "surveyor profile")
            all_ok = all_ok and ok

            # --- Calendar page ---
            page.goto(f"{BASE}/calendar.html")
            page.wait_for_selector(".cal-grid")
            screenshot(page, f"{label}_07_calendar")
            ok = check_overflow(page, "calendar")
            all_ok = all_ok and ok

            # open a case popup from the calendar to check modal responsiveness (if any case exists today;
            # ".to-case" in the "today's out" card always opens the popup directly, unlike ".cal-chip" which
            # may instead open the day-list modal when a surveyor has more than one case that day)
            to_case = page.locator(".today-out-card .to-case").first
            if to_case.count() > 0:
                to_case.click()
                page.wait_for_selector("#casePopup", state="visible")
                screenshot(page, f"{label}_08_calendar_popup")
                ok = check_overflow(page, "calendar (popup open)")
                all_ok = all_ok and ok
                page.click("#casePopup .btn-outline")

            if errors:
                print(f"  [{label}] Console/page errors:")
                for e in errors:
                    print("   ERR:", e)
            else:
                print(f"  [{label}] no console errors")

            page.close()

        browser.close()

    print("\n=== SUMMARY ===")
    print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED (see OVERFLOW / False above)")


if __name__ == "__main__":
    main()
