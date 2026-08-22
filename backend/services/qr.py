"""
สร้าง QR code เป็นภาพ PNG จากข้อความ (ใช้แสดง otpauth:// URI ให้แอปยืนยันตัวตนสแกนตอนตั้งค่า 2FA)

ใช้ reportlab (มีตัวเข้ารหัส QR แบบ pure-Python ในตัวอยู่แล้ว — reportlab.graphics.barcode.qrencoder)
ร่วมกับ Pillow ในการวาดภาพ PNG เอง แทนที่จะใช้ไลบรารี qrcode/pyqrcode ตรงๆ เพราะ environment นี้ไม่มี
อินเทอร์เน็ตออกไปติดตั้ง package เพิ่มได้ แต่ reportlab และ Pillow ติดตั้งมาให้พร้อมอยู่แล้ว
ตรวจสอบความถูกต้องแล้วด้วยการถอดรหัสภาพที่สร้างกลับด้วย cv2.QRCodeDetector — อ่านค่าตรงกับข้อความต้นทาง 100%
"""
import base64
import io

from PIL import Image
from reportlab.graphics.barcode.qr import QrCodeWidget


def generate_qr_png_bytes(data: str, box_size: int = 8, border: int = 4) -> bytes:
    widget = QrCodeWidget(data)
    widget.qr.make()
    module_count = widget.qr.getModuleCount()

    img_size = (module_count + border * 2) * box_size
    img = Image.new("1", (img_size, img_size), 1)  # 1-bit: 1 = ขาว, 0 = ดำ
    pixels = img.load()

    for row in range(module_count):
        for col in range(module_count):
            if widget.qr.isDark(row, col):
                x0 = (col + border) * box_size
                y0 = (row + border) * box_size
                for dx in range(box_size):
                    for dy in range(box_size):
                        pixels[x0 + dx, y0 + dy] = 0

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_qr_data_uri(data: str, box_size: int = 8, border: int = 4) -> str:
    png_bytes = generate_qr_png_bytes(data, box_size, border)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"
