# คู่มือ Deploy ขึ้น Cloud จริง (สำหรับแชร์ลิงก์ Preview ให้คนอื่นดู)

> **หมายเหตุสำคัญ:** สภาพแวดล้อมที่ผมรันงานอยู่ตอนนี้ (cloud sandbox ของ Claude) ถูกจำกัดอินเทอร์เน็ตขาออกไว้เฉพาะบริการที่จำเป็น
> (PyPI, npm, GitHub) เท่านั้น **ผมเชื่อมต่อไปยัง Railway/Render/Fly.io จากในนี้โดยตรงไม่ได้** และไม่มีบัญชี Cloud ของคุณอยู่ในมือ
> ดังนั้นขั้นตอน deploy จริงต้องรันจาก**เครื่องของคุณเอง** (หรือถ้าคุณเปิดแอป Claude Desktop และเชื่อมต่อโฟลเดอร์ไว้ ผมสามารถรันคำสั่งเหล่านี้บนเครื่องคุณให้ได้โดยตรง — บอกผมได้เลยถ้าต้องการแบบนั้น)
>
> ระบบพร้อม deploy อยู่แล้ว (มี `Dockerfile` + `docker-compose.yml` ให้ครบ) เหลือแค่เลือกแพลตฟอร์มแล้วรันคำสั่งด้านล่าง

---

## ตัวเลือกที่ 1: Railway (แนะนำ — เร็วสุด ไม่ต้องพึ่ง GitHub)

Railway มี free trial ให้ทดลอง และรับ Dockerfile ได้ตรงๆ ผ่าน CLI โดยไม่ต้อง push ขึ้น GitHub ก่อน

```bash
# 1) ติดตั้ง Railway CLI (รันบนเครื่องคุณ)
npm install -g @railway/cli
# หรือถ้าไม่มี Node.js: curl -fsSL https://railway.app/install.sh | sh

# 2) ล็อกอิน (จะเปิดเบราว์เซอร์ให้ยืนยันตัวตน)
railway login

# 3) เข้าไปที่โฟลเดอร์โปรเจกต์ที่แตกไฟล์ zip แล้ว
cd dol-survey-logbook

# 4) สร้างโปรเจกต์ใหม่บน Railway
railway init

# 5) ตั้งค่า Environment Variables ที่จำเป็น (สำคัญ! อย่าใช้ค่า default ตอน deploy จริง)
railway variables set JWT_SECRET_KEY="$(openssl rand -hex 32)"
railway variables set DATABASE_PATH="/app/backend/data/dol_survey_logbook.db"

# 6) เพิ่ม Volume ถาวรสำหรับเก็บไฟล์ฐานข้อมูล SQLite (ไม่งั้นข้อมูลหายทุกครั้งที่ redeploy)
railway volume add --mount-path /app/backend/data

# 7) Deploy!
railway up

# 8) ขอโดเมนสาธารณะ (จะได้ลิงก์แบบ https://xxxx.up.railway.app)
railway domain
```

หลังจากนี้ Railway จะ build จาก `Dockerfile` อัตโนมัติ แล้วให้ลิงก์สาธารณะมาแชร์ได้ทันที ระบบจะรัน `seed.py` ให้เองรอบแรกตาม `entrypoint.sh`

---

## ตัวเลือกที่ 2: Render (ทางเลือก — ต้องมี GitHub repo)

1. Push โค้ดขึ้น GitHub repo ของคุณเอง (ไม่ต้อง public ก็ได้):
   ```bash
   cd dol-survey-logbook
   git init && git add . && git commit -m "DOL Survey Logbook"
   git branch -M main
   git remote add origin https://github.com/<your-username>/dol-survey-logbook.git
   git push -u origin main
   ```
2. ไปที่ [render.com](https://render.com) → **New +** → **Web Service** → เชื่อมต่อ GitHub repo ที่เพิ่ง push
3. Render จะเจอ `Dockerfile` เองอัตโนมัติ เลือก **Docker** เป็น environment
4. ไปที่แท็บ **Disks** → เพิ่ม Persistent Disk mount ที่ `/app/backend/data` (สำหรับเก็บ SQLite ไม่ให้หายตอน redeploy)
5. ไปที่แท็บ **Environment** → เพิ่มตัวแปร `JWT_SECRET_KEY` (ค่าสุ่มยาวๆ), `DATABASE_PATH=/app/backend/data/dol_survey_logbook.db`
6. กด **Create Web Service** — รอ build เสร็จจะได้ลิงก์แบบ `https://xxxx.onrender.com`

---

## ตัวเลือกที่ 3: Fly.io (ทางเลือก — เหมาะกับสาย CLI)

```bash
curl -L https://fly.io/install.sh | sh
cd dol-survey-logbook
fly launch --no-deploy          # ตอบคำถามตั้งค่าเบื้องต้น จะสร้าง fly.toml ให้
fly volumes create dol_data --size 1   # พื้นที่เก็บ SQLite ถาวร
fly secrets set JWT_SECRET_KEY="$(openssl rand -hex 32)"
fly deploy
fly open   # เปิดลิงก์สาธารณะที่ได้
```

---

---

## ต่อฐานข้อมูลออนไลน์ (PostgreSQL)

ระบบรองรับการต่อฐานข้อมูล PostgreSQL ออนไลน์ในตัวอยู่แล้ว (ดูรายละเอียดโค้ดใน README หัวข้อ "ต่อฐานข้อมูล
PostgreSQL ออนไลน์") — สิ่งที่ต้องทำคือ **สมัครใช้บริการฐานข้อมูล 1 อย่าง แล้วเอา connection string มาตั้งเป็น
`DATABASE_URL`** เท่านั้น เลือกได้ตามความสะดวก:

### ตัวเลือก A: Railway Postgres (ง่ายสุดถ้า deploy บน Railway อยู่แล้ว)

```bash
# ในโปรเจกต์ Railway เดียวกับที่ deploy เว็บแอปไว้
railway add --database postgres
# Railway จะสร้างตัวแปร DATABASE_URL ให้ในโปรเจกต์อัตโนมัติ (เชื่อมกับ service เว็บแอปให้เองถ้าอยู่โปรเจกต์เดียวกัน)
railway up   # deploy ใหม่อีกครั้งให้ระบบอ่านค่า DATABASE_URL ที่เพิ่งเพิ่ม
```

### ตัวเลือก B: Supabase (ฟรี, มี dashboard ดูข้อมูลสวยงาม, เหมาะถ้าอยากดูข้อมูลผ่านหน้าเว็บด้วย)

1. สมัคร/ล็อกอินที่ [supabase.com](https://supabase.com) → **New Project**
2. ตั้งรหัสผ่านฐานข้อมูล (จำไว้ให้ดี) รอสร้างโปรเจกต์เสร็จ (~2 นาที)
3. ไปที่ **Project Settings → Database → Connection string** เลือกโหมด **URI** (แนะนำใช้ตัวเลือก "Session pooler" หรือ "Transaction pooler" ถ้ามี เพื่อรองรับการเชื่อมต่อพร้อมกันหลาย container ได้ดีกว่า)
4. คัดลอก connection string มาตั้งเป็น `DATABASE_URL` ในแพลตฟอร์มที่ deploy เว็บแอปไว้ (Railway: `railway variables set DATABASE_URL="..."`, Render: ใส่ในแท็บ Environment)

### ตัวเลือก C: Render Postgres / Neon

คล้ายกัน — สร้างฐานข้อมูลผ่านหน้าเว็บของผู้ให้บริการ คัดลอก connection string (เริ่มต้นด้วย `postgres://` หรือ `postgresql://`) มาตั้งเป็น `DATABASE_URL`

### ทดสอบการเชื่อมต่อก่อนใช้งานจริง (สำคัญ!)

โค้ดฝั่ง PostgreSQL เขียนตามมาตรฐาน psycopg2 และตรวจ syntax แล้ว แต่ยังไม่เคยเชื่อมต่อฐานข้อมูลจริงแบบ
end-to-end เพราะพัฒนาในสภาพแวดล้อมที่ไม่มีอินเทอร์เน็ตออกไปยัง Postgres provider ได้เลย ก่อนใช้งานจริงให้รัน:

```bash
cd backend
pip install -r requirements.txt
pip install -r requirements-postgres.txt   # ติดตั้งเพิ่มเฉพาะตอนจะต่อ PostgreSQL (มี psycopg2-binary)
export DATABASE_URL="postgres://..."   # connection string ที่ได้จากขั้นตอนข้างต้น
python check_db_connection.py
```

ถ้าเห็น `✅ เชื่อมต่อสำเร็จ` แปลว่าใช้งานได้แล้ว รัน `python seed.py` ต่อเพื่อสร้าง schema + ข้อมูลตั้งต้น (ถ้ายังไม่มี) แล้ว deploy เว็บแอปโดยตั้ง `DATABASE_URL` เดียวกันนี้ในแพลตฟอร์มที่ใช้ deploy ได้เลย ถ้าเจอ error ให้ส่งข้อความ error กลับมาบอกผม จะช่วยตรวจให้

---

## สำรองข้อมูล (Backup)

ถ้าใช้ SQLite (ไม่ได้ตั้งค่า `DATABASE_URL`) ควรตั้งให้สำรองข้อมูลอัตโนมัติเป็นระยะ เพราะข้อมูลทั้งหมดอยู่ในไฟล์เดียว
บน volume ที่ mount ไว้ — มีสคริปต์ `backend/backup_db.py` เตรียมไว้ให้แล้ว (ใช้ sqlite3 backup API ปลอดภัยแม้ระบบ
กำลังทำงาน/มีคนใช้อยู่พอดี):

```bash
cd backend
python backup_db.py            # สำรองครั้งเดียว ไปที่ backend/data/backups/ (เก็บย้อนหลัง 14 ไฟล์ล่าสุด)
python backup_db.py --keep 30  # ปรับจำนวนไฟล์ย้อนหลังที่จะเก็บไว้
```

ตั้งให้รันอัตโนมัติทุกวันตามแพลตฟอร์มที่ deploy:

- **Railway**: เพิ่มบริการ (service) ใหม่แบบ "Cron Job" ในโปรเจกต์เดียวกัน รันคำสั่ง `cd backend && python backup_db.py`
- **Render**: ใช้ฟีเจอร์ "Cron Jobs" ชี้มาที่คำสั่งเดียวกัน
- **Fly.io / self-host**: เพิ่มใน crontab เช่น `0 2 * * * cd /app/backend && python backup_db.py`

ไฟล์ backup จะอยู่ใน `backend/data/backups/` ซึ่งอยู่บน volume ถาวรเดียวกับฐานข้อมูลหลักโดยอัตโนมัติ แต่เพื่อความ
ปลอดภัยสูงสุด ควรดาวน์โหลดไฟล์ backup ออกไปเก็บไว้อีกที่นอกเหนือจาก volume นี้เป็นระยะด้วย (เช่นอัปโหลดขึ้น
cloud storage แยกต่างหาก) เผื่อกรณี volume ทั้งก้อนมีปัญหา

ถ้าต่อ **PostgreSQL ออนไลน์** (ตั้งค่า `DATABASE_URL`) อยู่แล้ว ให้ใช้เครื่องมือ backup ของผู้ให้บริการแทน — ส่วนใหญ่
(Railway/Render/Supabase/Neon) มี automated backup ให้อยู่แล้วในตัว (บางเจ้าอาจต้องเปิดใช้/อัปเกรดแผนก่อน)

---

## ⚠️ ก่อนแชร์ลิงก์ preview ให้คนอื่นดู

- **เปลี่ยนรหัสผ่านบัญชีทดสอบทันที** (`admin`, `director`, `supervisor1`, `surveyor1`, `surveyor2`) เพราะรหัสในคู่มืออยู่ใน README แบบ public
- ตั้งค่า `JWT_SECRET_KEY` เป็นค่าสุ่มใหม่เสมอ (ห้ามใช้ค่า default ใน `.env.example`)
- ถ้าไม่อยากให้คนแปลกหน้าเข้าถึง อาจเปิดใช้ Basic Auth ชั้นนอก (Railway/Render มี "Preview protection" หรือใช้ Cloudflare Access ฟรีคลุมอีกชั้นได้)
- ข้อมูลที่กรอกใน preview จะเป็นข้อมูลทดสอบเท่านั้น — อย่าใส่ข้อมูลจริงของประชาชน จนกว่าจะผ่านการตรวจสอบความปลอดภัยแล้ว (ดูหัวข้อ Security/PDPA ใน Blueprint)

---

## ถ้าอยากให้ผมรันขั้นตอนนี้ให้เลย

เปิดแอป **Claude Desktop** บนเครื่องของคุณและเชื่อมต่อโฟลเดอร์ที่ต้องการ (เครื่องคุณมีอินเทอร์เน็ตปกติ ไม่ถูกจำกัดแบบ sandbox นี้)
แล้วบอกผมว่าพร้อมแล้ว — ผมจะรันคำสั่ง `railway login` / `railway up` (หรือแพลตฟอร์มที่คุณเลือก) ให้บนเครื่องคุณโดยตรง แล้วส่งลิงก์ preview กลับมาให้เลย
