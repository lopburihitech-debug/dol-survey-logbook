-- DOL Survey Logbook — Database Schema (SQLite, พอร์ตมาจาก ER Diagram ใน System Blueprint v2.0)
-- หมายเหตุ: ทุกตารางที่เกี่ยวกับงานรังวัดผูกกับ office_id เพื่อรองรับหลายสาขาบนฐานข้อมูลเดียว (multi-office)
-- id ทุกตารางเป็น TEXT (uuid4 hex) / วันเวลาเก็บเป็น ISO-8601 string / boolean เก็บเป็น INTEGER (0/1)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS offices (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    province TEXT NOT NULL,
    district TEXT,
    address TEXT,
    lat REAL,             -- พิกัดที่ตั้งสำนักงาน — ใช้เป็นจุดอ้างอิงคำนวณระยะทางในหน้าแผนที่ช่างรังวัด (field-map.html)
    lng REAL,              -- NULL = ยังไม่ได้กรอก (แก้ไขได้ที่หน้าจัดการสำนักงาน offices.html)
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    role TEXT NOT NULL,               -- system_admin / administrator / supervisor / surveyor / citizen
    office_id TEXT REFERENCES offices(id),
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    mfa_secret TEXT,                  -- Base32 TOTP secret — มีค่าตอนตั้งค่าไว้แล้วเท่านั้น (NULL = ยังไม่ตั้ง/ปิดใช้งาน)
    is_active INTEGER NOT NULL DEFAULT 1,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,  -- นับจำนวนครั้งที่ใส่รหัสผ่านผิดติดต่อกัน (reset เป็น 0 ทุกครั้งที่ล็อกอินสำเร็จ)
    lockout_until TEXT,                                -- ล็อกบัญชีชั่วคราวถึงเวลานี้ (ISO-8601) — NULL = ไม่ได้ถูกล็อก
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- รหัสสำรองใช้ครั้งเดียวสำหรับ 2FA (กรณีทำอุปกรณ์ยืนยันตัวตนหาย) — เก็บเฉพาะแฮช ไม่เก็บ plaintext
CREATE TABLE IF NOT EXISTS mfa_backup_codes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    code_hash TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

-- อุปกรณ์ (ลายนิ้วมือ/ใบหน้า/PIN เครื่อง) ที่ลงทะเบียนไว้สำหรับล็อกอินแบบ WebAuthn — เก็บเฉพาะกุญแจสาธารณะ
-- (public key) ไม่ใช่ข้อมูลไบโอเมตริกซ์ใดๆ ทั้งสิ้น (ดู services/webauthn.py หัวไฟล์สำหรับรายละเอียด)
CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    credential_id TEXT UNIQUE NOT NULL,     -- base64url ของ credential ID ที่อุปกรณ์สร้างให้ตอนลงทะเบียน
    public_key_json TEXT NOT NULL,          -- JSON ของกุญแจสาธารณะ {"kty":"EC2"/"RSA", ...} สำหรับตรวจลายเซ็นภายหลัง
    sign_count INTEGER NOT NULL DEFAULT 0,  -- ตัวนับป้องกันการคัดลอกอุปกรณ์ (replay) ต้องเพิ่มขึ้นทุกครั้งที่ใช้
    device_label TEXT,                      -- ชื่อที่ผู้ใช้ตั้งเอง เช่น "โน้ตบุ๊คที่ทำงาน" (ไว้แยกเวลามีหลายเครื่อง)
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

-- challenge ชั่วคราวระหว่างขั้นตอน "ขอ options" กับ "ส่งผลลัพธ์กลับมาตรวจ" ของ WebAuthn — ต้องเก็บใน DB ไม่ใช่
-- ตัวแปรในหน่วยความจำ เพราะ gunicorn รันหลาย worker process (ดู entrypoint.sh) คำขอสองจังหวะอาจไปตกคนละ worker
CREATE TABLE IF NOT EXISTS webauthn_challenges (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    purpose TEXT NOT NULL,   -- 'register' หรือ 'authenticate'
    challenge TEXT NOT NULL, -- base64url
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS surveyors (
    id TEXT PRIMARY KEY,
    user_id TEXT UNIQUE NOT NULL REFERENCES users(id),
    employee_code TEXT UNIQUE NOT NULL,
    nickname TEXT,
    position TEXT,
    photo_url TEXT,
    office_id TEXT NOT NULL REFERENCES offices(id),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citizens (
    id TEXT PRIMARY KEY,
    national_id_hash TEXT UNIQUE NOT NULL,
    full_name_enc TEXT,
    address_enc TEXT,
    phone_enc TEXT,
    consent_pdpa_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS survey_types (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    target_days INTEGER NOT NULL DEFAULT 30,
    requires_announcement INTEGER NOT NULL DEFAULT 0,
    fee_amount REAL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS survey_cases (
    id TEXT PRIMARY KEY,
    case_code TEXT NOT NULL,                  -- เลข รว.12 (unique เฉพาะภายในสำนักงานเดียวกัน — ดู idx_survey_cases_office_code
                                               -- ด้านล่าง เพราะแต่ละสำนักงานออกเลขของตัวเองแยกกัน อาจซ้ำกันข้ามสำนักงานได้)
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
    -- เช็คลิสต์ความคืบหน้าภาคสนาม (เพิ่มทีหลัง — ดู db.py migration ด้วยสำหรับฐานข้อมูลที่มีอยู่ก่อนแล้ว)
    survey_result TEXT,          -- ผลการรังวัด: DONE / NOT_DONE
    mapping_status TEXT,         -- ขึ้นรูป: DONE / NOT_DONE
    neighbor_status TEXT,        -- ข้างเคียง: COMPLETE / WAITING_AGENCY
    announcement_status TEXT,    -- ปิดประกาศ: POSTED / WAITING
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_survey_cases_status ON survey_cases(status);
CREATE INDEX IF NOT EXISTS idx_survey_cases_office ON survey_cases(office_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_survey_cases_office_code ON survey_cases(office_id, case_code);

CREATE TABLE IF NOT EXISTS parcels (
    id TEXT PRIMARY KEY,
    case_id TEXT UNIQUE NOT NULL REFERENCES survey_cases(id),
    deed_no TEXT,
    parcel_no TEXT,
    survey_sheet_no TEXT,      -- ระวาง
    sub_district TEXT,         -- ตำบล
    district TEXT,             -- อำเภอ
    province TEXT,             -- จังหวัด
    area_rai INTEGER,
    area_ngan INTEGER,
    area_wa REAL,
    lat REAL,
    lng REAL,
    location_url TEXT,         -- ลิงก์แผนที่ (เช่น Google Maps) กดนำทางได้
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_assignments (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    surveyor_id TEXT NOT NULL REFERENCES surveyors(id),
    assigned_by TEXT NOT NULL REFERENCES users(id),
    assigned_at TEXT NOT NULL,
    unassigned_at TEXT,
    reason TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_case_assignments_case ON case_assignments(case_id);
CREATE INDEX IF NOT EXISTS idx_case_assignments_surveyor ON case_assignments(surveyor_id);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    appointment_start TEXT NOT NULL,
    appointment_end TEXT,
    location TEXT,
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_documents (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    document_type TEXT NOT NULL,
    file_url TEXT NOT NULL,
    geo_lat REAL,
    geo_lng REAL,
    taken_at TEXT,
    uploaded_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    -- ใช้เฉพาะ document_type = 'boundary_marker' (แผนที่หมุดหลักเขต): ลำดับหมุด + ป้ายชื่อหมุด
    sequence_no INTEGER,
    label TEXT,
    -- ใช้เฉพาะ document_type = 'boundary_marker' เช่นกัน: NULL/ว่าง = หมุดของแปลงหลัก, ค่าอื่น = ชื่อแปลงข้างเคียง
    -- ที่หมุดจุดนี้เป็นส่วนหนึ่งของขอบเขต (รองรับหลายแปลงข้างเคียงต่อเรื่องเดียว แยกด้วยชื่อกลุ่มนี้)
    marker_group TEXT
);

CREATE TABLE IF NOT EXISTS case_neighbors (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    name TEXT NOT NULL,
    contact TEXT,
    notify_status TEXT NOT NULL DEFAULT 'PENDING',
    notified_at TEXT,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS case_status_history (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    previous_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT REFERENCES users(id),
    reason TEXT,
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_status_history_case ON case_status_history(case_id);

CREATE TABLE IF NOT EXISTS case_reviews (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    reviewed_by TEXT NOT NULL REFERENCES users(id),
    review_result TEXT NOT NULL,   -- APPROVE / REJECT
    comments TEXT,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rework_requests (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    requested_by TEXT NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS complaints (
    id TEXT PRIMARY KEY,
    case_id TEXT REFERENCES survey_cases(id),
    citizen_contact TEXT NOT NULL,
    complaint_type TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    resolved_by TEXT REFERENCES users(id),
    resolved_at TEXT,
    reply_text TEXT,              -- คำตอบที่เจ้าหน้าที่/ช่างรังวัดพิมพ์ตอบกลับ ให้ประชาชนเห็นในหน้าติดตามงาน (track.html)
    replied_by TEXT REFERENCES users(id),
    replied_at TEXT,
    created_at TEXT NOT NULL
);

-- คะแนนความพึงพอใจจากประชาชน (หน้าน้อยยิ้ม 5 ระดับ ในหน้าติดตามงาน frontend/track.html) — เก็บได้ 1 คะแนนต่อ 1
-- เรื่อง (case_id UNIQUE) เปิดให้ให้คะแนนเฉพาะเมื่องานเสร็จสิ้นแล้ว (ดู CaseStatus.COMPLETED/CLOSED ใน
-- blueprints/public_track.py) — ให้ซ้ำได้ (แก้คะแนนเดิม) แต่ไม่สร้างแถวใหม่ซ้อน เผื่อกรณีกดผิดแล้วอยากแก้
CREATE TABLE IF NOT EXISTS case_satisfaction_ratings (
    id TEXT PRIMARY KEY,
    case_id TEXT UNIQUE NOT NULL REFERENCES survey_cases(id),
    rating INTEGER NOT NULL,      -- 1 (ไม่พอใจมาก) ถึง 5 (พอใจมาก)
    comment TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fees (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES survey_cases(id),
    fee_type TEXT NOT NULL,
    amount REAL NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'UNPAID',
    paid_at TEXT,
    receipt_no TEXT,
    payment_method TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public_holidays (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    name TEXT NOT NULL,
    office_id TEXT REFERENCES offices(id),
    UNIQUE(date, office_id)
);

CREATE TABLE IF NOT EXISTS pdpa_requests (
    id TEXT PRIMARY KEY,
    citizen_id TEXT REFERENCES citizens(id),
    contact TEXT NOT NULL,
    request_type TEXT NOT NULL,   -- ACCESS / CORRECT / DELETE
    status TEXT NOT NULL DEFAULT 'PENDING',
    processed_by TEXT REFERENCES users(id),
    processed_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    case_id TEXT REFERENCES survey_cases(id),
    channel TEXT NOT NULL,       -- SMS / LINE / EMAIL / IN_APP
    recipient TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    sent_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    action TEXT NOT NULL,        -- CREATE / UPDATE / DELETE / VIEW / LOGIN
    entity TEXT NOT NULL,
    entity_id TEXT,
    before_data TEXT,
    after_data TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equipment (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    serial_no TEXT,
    type TEXT,
    status TEXT NOT NULL DEFAULT 'AVAILABLE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equipment_assignments (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    surveyor_id TEXT NOT NULL REFERENCES surveyors(id),
    case_id TEXT REFERENCES survey_cases(id),
    assigned_at TEXT NOT NULL,
    returned_at TEXT
);
