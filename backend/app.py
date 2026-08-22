"""
DOL Survey Logbook — Flask app เดียวที่ให้บริการทั้ง API (/api/v1/...) และหน้าเว็บ (static files ใน ../frontend)
เลือกใช้โปรเซสเดียวโดยตั้งใจ เพื่อให้ deploy ง่ายที่สุดเวลาต้องขยายไปแต่ละสาขา (1 container ต่อ 1 สำนักงาน)

รันด้วย: python app.py  (dev server) หรือ gunicorn -b 0.0.0.0:8000 app:app (production)
"""
import logging
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

logger = logging.getLogger(__name__)

from blueprints import appointments, auth, case_documents, dashboard, offices, public_track, survey_cases, survey_types, surveyors, users
from config import settings, INSECURE_DEFAULT_JWT_SECRET
from db import init_db

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.json.ensure_ascii = False  # ให้ตอบกลับเป็นภาษาไทยตรงๆ อ่านง่ายใน response แทนการ escape เป็น \\uXXXX

app.register_blueprint(auth.bp)
app.register_blueprint(users.bp)
app.register_blueprint(offices.bp)
app.register_blueprint(surveyors.bp)
app.register_blueprint(surveyors.uploads_bp)
app.register_blueprint(survey_types.bp)
app.register_blueprint(survey_cases.bp)
app.register_blueprint(appointments.bp)
app.register_blueprint(case_documents.bp)
app.register_blueprint(dashboard.bp)
app.register_blueprint(public_track.bp)


@app.get("/health")
def health_check():
    return jsonify({"status": "ok", "service": settings.APP_NAME})


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "login.html")


@app.errorhandler(404)
def not_found(e):
    # ถ้าเป็น request ที่เรียก API ให้ตอบ JSON, ถ้าเป็นหน้าเว็บให้ปล่อยผ่านไปหน้า login (SPA-like fallback)
    from flask import request

    if request.path.startswith("/api/"):
        return jsonify({"error": {"message": "ไม่พบ endpoint ที่ระบุ"}}), 404
    return send_from_directory(app.static_folder, "login.html")


@app.errorhandler(500)
def server_error(e):
    # log รายละเอียด exception จริงไว้ฝั่ง server เสมอ (เห็นได้จาก log ของ gunicorn/แพลตฟอร์ม deploy)
    # ไม่ว่าจะส่งกลับไปให้ผู้ใช้เห็นหรือไม่ก็ตาม เพื่อให้ยังตรวจสอบปัญหาย้อนหลังได้
    logger.error("เกิดข้อผิดพลาดที่ไม่ได้ดักไว้ (unhandled exception)", exc_info=e)

    if __name__ == "__main__":
        # dev: รันตรงด้วย python app.py — โชว์รายละเอียด error กลับไปด้วยเพื่อ debug ได้ง่ายในเครื่อง
        return jsonify({"error": {"message": "เกิดข้อผิดพลาดภายในระบบ", "detail": str(e)}}), 500

    # production: รันผ่าน gunicorn — ไม่ส่งรายละเอียด exception กลับไปให้ผู้ใช้เห็น เพราะอาจหลุดข้อมูลภายใน
    # (path ไฟล์ในเครื่อง server, ชื่อตาราง/คอลัมน์ในฐานข้อมูล ฯลฯ) รายละเอียดจริงถูก log ไว้ฝั่ง server แล้วด้านบน
    return jsonify({"error": {"message": "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่อีกครั้ง หรือติดต่อผู้ดูแลระบบหากยังพบปัญหา"}}), 500


def _guard_jwt_secret():
    """ป้องกันการขึ้นระบบจริงด้วย JWT_SECRET_KEY ค่า default ที่เปิดเผยอยู่ใน source code (config.py)
    ถ้ายังไม่ได้ตั้งค่า environment variable JWT_SECRET_KEY เป็นค่าอื่น:
    - รันตรงด้วย python app.py (dev, __name__ == "__main__"): แจ้งเตือนดังๆ แต่ยอมให้รันต่อ ไม่ขัดขวางการพัฒนา/ทดสอบ
    - รันผ่าน gunicorn app:app (production, __name__ == "app" เพราะถูก import ไม่ได้รันตรง): หยุดการทำงานทันที (RuntimeError)
    """
    if settings.JWT_SECRET_KEY != INSECURE_DEFAULT_JWT_SECRET:
        return
    if __name__ == "__main__":
        print("=" * 78)
        print("คำเตือน: ยังไม่ได้ตั้งค่า JWT_SECRET_KEY (กำลังใช้ค่า default ที่ไม่ปลอดภัย)")
        print("ค่านี้ใช้ได้เฉพาะตอนพัฒนา/ทดสอบในเครื่องเท่านั้น ห้ามใช้ขึ้นระบบจริงเด็ดขาด")
        print("ก่อน deploy จริง ต้องตั้งค่า environment variable JWT_SECRET_KEY เป็นค่าสุ่มที่ปลอดภัย (ดูวิธีใน DEPLOY.md)")
        print("=" * 78)
    else:
        raise RuntimeError(
            "ห้ามขึ้นระบบจริงโดยไม่ตั้งค่า JWT_SECRET_KEY! "
            "กรุณาตั้งค่า environment variable JWT_SECRET_KEY เป็นค่าสุ่มที่ปลอดภัยก่อน deploy จริง (ดูวิธีใน DEPLOY.md)"
        )


# สร้างตารางฐานข้อมูลอัตโนมัติเมื่อ import โมดูลนี้ (ทั้งตอนรัน dev server และตอนรันผ่าน gunicorn)
init_db()
_guard_jwt_secret()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
