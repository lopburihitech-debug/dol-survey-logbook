// กราฟสำหรับ Dashboard — เขียนด้วย SVG + JS ธรรมดาทั้งหมด ไม่พึ่งไลบรารีภายนอก (เช่น Chart.js จาก CDN)
// ให้สอดคล้องกับหลักการเดิมของระบบ (ดู css/style.css บรรทัดแรก) คือต้อง deploy/ใช้งานได้แม้เครื่องไม่มีอินเทอร์เน็ต

// ตัดชื่อสำนักงานเต็มให้เหลือแค่ส่วนที่บอกความแตกต่าง (สาขาอะไร) เพื่อให้ป้ายกำกับกราฟสั้น อ่านง่าย
// เช่น "สำนักงานที่ดินจังหวัดลพบุรี สาขาบ้านหมี่" -> "สาขาบ้านหมี่", "สำนักงานที่ดินจังหวัดลพบุรี" (ไม่มีคำว่าสาขา) -> "สำนักงานจังหวัด (ส่วนกลาง)"
function officeShortLabel(name) {
  const idx = (name || "").indexOf("สาขา");
  if (idx >= 0) return name.slice(idx);
  return "สำนักงานจังหวัด (ส่วนกลาง)";
}

// จัดกลุ่มรายการสำนักงานจาก /dashboard/by-office ตามจังหวัด -> [{ province, offices: [...] }] เรียงจังหวัดตามตัวอักษร
function groupOfficesByProvince(offices) {
  const byProvince = {};
  offices.forEach((o) => {
    if (!byProvince[o.province]) byProvince[o.province] = [];
    byProvince[o.province].push(o);
  });
  return Object.keys(byProvince)
    .sort((a, b) => a.localeCompare(b, "th"))
    .map((province) => ({ province, offices: byProvince[province] }));
}

// กราฟแท่งแนวนอนแบบ stacked ต่อสำนักงาน/สาขา 1 แถว = 1 สำนักงาน แบ่งเป็น 3 ส่วน: เสร็จแล้ว (เขียว) / ค้างแต่ยังไม่เกินกำหนด (ทอง) /
// เกินกำหนด (แดง) — เรียงจากสำนักงานที่มีงานค้างมากที่สุดขึ้นก่อน เพื่อให้เห็นภาระงานที่ต้องเร่งจัดการได้เร็วที่สุด
function renderOfficeBarChart(container, rows) {
  if (!rows || rows.length === 0) {
    container.innerHTML = `<div class="empty-state">ไม่มีข้อมูล</div>`;
    return;
  }
  const sorted = [...rows].sort((a, b) => b.pending_cases - a.pending_cases);
  const maxTotal = Math.max(...sorted.map((r) => r.total_cases), 1);

  const barH = 22, gap = 16, labelW = 160, chartW = 340, rowH = barH + gap;
  const svgW = labelW + chartW + 46;
  const svgH = sorted.length * rowH + 6;
  const scale = chartW / maxTotal;

  const parts = sorted.map((r, i) => {
    const y = i * rowH + 4;
    const onTime = Math.max(r.pending_cases - r.overdue_cases, 0);
    let x = labelW;
    let segs = "";
    if (r.completed_cases > 0) {
      const w = r.completed_cases * scale;
      segs += `<rect x="${x}" y="${y}" width="${Math.max(w, 1)}" height="${barH}" fill="var(--success)"></rect>`;
      x += w;
    }
    if (onTime > 0) {
      const w = onTime * scale;
      segs += `<rect x="${x}" y="${y}" width="${Math.max(w, 1)}" height="${barH}" fill="var(--gold)"></rect>`;
      x += w;
    }
    if (r.overdue_cases > 0) {
      const w = r.overdue_cases * scale;
      segs += `<rect x="${x}" y="${y}" width="${Math.max(w, 1)}" height="${barH}" fill="var(--danger)"></rect>`;
      x += w;
    }
    if (r.total_cases === 0) {
      segs += `<rect x="${x}" y="${y}" width="2" height="${barH}" fill="#D8E2DC"></rect>`;
    }
    return `
      <text x="${labelW - 10}" y="${y + barH / 2 + 4}" text-anchor="end" font-size="12.5" fill="var(--text)">${r.label}</text>
      ${segs}
      <text x="${labelW + chartW + 10}" y="${y + barH / 2 + 4}" font-size="12.5" font-weight="700" fill="var(--text)">${r.total_cases}</text>
    `;
  }).join("");

  container.innerHTML = `
    <svg viewBox="0 0 ${svgW} ${svgH}" width="100%" height="${svgH}" style="max-width:600px; display:block;">
      ${parts}
    </svg>
    <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:8px; font-size:12px; color:var(--text-muted);">
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--success);margin-right:5px;vertical-align:middle;"></span>เสร็จแล้ว</span>
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--gold);margin-right:5px;vertical-align:middle;"></span>ค้าง (ยังไม่เกินกำหนด)</span>
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--danger);margin-right:5px;vertical-align:middle;"></span>เกินกำหนด</span>
    </div>
  `;
}

// กราฟโดนัทสัดส่วนสถานะงานทั้งหมด พร้อม legend บอกจำนวน/เปอร์เซ็นต์ — segments: [{label, count, color}]
function renderDonutChart(container, segments) {
  const nonZero = segments.filter((s) => s.count > 0);
  const total = nonZero.reduce((sum, s) => sum + s.count, 0);
  if (total === 0) {
    container.innerHTML = `<div class="empty-state">ไม่มีข้อมูล</div>`;
    return;
  }
  const size = 190, cx = size / 2, cy = size / 2, r = 74, strokeW = 30;
  const circumference = 2 * Math.PI * r;
  let offset = 0;
  const arcs = nonZero
    .map((s) => {
      const dash = (s.count / total) * circumference;
      const arc = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}" stroke-width="${strokeW}"
        stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${-offset}"
        transform="rotate(-90 ${cx} ${cy})"></circle>`;
      offset += dash;
      return arc;
    })
    .join("");

  const legend = nonZero
    .map(
      (s) => `
      <div style="display:flex; align-items:center; gap:8px; font-size:12.5px; padding:3px 0;">
        <span style="width:11px; height:11px; border-radius:3px; background:${s.color}; flex-shrink:0;"></span>
        <span style="flex:1; color:var(--text);">${s.label}</span>
        <span style="font-weight:700; color:var(--text);">${s.count}</span>
        <span style="color:var(--text-muted); min-width:40px; text-align:right;">${((s.count / total) * 100).toFixed(0)}%</span>
      </div>`
    )
    .join("");

  container.innerHTML = `
    <div style="display:flex; gap:24px; align-items:center; flex-wrap:wrap;">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="flex-shrink:0;">
        ${arcs}
        <text x="${cx}" y="${cy - 3}" text-anchor="middle" font-size="23" font-weight="700" fill="var(--text)">${total}</text>
        <text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="11" fill="var(--text-muted)">งานทั้งหมด</text>
      </svg>
      <div style="flex:1; min-width:210px;">${legend}</div>
    </div>
  `;
}
