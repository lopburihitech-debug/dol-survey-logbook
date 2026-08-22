"""
เชื่อมต่อฐานข้อมูล — เลือกอัตโนมัติจาก environment variable `DATABASE_URL`:

- ไม่ตั้งค่า DATABASE_URL (ค่าเริ่มต้น) -> ใช้ SQLite ไฟล์เดียว รันได้ทันทีโดยไม่ต้องติดตั้งบริการฐานข้อมูลแยก
  เหมาะกับการทดสอบ/พัฒนา หรือ deploy แบบ 1 container ต่อ 1 สำนักงาน
- ตั้งค่า DATABASE_URL เป็น connection string ของ PostgreSQL (เช่น postgres://user:pass@host:5432/db
  จาก Railway/Render/Supabase/Neon) -> ระบบจะเชื่อมต่อฐานข้อมูลออนไลน์แทนทันที เหมาะกับการให้หลายสาขา/หลาย
  container เข้าถึงข้อมูลชุดเดียวกันพร้อมกัน (ดู db_postgres.py และ DEPLOY.md หัวข้อ "ต่อฐานข้อมูลออนไลน์")

schema.sql เขียนด้วย SQL มาตรฐานที่ใช้ได้กับทั้งสองฐานข้อมูลโดยแทบไม่ต้องแก้ไข (มีแค่บรรทัด PRAGMA ของ SQLite
ที่ถูกตัดออกอัตโนมัติเมื่อรันกับ PostgreSQL เพราะ PostgreSQL บังคับ foreign key อยู่แล้วโดย default)
"""
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL")  # ถ้ามีค่า -> ใช้ PostgreSQL ออนไลน์แทน SQLite
DB_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "data" / "dol_survey_logbook.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_connection():
    if DATABASE_URL:
        from db_postgres import get_pg_connection
        return get_pg_connection(DATABASE_URL)

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# คอลัมน์ที่เพิ่มทีหลังจาก schema.sql เดิม — สำหรับฐานข้อมูลที่มีอยู่แล้วก่อนหน้านี้ (CREATE TABLE IF NOT EXISTS
# จะไม่เพิ่มคอลัมน์ใหม่ให้ตารางที่มีอยู่แล้ว จึงต้อง ALTER TABLE เสริมแบบ idempotent ตรงนี้)
_COLUMN_MIGRATIONS = [
    ("parcels", "location_url", "TEXT"),
    ("survey_cases", "survey_result", "TEXT"),
    ("survey_cases", "mapping_status", "TEXT"),
    ("survey_cases", "neighbor_status", "TEXT"),
    ("survey_cases", "announcement_status", "TEXT"),
    ("case_documents", "sequence_no", "INTEGER"),
    ("case_documents", "label", "TEXT"),
    ("users", "mfa_secret", "TEXT"),
    ("users", "failed_login_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "lockout_until", "TEXT"),
]


def _run_column_migrations(conn) -> None:
    for table, column, coltype in _COLUMN_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            conn.commit()
        except Exception:
            # คอลัมน์นี้มีอยู่แล้วจากการรันครั้งก่อน (หรือ error อื่นที่ไม่ร้ายแรง) -> rollback แล้วข้ามไป
            try:
                conn.rollback()
            except Exception:
                pass


def init_db() -> None:
    conn = get_connection()
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        if DATABASE_URL:
            # PostgreSQL ไม่รู้จัก PRAGMA ของ SQLite (และไม่จำเป็นต้องใช้ เพราะบังคับ FK อยู่แล้วโดย default)
            schema_sql = "\n".join(
                line for line in schema_sql.splitlines() if not line.strip().upper().startswith("PRAGMA")
            )
        conn.executescript(schema_sql)
        conn.commit()
        _run_column_migrations(conn)
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]
