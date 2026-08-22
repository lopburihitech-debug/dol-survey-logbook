"""ทดสอบหน้าติดตามงานสำหรับประชาชน (track.html + /api/v1/public/track) — ไม่ต้องล็อกอิน
ครอบคลุม: ค้นหาสำเร็จ (เลข รว.19 + เบอร์โทร 4 ตัวท้ายถูกต้อง), เบอร์โทรผิด, เลข รว.19 ผิด,
กรอกไม่ครบ, พิมพ์เลข รว.19 ตัวพิมพ์เล็ก/ใหญ่ปนกัน (ต้องหาเจอเหมือนกัน — COLLATE NOCASE)"""
import sqlite3
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
DB_PATH = "../backend/data/dol_survey_logbook.db"


def get_seed_case():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT case_code, requester_contact FROM survey_cases WHERE requester_contact IS NOT NULL LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row)


def main():
    case = get_seed_case()
    case_code = case["case_code"]
    phone_last4 = "".join(c for c in case["requester_contact"] if c.isdigit())[-4:]
    print(f"ใช้เคสทดสอบ: {case_code} / โทร ...{phone_last4}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})

        # 1) ค้นหาสำเร็จ
        page.goto(f"{BASE}/track.html")
        page.fill("#caseCode", case_code)
        page.fill("#phoneLast4", phone_last4)
        page.click("#searchBtn")
        page.wait_for_selector("#resultCard", state="visible", timeout=5000)
        assert page.inner_text("#rCaseCode") == case_code, "ควรแสดงเลข รว.19 ที่ค้นหาถูกต้อง"
        assert page.inner_text("#rStatusBadge").strip() != "", "ควรมีป้ายสถานะ"
        print("OK: ค้นหาสำเร็จ แสดงผลถูกต้อง")

        # 2) ตัวพิมพ์เล็ก/ใหญ่ปนกัน ต้องหาเจอเหมือนกัน
        page.click("#searchAgainBtn")
        page.fill("#caseCode", case_code.lower())
        page.fill("#phoneLast4", phone_last4)
        page.click("#searchBtn")
        page.wait_for_selector("#resultCard", state="visible", timeout=5000)
        print("OK: ค้นหาแบบตัวพิมพ์เล็กก็เจอเหมือนกัน (case-insensitive)")

        # 3) เบอร์โทรผิด -> generic error, ไม่บอกว่าเลข รว.19 ถูกหรือผิด
        page.click("#searchAgainBtn")
        page.fill("#caseCode", case_code)
        page.fill("#phoneLast4", "0000")
        page.click("#searchBtn")
        page.wait_for_selector("#errorBox", state="visible", timeout=5000)
        err_wrong_phone = page.inner_text("#errorBox")
        print("OK: เบอร์โทรผิด ->", err_wrong_phone)

        # 4) เลข รว.19 ผิด -> ข้อความ error เดียวกันเป๊ะ (กันเดา/enumerate)
        page.fill("#caseCode", "LB99-2569-99999")
        page.fill("#phoneLast4", "9999")
        page.click("#searchBtn")
        page.wait_for_selector("#errorBox", state="visible", timeout=5000)
        err_wrong_code = page.inner_text("#errorBox")
        assert err_wrong_code == err_wrong_phone, "ข้อความ error ต้องเหมือนกันทุกกรณีที่หาไม่เจอ (กัน enumeration)"
        print("OK: เลข รว.19 ผิด ข้อความ error เหมือนกับกรณีเบอร์โทรผิด")

        # 5) กรอกเบอร์ไม่ครบ 4 หลัก -> ปุ่มควรถูก block ด้วย maxlength หรือ backend reject
        page.fill("#caseCode", case_code)
        page.fill("#phoneLast4", "12")
        page.click("#searchBtn")
        page.wait_for_selector("#errorBox", state="visible", timeout=5000)
        print("OK: เบอร์ไม่ครบ 4 หลัก ->", page.inner_text("#errorBox"))

        browser.close()

    print("\nการทดสอบหน้าติดตามงานประชาชนผ่านทั้งหมด")


if __name__ == "__main__":
    main()
