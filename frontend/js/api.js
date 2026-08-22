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
  if (["system_admin", "administrator", "province_admin", "supervisor", "branch_admin"].includes(user.role)) {
    primary.push({ href: "/surveyors.html", label: "ช่างรังวัด", key: "surveyors", icon: NAV_ICONS.people });
  }

  // งานเชิงจัดการ (นำเข้า/แก้ไขข้อมูลช่างรังวัด) — จัดกลุ่มไว้ในเมนูย่อย "จัดการ" ไม่ให้แถวหลักรกเกินไป
  // นำเข้างาน (bulk import): จำกัดเฉพาะบทบาทที่สร้างเรื่องได้ ไม่รวม branch_admin (สร้างเรื่องใหม่ไม่ได้)
  // จัดการช่างรังวัด: เจ้าพนักงานที่ดินสาขาก็เพิ่ม/แก้ไขบัญชีช่างรังวัดในสาขาตนเองได้ด้วย (ดู surveyors.py)
  const manageItems = [];
  if (["system_admin", "administrator", "province_admin"].includes(user.role)) {
    manageItems.push({ href: "/cases-import.html", label: "นำเข้างาน", key: "cases-import" });
  }
  if (["system_admin", "administrator", "province_admin", "branch_admin"].includes(user.role)) {
    manageItems.push({ href: "/surveyors-manage.html", label: "จัดการช่างรังวัด", key: "surveyors-manage" });
  }

  // งานดูแลระบบระดับสูงสุด — เฉพาะ system_admin เท่านั้น จัดไว้ในเมนูย่อย "ระบบ" แยกออกจากงานประจำวันชัดเจน
  const systemItems = [];
  if (user.role === "system_admin") {
    systemItems.push({ href: "/users.html", label: "ผู้ใช้งาน", key: "users" });
    systemItems.push({ href: "/offices.html", label: "สำนักงาน", key: "offices" });
  }

  let navHtml = primary.map((n) => navLinkHtml(n, activePage)).join("");
  if (manageItems.length) navHtml += navDropdownHtml("จัดการ", NAV_ICONS.manage, manageItems, activePage);
  if (systemItems.length) navHtml += navDropdownHtml("ระบบ", NAV_ICONS.system, systemItems, activePage);

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

  return user;
}
