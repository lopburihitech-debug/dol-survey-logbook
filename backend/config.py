"""การตั้งค่าระบบส่วนกลาง อ่านจาก environment variables เพื่อให้ deploy image เดียวกันไปแต่ละสาขาได้โดยไม่ต้องแก้โค้ด"""
import os

# ค่า default นี้ใช้ได้เฉพาะตอนพัฒนา/ทดสอบในเครื่องเท่านั้น ห้ามใช้ตอนขึ้นระบบจริงเด็ดขาด
# เพราะเป็นค่าที่อยู่ใน source code นี้ตรงๆ ใครเห็นโค้ดก็เห็นค่านี้ -> ปลอม JWT token เป็นผู้ใช้คนไหนก็ได้ รวมถึง admin
# app.py จะตรวจสอบค่านี้ตอนเริ่มระบบ (ดู _guard_jwt_secret) และหยุดการทำงานถ้าใช้ค่านี้ตอนรันจริงผ่าน gunicorn
INSECURE_DEFAULT_JWT_SECRET = "CHANGE_ME_IN_PRODUCTION_dol_survey_logbook_secret"


class Settings:
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", INSECURE_DEFAULT_JWT_SECRET)
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    APP_NAME = "DOL Survey Logbook API"
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")


settings = Settings()
