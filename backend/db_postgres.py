"""
Adapter บาง ๆ สำหรับเชื่อมต่อ PostgreSQL ออนไลน์ (เช่น Railway Postgres, Render Postgres, Supabase, Neon)

ให้ interface เหมือน sqlite3.Connection ทุกจุดที่โค้ดส่วนอื่นเรียกใช้ (execute / commit / close / executescript)
เพื่อไม่ต้องแก้โค้ด business logic ในทุก blueprint เลยแม้แต่บรรทัดเดียว — เปิดใช้งานได้ทันทีแค่ตั้งค่า
environment variable DATABASE_URL (ดู db.py)

หมายเหตุ: โมดูล psycopg2 จะถูก import ก็ต่อเมื่อมีการตั้งค่า DATABASE_URL เท่านั้น (lazy import) ดังนั้นถ้ารันแบบ
SQLite ตามปกติ (ไม่ตั้ง DATABASE_URL) เครื่องที่ไม่ได้ติดตั้ง psycopg2-binary ก็ยังใช้งานได้ตามปกติไม่มีผลกระทบ
"""
import re

# แปลง LIKE ให้เป็น ILIKE (case-insensitive) เพื่อให้พฤติกรรมค้นหาเหมือน SQLite เดิม
# (SQLite's LIKE เป็น case-insensitive โดย default สำหรับ ASCII แต่ PostgreSQL's LIKE เป็น case-sensitive)
_LIKE_RE = re.compile(r"\bLIKE\b", re.IGNORECASE)


def _translate(sql: str) -> str:
    """แปลง SQL แบบที่โค้ดเขียนไว้สำหรับ SQLite (placeholder '?') ให้ใช้กับ PostgreSQL (placeholder '%s') ได้"""
    sql = sql.replace("?", "%s")
    sql = _LIKE_RE.sub("ILIKE", sql)
    return sql


def _normalize_dsn(dsn: str) -> str:
    """บังคับใช้ SSL ถ้ายังไม่ได้ระบุไว้ในค่า DATABASE_URL — ผู้ให้บริการ Postgres แบบ managed
    ส่วนใหญ่ (Railway/Render/Supabase/Neon) กำหนดให้เชื่อมต่อผ่าน SSL เท่านั้น"""
    if "sslmode=" in dsn:
        return dsn
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}sslmode=require"


class PGCursorResult:
    """ห่อ psycopg2 cursor ให้พฤติกรรมเหมือนค่าที่ conn.execute(...) ของ sqlite3 คืนกลับมา
    (แถวที่ได้เป็น RealDictRow ซึ่งเป็น dict subclass อยู่แล้ว — dict(row) และ row["col"] ใช้ได้ตรงๆ)"""

    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount


class PGConnection:
    def __init__(self, dsn: str):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise RuntimeError(
                "ตั้งค่า DATABASE_URL ไว้แต่ยังไม่ได้ติดตั้ง psycopg2-binary — "
                "รัน `pip install -r requirements.txt` ก่อน (แพลตฟอร์ม cloud เช่น Railway/Render จะติดตั้งให้อัตโนมัติตอน build อยู่แล้ว)"
            ) from exc

        self._conn = psycopg2.connect(_normalize_dsn(dsn), cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(_translate(sql), params or [])
        return PGCursorResult(cur)

    def executescript(self, sql):
        # psycopg2 รองรับการรันหลาย statement คั่นด้วย ';' ในการ execute() ครั้งเดียว ตราบใดที่ไม่มี params
        cur = self._conn.cursor()
        cur.execute(sql)
        cur.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_pg_connection(dsn: str) -> PGConnection:
    return PGConnection(dsn)
