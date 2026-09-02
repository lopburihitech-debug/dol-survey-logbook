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
    ("parcels", "deed_type", "TEXT"),  # ประเภทเอกสารสิทธิ์ เช่น "โฉนดที่ดิน", "น.ส.3", "น.ส.3 ก.", "น.ส.3 ข."
    ("survey_cases", "survey_result", "TEXT"),
    ("survey_cases", "mapping_status", "TEXT"),
    ("survey_cases", "neighbor_status", "TEXT"),
    ("survey_cases", "announcement_status", "TEXT"),
    ("case_documents", "sequence_no", "INTEGER"),
    ("case_documents", "label", "TEXT"),
    ("case_documents", "marker_group", "TEXT"),
    ("users", "mfa_secret", "TEXT"),
    ("users", "failed_login_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "lockout_until", "TEXT"),
    ("users", "pin_hash", "TEXT"),  # เข้ารหัสด้วย PBKDF2 เดียวกับ password_hash — NULL = ยังไม่ได้ตั้ง PIN
    ("complaints", "reply_text", "TEXT"),
    ("complaints", "replied_by", "TEXT"),
    ("complaints", "replied_at", "TEXT"),
    ("offices", "lat", "REAL"),
    ("offices", "lng", "REAL"),
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


def _migrate_case_code_uniqueness(conn) -> None:
    """เดิมเลข รว.12 (case_code) unique ทั้งระบบ ทำให้ 2 สำนักงานใช้เลขเดียวกันไม่ได้ทั้งที่ในความเป็นจริงแต่ละ
    สำนักงานออกเลขของตัวเองแยกกัน (ดูคอมเมนต์ตรง idx_survey_cases_office_code ใน schema.sql) แก้เป็น unique
    เฉพาะภายในสำนักงานเดียวกันแทน (ผูกคู่ office_id + case_code) — ต้องสร้างตารางใหม่แล้วย้ายข้อมูล เพราะ SQLite
    แก้ UNIQUE ที่ผูกกับคอลัมน์โดยตรงด้วย ALTER TABLE ตรงๆ ไม่ได้ ตรวจก่อนว่า migrate ไปแล้วหรือยัง (มี index ใหม่
    อยู่แล้ว) กันรัน rebuild ซ้ำทุกครั้งที่แอปสตาร์ท — id ของทุกแถวคงเดิมทั้งหมด ตารางอื่นที่ foreign key อ้างอิง
    survey_cases(id) (case_assignments/case_documents/appointments/parcels/case_status_history ฯลฯ) จึงไม่กระทบ"""
    if DATABASE_URL:
        return  # PostgreSQL: migration นี้ยังไม่ได้ทดสอบกับฐานข้อมูลจริง ข้ามไปก่อน (ดู DEPLOY.md)

    # เช็คว่ายังมี unique constraint แบบเดี่ยวผูกกับคอลัมน์ case_code อยู่หรือไม่ (origin='u' = สร้างจาก UNIQUE
    # บนคอลัมน์ตรงๆ ตอน CREATE TABLE ไม่ใช่จาก CREATE INDEX ทีหลัง) ถ้าไม่เจอแล้วแปลว่า migrate ไปแล้ว หรือเป็น
    # ตารางใหม่ที่ถูกต้องอยู่แล้วตั้งแต่แรก — เช็คจาก index จริงในไฟล์ฐานข้อมูล ไม่ใช่แค่เดาจากชื่อ index ใหม่
    # (กันกรณี executescript ข้างบนสร้าง idx_survey_cases_office_code ไปแล้วทั้งที่ตารางเก่ายังไม่ได้ rebuild จริง)
    has_old_single_column_unique = False
    for idx in conn.execute("PRAGMA index_list(survey_cases)").fetchall():
        if not idx["unique"] or idx["origin"] != "u":
            continue
        cols = [c["name"] for c in conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()]
        if cols == ["case_code"]:
            has_old_single_column_unique = True
            break
    if not has_old_single_column_unique:
        return
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            BEGIN TRANSACTION;
            CREATE TABLE survey_cases_new (
                id TEXT PRIMARY KEY,
                case_code TEXT NOT NULL,
                office_id TEXT NOT NULL REFERENCES offices(id),
                survey_type_id TEXT NOT NULL REFERENCES survey_types(id),
                requester_name TEXT NOT NULL,
                requester_contact TEXT,
                citizen_id TEXT REFERENCES citizens(id),
                received_date TEXT NOT NULL,
                due_date TEXT,
                appointment_date TEXT,
                status TEXT NOT NULL DEFAULT 'RECEIVED',
                priority TEXT NOT NULL DEFAULT 'normal',
                survey_result TEXT,
                mapping_status TEXT,
                neighbor_status TEXT,
                announcement_status TEXT,
                created_by TEXT REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO survey_cases_new (
                id, case_code, office_id, survey_type_id, requester_name, requester_contact,
                citizen_id, received_date, due_date, appointment_date, status, priority,
                survey_result, mapping_status, neighbor_status, announcement_status,
                created_by, created_at, updated_at
            )
            SELECT
                id, case_code, office_id, survey_type_id, requester_name, requester_contact,
                citizen_id, received_date, due_date, appointment_date, status, priority,
                survey_result, mapping_status, neighbor_status, announcement_status,
                created_by, created_at, updated_at
            FROM survey_cases;
            DROP TABLE survey_cases;
            ALTER TABLE survey_cases_new RENAME TO survey_cases;
            CREATE INDEX IF NOT EXISTS idx_survey_cases_status ON survey_cases(status);
            CREATE INDEX IF NOT EXISTS idx_survey_cases_office ON survey_cases(office_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_survey_cases_office_code ON survey_cases(office_id, case_code);
            COMMIT;
            """
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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
        _migrate_case_code_uniqueness(conn)
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]
