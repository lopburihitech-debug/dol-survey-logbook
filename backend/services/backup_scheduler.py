"""ตั้งเวลาสำรองข้อมูลฐานข้อมูลอัตโนมัติเป็นระยะระหว่างที่แอปรันอยู่ (background thread) — เรียกใช้ backup_db.py
เดิม (hot backup ผ่าน sqlite3 backup API ที่ปลอดภัยแม้แอปกำลังเขียนข้อมูลพร้อมกันอยู่พอดี) ไฟล์สำรองจะถูกเก็บไว้
ในโฟลเดอร์เดียวกับฐานข้อมูลหลัก (backend/data/backups/) ซึ่งอยู่บน persistent volume เดียวกัน จึงไม่หายไปตอน
deploy โค้ดใหม่ (deploy แค่เปลี่ยน container/โค้ด ไม่แตะ volume — ดูรายละเอียดที่คุยกับผู้ใช้ไว้)

ป้องกันเฉพาะกรณี "ฐานข้อมูลเสียหาย/ถูกลบผิดพลาดจากการใช้งานหรือ migration" เท่านั้น — **ไม่ได้ป้องกันกรณี
persistent volume ทั้งก้อนมีปัญหา** เพราะไฟล์สำรองอยู่ในโฟลเดอร์เดียวกับต้นฉบับ ถ้าต้องการป้องกันกรณีนั้นด้วย
ต้องคัดลอกไฟล์สำรองออกไปเก็บนอกระบบเป็นระยะ (ยังไม่ได้ทำอัตโนมัติ — ดูเหตุผลด้านเทคนิคที่คุยกับผู้ใช้ไว้)

ทำงานเฉพาะกับ SQLite เท่านั้น (ข้าม silently ถ้าตั้งค่า DATABASE_URL ไว้ เพราะ backup_db.py รองรับเฉพาะ SQLite)

หมายเหตุสำคัญเรื่อง gunicorn หลาย worker (ดู entrypoint.sh: `-w 2`): ถ้าปล่อยให้ทุก worker process ตั้ง thread
สำรองข้อมูลของตัวเอง จะสำรองซ้ำกันหลายรอบโดยไม่จำเป็น (สิ้นเปลือง I/O เฉยๆ ไม่ทำให้ข้อมูลเสียหาย เพราะแต่ละไฟล์
สำรองมีชื่อไม่ซ้ำกันตาม timestamp) จึงใช้ file lock (fcntl.flock แบบ non-blocking) เลือก worker เพียงตัวเดียว
เป็น "หัวหน้า" ที่จะรันตารางเวลานี้จริง — worker อื่นเช็คแล้วเจอ lock ถูกถือครองอยู่ก็จะข้ามไปเงียบๆ lock จะถูก
ปล่อยอัตโนมัติเมื่อ worker นั้น process ตายไป (OS จัดการให้ ไม่ต้องเขียนโค้ด cleanup เอง)
"""
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_INTERVAL_SECONDS = 6 * 60 * 60  # ทุก 6 ชั่วโมง — ปรับได้ตามความถี่ที่ต้องการระหว่างช่วงลงข้อมูลทดสอบจริง
BACKUP_KEEP_COUNT = 28  # ที่ความถี่ 6 ชม./ครั้ง = เก็บย้อนหลังประมาณ 7 วัน
_LOCK_FILE_NAME = ".backup_scheduler.lock"

_lock_file_handle = None  # ต้องเก็บ reference ไว้ระดับ module กัน garbage collector ปิด lock ทิ้งก่อนเวลา


def _try_become_backup_leader(db_path: str) -> bool:
    """คืน True ถ้า process (worker) นี้ได้เป็น "หัวหน้า" ที่จะรันตารางสำรองข้อมูล (ถือ lock ไฟล์ไว้ได้สำเร็จ)"""
    global _lock_file_handle
    try:
        import fcntl
    except ImportError:
        return False  # Windows ไม่มี fcntl — แต่ deploy จริงรันบน Linux (Railway) เสมอ ไม่กระทบการใช้งานจริง

    lock_path = Path(db_path).resolve().parent / _LOCK_FILE_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False  # worker อื่นถือ lock อยู่แล้ว (หรือเปิดไฟล์ไม่ได้ — ไม่ใช่กรณีร้ายแรง แค่ข้ามไป)
    _lock_file_handle = lock_file
    return True


def _run_backup_once_safely() -> None:
    from backup_db import backup_once, prune_old_backups

    try:
        backup_once()
        prune_old_backups(keep=BACKUP_KEEP_COUNT)
    except SystemExit:
        # backup_once()/argparse เรียก sys.exit(1) ตอนใช้เป็นสคริปต์ CLI ถ้าไม่พบไฟล์ฐานข้อมูล — ต้องดักไว้ไม่ให้
        # SystemExit หลุดไปทำให้ thread นี้ตาย (และไม่ควรกระทบ worker หลักที่กำลังให้บริการ request อยู่ด้วย)
        logger.warning("สำรองข้อมูลอัตโนมัติข้ามรอบนี้ไป (ยังไม่พบไฟล์ฐานข้อมูล — ปกติตอนเพิ่งขึ้นระบบครั้งแรก)")
    except Exception:
        logger.exception("สำรองข้อมูลอัตโนมัติล้มเหลว (จะลองใหม่รอบถัดไป)")


def _backup_loop() -> None:
    _run_backup_once_safely()  # สำรองทันทีครั้งแรกตอน worker เริ่มทำงาน ไม่ต้องรอครบรอบแรกก่อน
    while True:
        time.sleep(BACKUP_INTERVAL_SECONDS)
        _run_backup_once_safely()


def start_scheduled_backups(db_path: str) -> None:
    """เรียกครั้งเดียวตอนแอป start ขึ้นมา (จาก app.py) — ปลอดภัยที่จะเรียกจากทุก worker process พร้อมกัน เพราะ
    มีแค่ worker เดียวที่จะได้เป็นหัวหน้าจริง (ดู _try_become_backup_leader)"""
    if not _try_become_backup_leader(db_path):
        return
    thread = threading.Thread(target=_backup_loop, daemon=True, name="backup-scheduler")
    thread.start()
    logger.info(
        "เริ่มตั้งเวลาสำรองข้อมูลอัตโนมัติทุก %d ชั่วโมง (worker นี้เป็นหัวหน้า, เก็บย้อนหลังไว้ %d ไฟล์)",
        BACKUP_INTERVAL_SECONDS // 3600,
        BACKUP_KEEP_COUNT,
    )
