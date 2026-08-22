#!/bin/sh
# สร้างฐานข้อมูลและข้อมูลตั้งต้นถ้ายังไม่มี (ปลอดภัยที่จะรันซ้ำ — seed.py จะข้ามถ้ามีข้อมูลอยู่แล้ว) แล้วค่อยเริ่ม server
set -e
python seed.py
exec gunicorn -b 0.0.0.0:8000 -w 2 --timeout 60 app:app
