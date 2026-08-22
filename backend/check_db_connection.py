"""
เครื่องมือเช็คว่าต่อฐานข้อมูลออนไลน์ (DATABASE_URL) ได้จริงหรือไม่ — รันคำสั่งนี้บนเครื่อง/เซิร์ฟเวอร์ที่มีอินเทอร์เน็ต
จริง (ไม่ใช่ sandbox ที่จำกัดเครือข่าย) หลังตั้งค่า DATABASE_URL แล้ว เพื่อยืนยันก่อน deploy จริง

วิธีใช้:
    export DATABASE_URL="postgres://user:pass@host:5432/dbname"
    python check_db_connection.py
"""
import os
import sys


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("❌ ไม่พบ environment variable DATABASE_URL — ตั้งค่าก่อนแล้วรันใหม่")
        print('   ตัวอย่าง: export DATABASE_URL="postgres://user:pass@host:5432/dbname"')
        sys.exit(1)

    masked = dsn.split("@")[-1] if "@" in dsn else dsn
    print(f"กำลังทดสอบเชื่อมต่อไปยัง: ...@{masked}")

    try:
        from db_postgres import get_pg_connection
    except Exception as exc:
        print(f"❌ โหลดโมดูล db_postgres ไม่สำเร็จ: {exc}")
        sys.exit(1)

    try:
        conn = get_pg_connection(dsn)
    except RuntimeError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"❌ เชื่อมต่อฐานข้อมูลไม่สำเร็จ: {exc}")
        print("   ตรวจสอบ: connection string ถูกต้องหรือไม่ / ฐานข้อมูลเปิดใช้งานอยู่หรือไม่ / อนุญาต IP ที่เชื่อมต่อมาหรือไม่ (บาง provider ต้องเปิด allowlist)")
        sys.exit(1)

    try:
        row = conn.execute("SELECT version() AS v").fetchone()
        print(f"✅ เชื่อมต่อสำเร็จ — PostgreSQL: {row['v']}")

        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
        ).fetchall()
        if tables:
            print(f"พบตารางอยู่แล้ว {len(tables)} ตาราง — ระบบสร้าง schema ไว้แล้ว พร้อมใช้งาน")
        else:
            print("ยังไม่มีตารางในฐานข้อมูลนี้ — รัน `python seed.py` เพื่อสร้าง schema + ข้อมูลตั้งต้นให้อัตโนมัติ")
    except Exception as exc:
        print(f"❌ เชื่อมต่อได้แต่รันคำสั่งทดสอบไม่สำเร็จ: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
