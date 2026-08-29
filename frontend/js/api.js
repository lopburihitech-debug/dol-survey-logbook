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

// สถานะที่ถือว่า "จบงานแล้ว" ไม่นับเป็นงานค้าง — ต้องตรงกับ CaseStatus.CLOSED_SET ฝั่ง backend (constants.py)
// และ CLOSED_SET ในหน้า my-work.html เพื่อให้ตัวเลขป้ายบนเมนูตรงกับที่แสดงในหน้านั้นเป๊ะๆ
const NAV_CLOSED_SET = new Set(["COMPLETED", "CLOSED", "CANCELLED"]);

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
  // "แผนที่ช่างรังวัด" — ดูตำแหน่งงานที่ช่างรังวัดแต่ละคนออกไปตามวันนัด บนแผนที่ พร้อมระยะทางจากสำนักงาน จำกัดเฉพาะ
  // บทบาทเชิงบริหาร/หัวหน้า (เหมือนเมนู "ช่างรังวัด" ด้านล่าง) เพราะเป็นข้อมูลตำแหน่งของพนักงานหลายคนพร้อมกัน ไม่ใช่
  // ข้อมูลส่วนตัวของช่างรังวัดเอง (ต่างจากปฏิทินซึ่งช่างรังวัดดูตารางนัดของตัวเองได้ปกติ)
  if (["system_admin", "administrator", "province_admin", "supervisor", "branch_admin"].includes(user.role)) {
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
  // ผู้ใช้งาน/สำนักงาน: เฉพาะ system_admin เท่านั้น
  const manageItems = [];
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
