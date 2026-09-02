/* ตัวช่วยเรียก API กลาง — ใช้ same-origin เสมอ (frontend ถูก serve จาก Flask เดียวกับ API จึงไม่มีปัญหา CORS) */
const API_BASE = "/api/v1";

function getTokens() {
  return {
    access: localStorage.getItem("dol_access_token"),
    refresh: localStorage.getItem("dol_refresh_token"),
  };
}

function setTokens(access, refresh) {
  localStorage.setItem("dol_access_token", access);
  if (refresh) localStorage.setItem("dol_refresh_token", refresh);
}

function clearTokens() {
  localStorage.removeItem("dol_access_token");
  localStorage.removeItem("dol_refresh_token");
  localStorage.removeItem("dol_user");
}

function requireAuth() {
  const { access } = getTokens();
  if (!access) {
    window.location.href = "/login.html";
  }
}

async function apiFetch(path, options = {}) {
  const { access } = getTokens();
  const headers = Object.assign(
    { "Content-Type": "application/json" },
    options.headers || {}
  );
  if (access) headers["Authorization"] = "Bearer " + access;

  let res = await fetch(API_BASE + path, Object.assign({}, options, { headers }));

  if (res.status === 401 && getTokens().refresh) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      headers["Authorization"] = "Bearer " + getTokens().access;
      res = await fetch(API_BASE + path, Object.assign({}, options, { headers }));
    }
  }

  if (res.status === 401) {
    clearTokens();
    window.location.href = "/login.html";
    return null;
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = (data.error && data.error.message) || "เกิดข้อผิดพลาด";
    throw new Error(message);
  }
  return data;
}

async function apiUpload(path, file, extraFields) {
  const { access } = getTokens();
  const formData = new FormData();
  formData.append("file", file);
  if (extraFields) {
    Object.entries(extraFields).forEach(([k, v]) => formData.append(k, v));
  }
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: access ? { Authorization: "Bearer " + access } : {},
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = (data.error && data.error.message) || "เกิดข้อผิดพลาดในการอัปโหลด";
    throw new Error(message);
  }
  return data;
}

// แปลงข้อมูลไปมาระหว่าง base64url (รูปแบบที่ backend/WebAuthn ใช้ส่งข้อมูลเป็น string) กับ ArrayBuffer
// (รูปแบบที่ navigator.credentials.create()/.get() ของเบราว์เซอร์ต้องการ) — ใช้ร่วมกันทั้ง login.html
// (ยืนยันตัวตน) และ account.html (ลงทะเบียนอุปกรณ์ใหม่)
function b64urlToBuffer(b64url) {
  const padded = b64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64url.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function bufferToB64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function fetchAuthedBlobUrl(absolutePath) {
  const { access } = getTokens();
  try {
    const res = await fetch(absolutePath, { headers: access ? { Authorization: "Bearer " + access } : {} });
    if (!res.ok) return null;
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  } catch (e) {
    return null;
  }
}

async function tryRefreshToken() {
  const { refresh } = getTokens();
  if (!refresh) return false;
  try {
    const res = await fetch(API_BASE + "/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    setTokens(data.access_token, data.refresh_token);
    return true;
  } catch (e) {
    return false;
  }
}

async function loadCurrentUser() {
  const cached = localStorage.getItem("dol_user");
  if (cached) return JSON.parse(cached);
  const user = await apiFetch("/auth/me");
  if (user) localStorage.setItem("dol_user", JSON.stringify(user));
  return user;
}

function logout() {
  clearTokens();
  window.location.href = "/login.html";
}

const ROLE_LABELS = {
  system_admin: "ผู้ดูแลระบบ",
  administrator: "ผู้บริหาร",
  province_admin: "ผู้ดูแลระดับจังหวัด",
  supervisor: "หัวหน้าช่างรังวัด",
  branch_admin: "เจ้าพนักงานที่ดินสาขา",
  // ผู้ดูแลระดับสาขา — บทบาทแยกต่างหากจาก branch_admin ข้างบน มีสิทธิ์เดียวคือจัดการบัญชีผู้ใช้ที่เป็น "ช่างรังวัด"
  // ในสำนักงานตัวเอง (หน้า "จัดการผู้ใช้งาน") ไม่เกี่ยวกับงานรังวัด/เคสใดๆ เลย — ดู constants.py ฝั่ง backend
  branch_user_admin: "ผู้ดูแลระดับสาขา",
  surveyor: "ช่างรังวัด",
  citizen: "ประชาชน",
};

const STATUS_LABELS = {
  RECEIVED: "รับเรื่องแล้ว",
  WAITING_ASSIGNMENT: "รอมอบหมาย",
  ASSIGNED: "มอบหมายแล้ว",
  APPOINTED: "รอการรังวัด",
  SURVEY_DONE: "รังวัดเสร็จแล้ว",
  IN_SURVEY: "กำลังปฏิบัติงาน",
  WAITING_DOCUMENT: "รอเอกสาร",
  WAITING_ANNOUNCEMENT: "รอปิดประกาศ",
  PENDING_REVIEW: "รอตรวจ QC",
  PENDING_APPROVAL: "รออนุมัติถอนจ่าย",
  COMPLETED: "เสร็จสิ้น",
  CLOSED: "ถอนจ่ายแล้ว",
  POSTPONED: "เลื่อนรังวัด",
  CANCELLED: "ยกเลิก",
  ON_HOLD: "พักงาน",
  REWORK_REQUIRED: "ต้องแก้ไข/รังวัดซ้ำ",
  SURVEY_SKIPPED: "งดรังวัด",
  RE_APPOINTMENT_NEEDED: "นัดตรวจสอบใหม่",
};

// ป้ายกำกับสำหรับตัวเลือกใน dropdown "เปลี่ยนสถานะ" ของหน้าดำเนินการ (แยกจาก STATUS_LABELS ซึ่งใช้กับ badge ทั่วระบบ
// เพราะบางสถานะอยากให้ปุ่มดำเนินการสื่อ "การกระทำ" ในขณะที่ badge สื่อ "สถานะปัจจุบัน")
// หมายเหตุ: CLOSED ("ถอนจ่ายแล้ว") เลือกได้ตรงจากดรอปดาวน์นี้เลย แต่ backend จำกัดสิทธิ์เฉพาะผู้บริหาร/ผู้ดูแลระบบ
const ACTION_OPTION_LABELS = {
  APPOINTED: "รอการรังวัด",
  SURVEY_DONE: "รังวัดเสร็จแล้ว",
  CLOSED: "ถอนจ่ายแล้ว",
  SURVEY_SKIPPED: "งดรังวัด",
  RE_APPOINTMENT_NEEDED: "นัดตรวจสอบใหม่",
  POSTPONED: "เลื่อนรังวัด",
  CANCELLED: "ยกเลิก",
  // ตัวเลือกนี้โผล่เฉพาะตอนสถานะปัจจุบันคือ "ถอนจ่ายแล้ว" (ดู TRANSITIONS.CLOSED ใน case.html) — ใช้เปิดเรื่องกลับมา
  // แก้ไข/รังวัดซ้ำกรณีพบว่างานต้องซ่อมหรือแก้ไขเพิ่มเติมหลังปิดเรื่องไปแล้ว จำกัดสิทธิ์เฉพาะผู้บริหาร/ผู้ดูแลระบบ
  REWORK_REQUIRED: "เปิดกลับมาแก้ไข/รังวัดซ้ำ",
};

function statusBadge(status) {
  const label = STATUS_LABELS[status] || status;
  return `<span class="badge status-${status}">${label}</span>`;
}

function fmtDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("th-TH", { year: "numeric", month: "short", day: "numeric" }) +
    " " + d.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
}

// เหมือน fmtDate() แต่ตัดเวลาออก — ใช้กับคอลัมน์ที่เป็น "วันที่" ล้วนๆ (ไม่ใช่ timestamp ของเหตุการณ์) เช่น
// วันรับเรื่อง/วันนัดรังวัดในตารางรายการงาน ที่เวลา 00:00 กำกับไว้ไม่มีความหมายอะไรให้ผู้ใช้เห็น
function fmtDateOnly(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("th-TH", { year: "numeric", month: "short", day: "numeric" });
}

// ไอคอนเส้น (stroke=currentColor) ชุดเดียวกับที่ใช้ในหน้าอื่นๆ ของระบบ — วาดเองทั้งหมด ไม่พึ่งไอคอนฟอนต์/ไลบรารีภายนอก
const NAV_ICONS = {
  dashboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>`,
  cases: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M9 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3"/><path d="M9 12h6M9 16h6"/></svg>`,
  calendar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>`,
  people: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  manage: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94Z"/></svg>`,
  system: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/></svg>`,
  account: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.5-7 8-7s8 3 8 7"/></svg>`,
  summary: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 16v-4M12 16V8M17 16v-7"/></svg>`,
  report: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>`,
  map: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.94 11a8.94 8.94 0 1 0-17.88 0c0 5.02 6.87 10.16 8.4 11.24a.9.9 0 0 0 1.08 0c1.53-1.08 8.4-6.22 8.4-11.24Z"/><circle cx="12" cy="11" r="3"/></svg>`,
  logout: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>`,
  chevron: `<svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>`,
};

// สถานะที่ถือว่า "จบงานแล้ว" ไม่นับเป็นงานค้าง — ถอนจ่ายแล้ว/ยกเลิก/งดรังวัด (ตามที่ผู้ใช้ระบบยืนยัน ไม่รวม
// COMPLETED เพราะไม่มีเส้นทางไหนในระบบตั้งสถานะนี้จริง) ต้องตรงกับ CaseStatus.CLOSED_SET ฝั่ง backend
// (constants.py) และ CLOSED_SET ในหน้า my-work.html เพื่อให้ตัวเลขป้ายบนเมนูตรงกับที่แสดงในหน้านั้นเป๊ะๆ
const NAV_CLOSED_SET = new Set(["CLOSED", "CANCELLED", "SURVEY_SKIPPED"]);

function navLinkHtml(item, activePage) {
  const active = item.key === activePage;
  const badgeHtml = item.badge ? `<span class="nav-badge">${item.badge}</span>` : "";
  return `<a href="${item.href}" class="${active ? "active" : ""}">${item.icon || ""}<span>${item.label}</span>${badgeHtml}</a>`;
}

function navDropdownHtml(label, icon, items, activePage) {
  const hasActive = items.some((i) => i.key === activePage);
  const itemsHtml = items
    .map((i) => `<a href="${i.href}" class="${i.key === activePage ? "active" : ""}">${i.label}</a>`)
    .join("");
  return `
    <details class="nav-dropdown">
      <summary class="${hasActive ? "active" : ""}">${icon || ""}<span>${label}</span>${NAV_ICONS.chevron}</summary>
      <div class="dropdown-menu">${itemsHtml}</div>
    </details>`;
}

async function renderTopbar(activePage) {
  const user = await loadCurrentUser();
  if (!user) return;

  // รายการหลัก — งานที่ใช้บ่อยที่สุด วางไว้เป็นลิงก์เดี่ยวเห็นตลอดเวลา
  // ช่างรังวัดไม่มีประโยชน์กับ Dashboard ภาพรวมทั้งระบบ (ไม่มีจังหวัด/สาขาให้เทียบ) จึงไม่แสดงลิงก์นี้ให้เห็น —
  // หน้าแรกหลังล็อกอินของช่างรังวัดคือหน้าสรุปผลงานของตัวเอง (surveyor-profile.html) แทน ดู dashboard.html
  // ซึ่งจะ redirect ไปหน้านั้นให้อัตโนมัติถ้าช่างรังวัดหลงเข้ามาที่ /dashboard.html โดยตรง
  const primary = [];
  const manageItems = [];

  // ผู้ดูแลระดับสาขา (branch_user_admin) ไม่เกี่ยวกับงานรังวัด/เคสใดๆ เลย — มีสิทธิ์เดียวคือหน้า "จัดการผู้ใช้งาน"
  // (จัดการเฉพาะบัญชีช่างรังวัดในสำนักงานตัวเอง ดู constants.py/users.py ฝั่ง backend) จึงให้เมนูมีแค่ลิงก์เดียว
  // ไม่ต้องสร้าง Dashboard/งานรังวัด/ปฏิทิน/รายงาน ฯลฯ ให้เลย (กดไปก็เจอ 403 จาก backend อยู่ดีเพราะไม่มีสิทธิ์)
  if (user.role === "branch_user_admin") {
    primary.push({ href: "/users.html", label: "จัดการผู้ใช้งาน", key: "users", icon: NAV_ICONS.people });
  } else {
    // ลิงก์ "สรุปผล" (หน้าสรุปผลงาน/KPI ของตัวเอง) เฉพาะช่างรังวัด — วางไว้เป็นรายการแรกสุดของเมนูตามที่ต้องการ
    // ต้องหา record ช่างรังวัดที่ตรงกับบัญชีนี้ก่อน (endpoint เดียวกับที่ dashboard.html ใช้ตอน redirect)
    // ถ้าไม่พบ (กรณีผิดปกติ) ก็ไม่ต้องแสดงลิงก์นี้เลย — ในจังหวะเดียวกันนี้ดึงรายการงานของตัวเองมานับ "งานค้าง"
    // ไว้แปะเป็นตัวเลขที่ลิงก์ "งานของฉัน" ด้วยเลย (คนละหน้าก็ยังเห็นตัวเลขนี้ได้ ไม่ต้องรอเข้าหน้างานของฉันก่อน)
    let myPendingCount = 0;
    if (user.role === "surveyor") {
      const [surveyors, myCases] = await Promise.all([
        apiFetch("/surveyors").then((r) => r || []),
        apiFetch("/survey-cases").then((r) => r || []),
      ]);
      const mine = surveyors.find((s) => s.user_id === user.id);
      if (mine) {
        primary.push({ href: "/surveyor-profile.html?id=" + mine.id, label: "สรุปผล", key: "surveyor-profile", icon: NAV_ICONS.summary });
      }
      myPendingCount = myCases.filter((c) => !NAV_CLOSED_SET.has(c.status)).length;
    }
    if (user.role !== "surveyor") {
      primary.push({ href: "/dashboard.html", label: "Dashboard", key: "dashboard", icon: NAV_ICONS.dashboard });
    }
    if (user.role === "surveyor") {
      primary.push({ href: "/my-work.html", label: "งานของฉัน", key: "my-work", icon: NAV_ICONS.cases, badge: myPendingCount || null });
    } else {
      primary.push({ href: "/cases.html", label: "งานรังวัด", key: "cases", icon: NAV_ICONS.cases });
    }
    primary.push({ href: "/calendar.html", label: "ปฏิทินนัดรังวัด", key: "calendar", icon: NAV_ICONS.calendar });
    // "แผนที่ช่างรังวัด" — ดูตำแหน่งงานที่ช่างรังวัดแต่ละคนออกไปตามวันนัด บนแผนที่ พร้อมระยะทางจากสำนักงาน บทบาทเชิง
    // บริหาร/หัวหน้าเห็นตำแหน่งของช่างรังวัดทุกคนในขอบเขตตน ส่วนช่างรังวัดเองก็ดูได้เช่นกัน (ตามที่ผู้ใช้ระบบขอ) แต่เห็น
    // เฉพาะตำแหน่งเรื่องที่ตนรับผิดชอบเท่านั้น — field-map.html จำกัดขอบเขตให้อัตโนมัติผ่าน scope_case_filter() ฝั่ง
    // backend (เหมือนปฏิทินนัดรังวัดที่ช่างรังวัดดูตารางนัดของตัวเองได้ปกติอยู่แล้ว)
    if (["system_admin", "administrator", "province_admin", "supervisor", "branch_admin", "surveyor"].includes(user.role)) {
      primary.push({ href: "/field-map.html", label: "แผนที่ช่างรังวัด", key: "field-map", icon: NAV_ICONS.map });
    }
    // "รายงาน" — พิมพ์รายงานสรุปงานรังวัดตามขอบเขตสิทธิ์ของผู้ใช้แต่ละคน (ทุกบทบาทที่ล็อกอินผ่านเมนูนี้เห็นได้
    // เนื้อหาในหน้าจะถูกจำกัดขอบเขตอัตโนมัติผ่าน scope_case_filter()/dashboard/by-office ฝั่ง backend อยู่แล้ว)
    primary.push({ href: "/report.html", label: "รายงาน", key: "report", icon: NAV_ICONS.report });
    if (["system_admin", "administrator", "province_admin", "supervisor", "branch_admin"].includes(user.role)) {
      primary.push({ href: "/surveyors.html", label: "ช่างรังวัด", key: "surveyors", icon: NAV_ICONS.people });
    }

    // งานเชิงจัดการ + งานดูแลระบบระดับสูงสุด — รวมไว้ในเมนูย่อยเดียว "จัดการระบบ" ไม่ให้แถวหลักรกเกินไป (เดิมแยกเป็น
    // 2 เมนูย่อย "จัดการ"/"ระบบ" — รวมเป็นเมนูเดียวตามที่ผู้ใช้ขอ)
    // นำเข้างาน (bulk import): จำกัดเฉพาะบทบาทที่สร้างเรื่องได้ ไม่รวม branch_admin (สร้างเรื่องใหม่ไม่ได้)
    // จัดการช่างรังวัด: เจ้าพนักงานที่ดินสาขาก็เพิ่ม/แก้ไขบัญชีช่างรังวัดในสาขาตนเองได้ด้วย (ดู surveyors.py)
    // ผู้ใช้งาน: system_admin หรือผู้ดูแลระดับสาขา (ขอบเขตแคบกว่ามาก — ดูเงื่อนไข role==="branch_user_admin" ด้านบน
    // ที่ให้ลิงก์นี้เป็นรายการหลักไปแล้วแทน ไม่ต้องซ้ำในนี้อีก) / สำนักงาน: เฉพาะ system_admin เท่านั้น
    if (["system_admin", "administrator", "province_admin"].includes(user.role)) {
      manageItems.push({ href: "/cases-import.html", label: "นำเข้างาน", key: "cases-import" });
    }
    if (["system_admin", "administrator", "province_admin", "branch_admin"].includes(user.role)) {
      manageItems.push({ href: "/surveyors-manage.html", label: "จัดการช่างรังวัด", key: "surveyors-manage" });
    }
    if (user.role === "system_admin") {
      manageItems.push({ href: "/users.html", label: "ผู้ใช้งาน", key: "users" });
      manageItems.push({ href: "/offices.html", label: "สำนักงาน", key: "offices" });
    }
  }

  let navHtml = primary.map((n) => navLinkHtml(n, activePage)).join("");
  if (manageItems.length) navHtml += navDropdownHtml("จัดการระบบ", NAV_ICONS.manage, manageItems, activePage);

  const topbar = document.getElementById("topbar");
  topbar.innerHTML = `
    <div class="topbar-inner">
      <div class="brand-group">
        <img src="/img/logo-96.png" alt="ตราสัญลักษณ์" class="brand-logo">
        <div class="brand">DOL Survey Logbook<small>สมุดช่างรังวัดออนไลน์</small></div>
      </div>
      <button class="nav-toggle" id="navToggle" aria-label="เปิดเมนู">&#9776;</button>
      <nav>${navHtml}</nav>
      <div class="user-box">
        <details class="user-menu">
          <summary>
            <span class="user-avatar">${(user.full_name || "?").charAt(0)}</span>
            <span class="user-meta">
              <span class="user-name">${user.full_name}</span>
              <span class="badge">${ROLE_LABELS[user.role] || user.role}</span>
            </span>
            ${NAV_ICONS.chevron}
          </summary>
          <div class="dropdown-menu">
            <a href="/account.html" class="${activePage === "account" ? "active" : ""}">${NAV_ICONS.account}<span>บัญชีของฉัน</span></a>
            <button type="button" onclick="logout()">${NAV_ICONS.logout}<span>ออกจากระบบ</span></button>
          </div>
        </details>
      </div>
    </div>
  `;

  const toggle = document.getElementById("navToggle");
  if (toggle) {
    toggle.addEventListener("click", () => topbar.classList.toggle("nav-open"));
  }

  // เปิดได้ทีละเมนูเดียว + ปิดเมื่อคลิกนอกเมนู (native <details> ไม่มีพฤติกรรมนี้ให้เองต้องเสริมเอง)
  const allDetails = topbar.querySelectorAll("details");
  allDetails.forEach((d) => {
    d.addEventListener("toggle", () => {
      if (d.open) allDetails.forEach((other) => { if (other !== d) other.open = false; });
    });
  });
  document.addEventListener("click", (e) => {
    allDetails.forEach((d) => {
      if (d.open && !d.contains(e.target)) d.open = false;
    });
  });

  renderStaffChatFab();
  renderNotificationBell(user);

  return user;
}

// ปุ่มลอย "แชทบอทเจ้าหน้าที่" — เชื่อมไปยังระบบแชทบอทของกรมที่ดินเองที่มีอยู่แล้ว (คนละระบบกับที่นี่ จึงแค่เปิดลิงก์
// ในแท็บใหม่ ไม่ได้ฝัง iframe หรือเรียก API ข้ามระบบ) ต่อท้าย renderTopbar() ด้านบนเสมอ จึงเห็นได้ทุกหน้าที่มี
// topbar (คือทุกหน้าที่ต้องล็อกอินก่อนถึงจะเข้าได้ — ดู requireAuth()/loadCurrentUser() ด้านบน) ไม่จำกัดบทบาท
// ไอคอนฝังเป็น data URI ในตัวไฟล์นี้เลย (ไม่พึ่งไฟล์ภาพ/อินเทอร์เน็ตภายนอก ตามหลักการเดิมของทั้งระบบ) โหลดครั้งเดียว
// แล้วเบราว์เซอร์แคชไฟล์ api.js นี้ไว้ใช้ได้ทุกหน้าโดยไม่ต้องโหลดซ้ำ
const STAFF_CHATBOT_ICON_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAANwAAADcCAYAAAAbWs+BAABuGklEQVR42u29eZwcV3Uv/j333qpep6dnRjPad9uSLK9Y2AZjLIXFgRACITYhZH1JCCQBfgkvLyGbpCxAkvdeAuQlIWQhCQEiBRI2B8IyIhjbgLzKi2Rr32afnt67q+re8/ujqrqre3qkkSwvOHX86c+0xjPdPd33W+ec7znne4DYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy222GKLLbbYYosttthiiy22F7oxMzGzZGbFzHLPnj0y8m/FzCJ+l2KL7RIBbZE/K2LgxfZsmHoB/k3EzIKINAD9wQ9+MPGmN73hVstStykhtwgls1prw2zOGGMeqNUqo0R0MACeDH4vtthiW4Snanm0j3zkI+mxsZPvnCtMH3TdOvc2zcW5aWdmevxzTz31xEsjnpHidzO22M4BtBAkO3fuVGfOHP/ZucL0QWbDxjhcrZZ0rVZyq9WiF95q1aJbqxbdeq3EzJor5YI5ffr4+2PQxRbbAhYQIK3c6/TpE2+em5t+kNkLgFb0arWSrtfLHN5qtTLXaqXWrVqZM9XKnFerFjUz85nTJ/ZEiJUYdLFd2nzne9SjCQAgIgMAY2OnX5dKJd7b39//UoBQq9U0ERGREAB3/S7Q/T0wg5kZYC+dyVunThz+yJp1l789zuli+28NuABoFILg1Knj39eXSf9mNpd7hZQW6vWqZgYJISKM4+IAF/lZ17Zt6/jR4z982aYr/y0GXWyX0tT3JtCO3dyX7XtvKp16vW2nUKuVDdCElFIy97qm8HkuOxSAjsBsJADO9GX++K677roLgMvMREQcH5fYnq6J5znQKPAwhoj00aOHri0Upj8xODh4T39+6PWe53G9XtFCSCGlXOTfwguDDgCREM1GwyxbtuyyrZs3/jQRmX379sn4qMT2gg0pmZn27dsnd+zY4QHAkSNHrhgayv2aZdk/lU7nrHq9DGZoIYQkonOEjvOBxszne3Iws7ET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";
const STAFF_CHATBOT_URL = "https://bi.dol.go.th/chatbot/login";

function renderStaffChatFab() {
  if (document.getElementById("staffChatFabWrap")) return; // กันสร้างซ้ำเผื่อถูกเรียกมากกว่า 1 ครั้งในหน้าเดียว
  const wrap = document.createElement("div");
  wrap.id = "staffChatFabWrap";
  wrap.innerHTML = `
    <div id="staffChatFabConfirm" class="staff-chat-fab-confirm">
      <div class="staff-chat-fab-confirm-text">ต้องการเปิดแชทบอทเจ้าหน้าที่ในแท็บใหม่หรือไม่?</div>
      <div class="staff-chat-fab-confirm-hint">จะพาไปที่ bi.dol.go.th</div>
      <div class="staff-chat-fab-confirm-actions">
        <button type="button" id="staffChatFabOpenBtn">เปิด</button>
        <button type="button" id="staffChatFabCloseBtn">ปิด</button>
      </div>
    </div>
    <div id="staffChatFabLabel" title="แชทบอทสำหรับเจ้าหน้าที่">🤖 แชทบอทเจ้าหน้าที่</div>
    <button type="button" id="staffChatFab" title="แชทบอทสำหรับเจ้าหน้าที่" aria-label="เปิดแชทบอทสำหรับเจ้าหน้าที่">
      <span class="staff-chat-fab-ring"></span>
      <img src="data:image/png;base64,${STAFF_CHATBOT_ICON_B64}" alt="ไอคอนแชทบอท">
    </button>`;
  document.body.appendChild(wrap);

  // กดไอคอนหรือป้ายข้อความแล้วไม่พาออกจากระบบไปทันที แต่เปิดกล่องเล็กๆ ให้เลือกยืนยันก่อน — "เปิด" ค่อยพาไปที่
  // ลิงก์แชทบอทจริง (แท็บใหม่ ไม่เสียหน้าที่ทำงานอยู่) ส่วน "ปิด" แค่ปิดกล่องนี้กลับไปที่เดิม ไม่มีผลอะไรอีก
  const confirmBox = document.getElementById("staffChatFabConfirm");
  const toggleConfirm = (e) => {
    e.stopPropagation();
    confirmBox.classList.toggle("open");
  };
  const closeConfirm = () => confirmBox.classList.remove("open");
  const openChatbot = () => {
    window.open(STAFF_CHATBOT_URL, "_blank", "noopener,noreferrer");
    closeConfirm();
  };

  document.getElementById("staffChatFab").addEventListener("click", toggleConfirm);
  document.getElementById("staffChatFabLabel").addEventListener("click", toggleConfirm);
  document.getElementById("staffChatFabOpenBtn").addEventListener("click", openChatbot);
  document.getElementById("staffChatFabCloseBtn").addEventListener("click", closeConfirm);
  // ปิดกล่องอัตโนมัติเมื่อคลิกที่อื่นนอกกล่อง (แบบเดียวกับเมนู dropdown อื่นๆ ของ topbar ด้านบน)
  document.addEventListener("click", (e) => {
    if (confirmBox.classList.contains("open") && !wrap.contains(e.target)) closeConfirm();
  });
}

// ---------------------------------------------------------------------------
// กระดิ่งแจ้งเตือน "ข้อความจากประชาชน" — แสดงเฉพาะเมื่อมีข้อความค้างตอบเท่านั้น (ไม่มีข้อความค้าง = ไม่มีปุ่มบน
// หน้าจอเลย ไม่รบกวนสายตาเพิ่มจากที่มีอยู่แล้ว) ต่อท้าย renderTopbar() เสมอเหมือนปุ่มแชทบอทด้านบน แต่แยกเป็นคนละปุ่ม
// เพราะแชทบอทพาออกนอกระบบ (bi.dol.go.th) ส่วนกระดิ่งนี้คือข้อความในระบบเอง ใช้โทนสีทอง (var(--gold)) ของระบบแยกจาก
// โทนฟ้าของปุ่มแชทบอทให้เห็นชัดว่าเป็นคนละฟีเจอร์ ใช้ endpoint ที่มีอยู่แล้วเกือบทั้งหมด: GET
// /survey-cases/messages/pending (ใหม่ — กรองสิทธิ์ผ่าน scope_case_filter() ฝั่ง backend เหมือนหน้าอื่นๆ ทุกหน้า)
// และ PATCH /survey-cases/messages/<id>/reply กับ /resolve (ของเดิม ใช้ในหน้า case.html อยู่แล้ว ไม่ได้แก้)
// ---------------------------------------------------------------------------
const NOTIF_POLL_INTERVAL_MS = 60000; // ดึงรายการซ้ำทุก 1 นาที พอเห็นข้อความใหม่ไว โดยไม่ยิง request ถี่เกินไป
let notifPendingMessages = [];
let notifCurrentUserRole = null;

// เฉพาะบทบาทที่มองเห็นงานรังวัดได้เท่านั้นที่ควรมีกระดิ่งนี้ (ตรงกับเงื่อนไขที่ renderTopbar() ด้านบนใช้ตัดสินใจ
// ว่าจะมีเมนู "งานรังวัด" ให้เห็นไหม) — branch_user_admin ไม่เกี่ยวกับงานรังวัดเลย ไม่ต้องยิง request เปล่าๆ ให้เปลือง
const NOTIF_ELIGIBLE_ROLES = new Set([
  "system_admin", "administrator", "province_admin", "supervisor", "branch_admin", "surveyor",
]);

// บทบาทที่กด "อ่านแล้ว" (ปิดเรื่องโดยไม่ตอบข้อความ) ได้ — ต้องตรงกับ canResolveMessages() ใน frontend/case.html
// และ role ที่ resolve_case_message() ฝั่ง backend อนุญาตทุกประการ (ไม่รวมช่างรังวัด — ของเดิมตั้งใจจำกัดไว้แบบนี้
// อยู่แล้ว ไม่ได้ขยายสิทธิ์ให้ที่นี่) ช่างรังวัดเห็นเฉพาะปุ่มตอบกลับสำเร็จรูป/พิมพ์เอง ซึ่งใช้ /reply ที่อนุญาตอยู่แล้ว
const NOTIF_RESOLVE_ROLES = new Set(["system_admin", "administrator", "province_admin", "supervisor", "branch_admin"]);

// ไอคอนกระดิ่ง 3 มิติตามภาพที่ผู้ใช้เลือก (ฝังเป็น data URI ในไฟล์นี้เลย ไม่ต้องเพิ่มไฟล์รูปแยกในโปรเจ็กต์)
const NOTIF_BELL_ICON_IMG =
  '<img src="data:image/png;base64,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" alt="" />';

function renderNotificationBell(user) {
  if (!NOTIF_ELIGIBLE_ROLES.has(user.role)) return;
  if (document.getElementById("notifBellWrap")) return; // กันสร้างซ้ำเผื่อถูกเรียกมากกว่า 1 ครั้งในหน้าเดียว
  notifCurrentUserRole = user.role;

  const wrap = document.createElement("div");
  wrap.id = "notifBellWrap";
  wrap.innerHTML = `
    <div id="notifBellPanel" class="notif-bell-panel">
      <div class="notif-bell-head">
        <b>ข้อความจากประชาชน</b>
        <span class="notif-bell-count" id="notifBellCount"></span>
        <button type="button" id="notifBellCloseBtn" aria-label="ปิด">&times;</button>
      </div>
      <div class="notif-bell-list" id="notifBellList"></div>
      <div class="notif-bell-foot"><a href="/cases.html">ดูข้อความทั้งหมดในหน้างานรังวัด &rarr;</a></div>
    </div>
    <button type="button" id="notifBellBtn" title="ข้อความจากประชาชน" aria-label="ข้อความจากประชาชน" hidden>
      ${NOTIF_BELL_ICON_IMG}
      <span class="notif-bell-badge" id="notifBellBadge"></span>
    </button>`;
  document.body.appendChild(wrap);

  const panel = document.getElementById("notifBellPanel");
  const togglePanel = (e) => {
    e.stopPropagation();
    panel.classList.toggle("open");
  };
  document.getElementById("notifBellBtn").addEventListener("click", togglePanel);
  document.getElementById("notifBellCloseBtn").addEventListener("click", () => panel.classList.remove("open"));
  // ปิดแผงอัตโนมัติเมื่อคลิกที่อื่นนอกกล่อง (แบบเดียวกับกล่องยืนยันของปุ่มแชทบอทด้านบน)
  document.addEventListener("click", (e) => {
    if (panel.classList.contains("open") && !wrap.contains(e.target)) panel.classList.remove("open");
  });

  refreshNotifBell();
  setInterval(refreshNotifBell, NOTIF_POLL_INTERVAL_MS);
}

async function refreshNotifBell() {
  if (!document.getElementById("notifBellBtn")) return; // หน้าเปลี่ยนไปแล้ว (เผื่อ timer ค้างจากหน้าเดิม)
  try {
    notifPendingMessages = (await apiFetch("/survey-cases/messages/pending")) || [];
  } catch (e) {
    return; // เงียบไว้ ไม่ให้ error ของกระดิ่งไปรบกวนงานหลักของหน้า (เช่น token หมดอายุกลางทาง ก็ redirect ไปเองอยู่แล้ว)
  }
  renderNotifBellList();
}

function renderNotifBellList() {
  const btn = document.getElementById("notifBellBtn");
  if (!btn) return;

  const n = notifPendingMessages.length;
  btn.hidden = n === 0; // ไม่มีข้อความค้าง = ไม่แสดงกระดิ่งเลย
  if (n === 0) {
    document.getElementById("notifBellPanel").classList.remove("open");
    return;
  }
  document.getElementById("notifBellBadge").textContent = n > 9 ? "9+" : String(n);
  document.getElementById("notifBellCount").textContent = n + " เรื่องรอตอบ";

  document.getElementById("notifBellList").innerHTML = notifPendingMessages.map((m) => {
    const ageMs = Date.now() - new Date(m.created_at).getTime();
    const ageCls = ageMs > 24 * 3600 * 1000 ? "late" : ageMs > 6 * 3600 * 1000 ? "warn" : "new";
    // "อ่านแล้ว" (ปิดเรื่องแบบไม่ตอบข้อความ) จำกัดเฉพาะบทบาทใน NOTIF_RESOLVE_ROLES เหมือน canResolveMessages() ใน
    // case.html — ช่างรังวัดไม่มีสิทธิ์เรียก /resolve (backend ตอบ 403) จึงไม่แสดงลิงก์นี้ให้กดแล้วเจอ error
    const markReadLink = NOTIF_RESOLVE_ROLES.has(notifCurrentUserRole)
      ? `<button type="button" class="notif-mark-read-link" onclick="notifMarkRead('${m.id}')">ทำเครื่องหมายว่าอ่านแล้ว (ไม่ตอบกลับ)</button>`
      : "";
    return `
      <div class="notif-msg" id="notif-row-${m.id}">
        <div class="notif-msg-top">
          <a href="/case.html?id=${m.case_id}" class="notif-msg-code">${notifEsc(m.case_code)}</a>
          <span class="notif-msg-who">${notifEsc(m.requester_name)}</span>
          <span class="notif-age ${ageCls}">รอ ${notifAgeLabel(ageMs)}</span>
        </div>
        <div class="notif-msg-text">${notifEsc(m.description)}</div>
        <div class="notif-reply-row">
          <textarea id="notif-textarea-${m.id}" rows="2" placeholder="พิมพ์คำตอบถึงประชาชน..."
            onkeydown="if(event.key==='Enter' &amp;&amp; !event.shiftKey){ event.preventDefault(); notifSendCustomReply('${m.id}'); }"></textarea>
          <button type="button" onclick="notifSendCustomReply('${m.id}')">ส่ง</button>
        </div>
        ${markReadLink}
      </div>`;
  }).join("");
}

function notifEsc(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function notifAgeLabel(ms) {
  const min = Math.floor(ms / 60000);
  if (min < 60) return `${Math.max(min, 1)} นาที`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ชั่วโมง`;
  return `${Math.floor(hr / 24)} วัน`;
}

// ส่งคำตอบถึงประชาชน (notifSendCustomReply ด้านล่างเรียกฟังก์ชันนี้) ใช้ endpoint /reply เดิมที่ case.html
// ใช้อยู่แล้วทุกประการ — ตอบสำเร็จแล้วถือว่าปิดเรื่องอัตโนมัติไปด้วย (ดู reply_case_message ฝั่ง backend)
// จึงลบออกจากรายการที่ต้องตอบได้เลยโดยไม่ต้องเรียก resolve ซ้ำ
async function notifQuickReply(messageId, replyText) {
  try {
    await apiFetch("/survey-cases/messages/" + messageId + "/reply", {
      method: "PATCH",
      body: JSON.stringify({ reply: replyText }),
    });
  } catch (e) {
    alert(e.message || "ส่งคำตอบไม่สำเร็จ กรุณาลองใหม่");
    return;
  }
  notifPendingMessages = notifPendingMessages.filter((m) => m.id !== messageId);
  renderNotifBellList();
}

// ทำเครื่องหมายว่าอ่านแล้วโดยไม่ต้องพิมพ์คำตอบ (เช่น ข้อความซ้ำ หรือได้โทรตอบไปแล้ว) — ใช้ endpoint /resolve เดิม
async function notifMarkRead(messageId) {
  try {
    await apiFetch("/survey-cases/messages/" + messageId + "/resolve", { method: "PATCH" });
  } catch (e) {
    alert(e.message || "ทำรายการไม่สำเร็จ กรุณาลองใหม่");
    return;
  }
  notifPendingMessages = notifPendingMessages.filter((m) => m.id !== messageId);
  renderNotifBellList();
}

// กล่องพิมพ์คำตอบแสดงอยู่ในการ์ดข้อความทุกใบตลอดเวลา (ไม่ต้องกดปุ่ม "พิมพ์เอง" ก่อนเหมือนเดิม) กด Enter
// (ไม่กด Shift) หรือกดปุ่ม "ส่ง" ก็ส่งได้ทันที
async function notifSendCustomReply(messageId) {
  const textarea = document.getElementById("notif-textarea-" + messageId);
  const text = (textarea.value || "").trim();
  if (!text) { textarea.focus(); return; }
  await notifQuickReply(messageId, text);
}
