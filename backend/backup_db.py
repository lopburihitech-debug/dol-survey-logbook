"""สำรองข้อมูลฐานข้อมูล SQLite แบบปลอดภัย (hot backup ผ่าน sqlite3 backup API — ใช้งานได้แม้ระบบกำลังทำงานอยู่
และมีการเขียนข้อมูลพร้อมกันพอดี ต่างจากการ copy ไฟล์ .db ตรงๆ ที่อาจได้ไฟล์ backup ที่ข้อมูลไม่สมบูรณ์)

ใช้ได้เฉพาะตอนใช้ SQLite เท่านั้น (ไม่ได้ตั้งค่า DATABASE_URL) — ถ้าต่อ PostgreSQL ออนไลน์อยู่ ให้ใช้เครื่องมือ
backup ของผู้ให้บริการแทน (Railway/Render/Supabase/Neon ส่วนใหญ่มี automated backup ให้อยู่แล้วในแผนเสียเงิน
หรือใช้ pg_dump เองก็ได้)

วิธีใช้:
    python backup_db.py                 # สำรองครั้งเดียว ไปที่ backend/data/backups/
    python backup_db.py --keep 30       # เก็บไฟล์สำรองล่าสุดไว้กี่ไฟล์ (ค่า default: 14) แล้วลบไฟล์เก่ากว่านั้นทิ้ง

ตั้งให้รันอัตโนมัติเป็นระยะบน production ได้ เช่น:
- Railway: เพิ่มบริการ (service) ใหม่แบบ "Cron Job" ในโปรเจกต์เดียวกัน ให้รันคำสั่ง
  `cd backend && python backup_db.py` ทุกวัน (ดูเอกสาร Railway → Cron Jobs)
- Render: ใช้ฟีเจอร์ "Cron Jobs" ของ Render ชี้มาที่คำสั่งเดียวกัน
- เครื่อง Linux ทั่วไป (self-host): เพิ่มใน crontab เช่น `0 2 * * * cd /app/backend && python backup_db.py`

หมายเหตุ: ไฟล์ backup จะถูกเก็บไว้ใน backend/data/backups/ ซึ่งอยู่ภายใต้โฟลเดอร์เดียวกับฐานข้อมูลหลัก
(backend/data/) จึงอยู่บน persistent volume เดียวกันโดยอัตโนมัติถ้าตั้งค่า volume ตาม DEPLOY.md ไว้แล้ว —
แต่เพื่อความปลอดภัยสูงสุด ควรดาวน์โหลดไฟล์ backup ออกไปเก็บไว้อีกที่นอกเหนือจาก volume นี้เป็นระยะด้วย
(เช่น อัปโหลดขึ้น cloud storage แยกต่างหาก) เผื่อกรณี volume ทั้งก้อนมีปัญหา
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from db import DATABASE_URL, DB_PATH

BACKUP_DIR = Path(DB_PATH).resolve().parent / "backups"
DEFAULT_KEEP = 14


def backup_once() -> Path:
    if DATABASE_URL:
        print(
            "กำลังใช้ PostgreSQL ออนไลน์ (ตั้งค่า DATABASE_URL ไว้) อยู่ — สคริปต์นี้สำรองข้อมูลเฉพาะ SQLite เท่านั้น\n"
            "กรุณาใช้เครื่องมือ backup ของผู้ให้บริการฐานข้อมูลที่ใช้อยู่แทน (ดูคอมเมนต์ด้านบนของไฟล์นี้)"
        )
        sys.exit(1)

    src_path = Path(DB_PATH)
    if not src_path.exists():
        print(f"ไม่พบไฟล์ฐานข้อมูลที่ {src_path} — ยังไม่เคยรันระบบ/seed ข้อมูลหรือเปล่า?")
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest_path = BACKUP_DIR / f"dol_survey_logbook_{timestamp}.db"

    src_conn = sqlite3.connect(str(src_path))
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()

    size_kb = dest_path.stat().st_size / 1024
    print(f"สำรองข้อมูลสำเร็จ: {dest_path} ({size_kb:.1f} KB)")
    return dest_path


def prune_old_backups(keep: int) -> None:
    if not BACKUP_DIR.exists():
        return
    backups = sorted(BACKUP_DIR.glob("dol_survey_logbook_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_backup in backups[keep:]:
        old_backup.unlink()
        print(f"ลบไฟล์สำรองเก่า (เกินจำนวนที่ตั้งเก็บไว้): {old_backup.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="สำรองข้อมูลฐานข้อมูล SQLite ของ DOL Survey Logbook")
    parser.add_argument(
        "--keep", type=int, default=DEFAULT_KEEP,
        help=f"จำนวนไฟล์สำรองล่าสุดที่จะเก็บไว้ ไฟล์เก่ากว่านั้นจะถูกลบทิ้ง (ค่า default: {DEFAULT_KEEP})",
    )
    args = parser.parse_args()

    backup_once()
    prune_old_backups(args.keep)
