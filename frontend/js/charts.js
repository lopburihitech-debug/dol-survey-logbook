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

// กราฟแท่งแนวนอนแบบ stacked ต่อแถว (สำนักงาน/สาขา หรือจังหวัด แล้วแต่ข้อมูลที่ส่งเข้ามา) แบ่งเป็น 2 ส่วน: เสร็จแล้ว (เขียว) /
// ค้าง (ทอง — รวมทั้งที่เกินกำหนดและยังไม่เกินกำหนดเข้าด้วยกัน ไม่แยกสีเกินกำหนดอีกต่อไปตามที่ผู้ใช้ระบบร้องขอ)
// — เรียงจากแถวที่มีงานค้างมากที่สุดขึ้นก่อน เพื่อให้เห็นภาระงานที่ต้องเร่งจัดการได้เร็วที่สุด
// rows[i].sublabel (ไม่ระบุก็ได้): บรรทัดที่ 2 ตัวเล็กจางกว่าใต้ป้ายชื่อหลัก (เช่น ชื่อจังหวัดกำกับใต้ชื่อสำนักงาน)
// — ใช้แทนการต่อสตริงชื่อจังหวัด+ชื่อสำนักงานเป็นบรรทัดเดียว เพราะข้อความยาวรวมกันจะยาวเกินคอลัมน์ป้ายชื่อ
// จนถูกตัด/บังไปโดย viewBox (SVG ตัดสิ่งที่วาดเลย x=0 ไปทางซ้ายทิ้งเสมอ)
// opts (ไม่ระบุก็ได้): { labelW: ความกว้างคอลัมน์ป้ายชื่อ (ค่าเริ่มต้น 160),
//                        highlightLabel: เน้นแถวที่ label ตรงกับค่านี้ด้วยพื้นหลังและตัวหนา (เช่น เน้นจังหวัดที่ผู้ใช้เลือกไว้
//                        ในโหมดเปรียบเทียบทุกจังหวัดของ dashboard.html) }
function renderOfficeBarChart(container, rows, opts) {
  opts = opts || {};
  if (!rows || rows.length === 0) {
    container.innerHTML = `<div class="empty-state">ไม่มีข้อมูล</div>`;
    return;
  }
  const sorted = [...rows].sort((a, b) => b.pending_cases - a.pending_cases);
  const maxTotal = Math.max(...sorted.map((r) => r.total_cases), 1);
  const hasSublabels = sorted.some((r) => r.sublabel);

  // แถวที่มี sublabel ต้องการพื้นที่สูงกว่าเดิมเล็กน้อยเพื่อวางข้อความ 2 บรรทัดซ้อนกันโดยไม่ชนกัน
  const barH = hasSublabels ? 26 : 22, gap = 16, labelW = opts.labelW || 160, chartW = 340, rowH = barH + gap;
  const svgW = labelW + chartW + 46;
  const svgH = sorted.length * rowH + 6;
  const scale = chartW / maxTotal;

  const parts = sorted.map((r, i) => {
    const y = i * rowH + 4;
    const centerY = y + barH / 2;
    const highlighted = !!opts.highlightLabel && r.label === opts.highlightLabel;
    let x = labelW;
    let segs = "";
    if (r.completed_cases > 0) {
      const w = r.completed_cases * scale;
      segs += `<rect x="${x}" y="${y}" width="${Math.max(w, 1)}" height="${barH}" fill="var(--success)"></rect>`;
      x += w;
    }
    // งานค้างทั้งหมด (เกินกำหนด + ยังไม่เกินกำหนด) รวมเป็นส่วนเดียวสีทอง ไม่แยกสีเกินกำหนดเป็นแดงอีกต่อไป
    if (r.pending_cases > 0) {
      const w = r.pending_cases * scale;
      segs += `<rect x="${x}" y="${y}" width="${Math.max(w, 1)}" height="${barH}" fill="var(--gold)"></rect>`;
      x += w;
    }
    if (r.total_cases === 0) {
      segs += `<rect x="${x}" y="${y}" width="2" height="${barH}" fill="#D8E2DC"></rect>`;
    }
    const highlightBg = highlighted
      ? `<rect x="0" y="${y - 3}" width="${svgW}" height="${barH + 6}" rx="5" fill="var(--accent-light)"></rect>`
      : "";
    const mainY = r.sublabel ? centerY - 2 : centerY + 4;
    const subLine = r.sublabel
      ? `<text x="${labelW - 10}" y="${centerY + 11}" text-anchor="end" font-size="10" fill="var(--text-muted)">${r.sublabel}</text>`
      : "";
    return `
      ${highlightBg}
      <text x="${labelW - 10}" y="${mainY}" text-anchor="end" font-size="12.5" font-weight="${highlighted ? 700 : 400}" fill="${highlighted ? "var(--primary)" : "var(--text)"}">${r.label}</text>
      ${subLine}
      ${segs}
      <text x="${labelW + chartW + 10}" y="${centerY + 4}" font-size="12.5" font-weight="700" fill="var(--text)">${r.total_cases}</text>
    `;
  }).join("");

  container.innerHTML = `
    <svg viewBox="0 0 ${svgW} ${svgH}" width="100%" height="${svgH}" style="max-width:640px; display:block;">
      ${parts}
    </svg>
    <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:8px; font-size:12px; color:var(--text-muted);">
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--success);margin-right:5px;vertical-align:middle;"></span>เสร็จแล้ว</span>
      <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--gold);margin-right:5px;vertical-align:middle;"></span>ค้าง (รวมเกินกำหนด)</span>
    </div>
  `;
}

// กราฟโดนัทสัดส่วนสถานะงานทั้งหมด พร้อม legend บอกจำนวน/เปอร์เซ็นต์ — segments: [{label, count, color}]
// centerLabel: ข้อความใต้ตัวเลขรวมกลางวงกลม (ไม่ระบุ = "งานทั้งหมด" ตามการใช้งานเดิมในหน้า Dashboard ผู้ดูแลระบบ
// — พารามิเตอร์นี้เพิ่มทีหลังแบบ optional เพื่อให้หน้าอื่น เช่น โปรไฟล์ช่างรังวัด เรียกใช้กับข้อมูลชุดอื่น เช่น
// การกระจายคะแนนความพึงพอใจ ได้โดยไม่กระทบของเดิม)
function renderDonutChart(container, segments, centerLabel) {
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
        <text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="11" fill="var(--text-muted)">${centerLabel || "งานทั้งหมด"}</text>
      </svg>
      <div style="flex:1; min-width:210px;">${legend}</div>
    </div>
  `;
}

// รายการจัดอันดับ (top-N) พร้อมแถบสัดส่วนเทียบกับค่าสูงสุดในชุดข้อมูล — ใช้ร่วมกันสำหรับทุกการ์ดจัดอันดับใน
// Dashboard ผู้บริหาร (จังหวัด/สำนักงาน/ช่างรังวัดที่มีงานค้างมากที่สุด) เพื่อให้หน้าตาเป็นแบบเดียวกันทั้งหมด
// rows: [{label, value, sublabel?}] ต้องเรียงจากมากไปน้อยมาก่อนแล้ว (ฟังก์ชันนี้ไม่เรียงเอง)
// opts: { emptyText? } ข้อความเมื่อไม่มีข้อมูล (ค่าเริ่มต้น "ไม่มีข้อมูล")
function renderRankedBarList(container, rows, opts) {
  opts = opts || {};
  if (!rows || rows.length === 0) {
    container.innerHTML = `<div class="empty-state">${opts.emptyText || "ไม่มีข้อมูล"}</div>`;
    return;
  }
  const maxVal = Math.max(...rows.map((r) => r.value), 1);
  container.innerHTML = rows
    .map((r, i) => {
      const rankClass = i === 0 ? "rank-1" : i === 1 ? "rank-2" : i === 2 ? "rank-3" : "";
      const pct = Math.max((r.value / maxVal) * 100, r.value > 0 ? 3 : 0);
      return `
      <div class="ranked-row">
        <div class="ranked-rank ${rankClass}">${i + 1}</div>
        <div class="ranked-body">
          <div class="ranked-top">
            <span class="ranked-label" title="${r.label}">${r.label}</span>
            <span class="ranked-value">${r.value}</span>
          </div>
          ${r.sublabel ? `<div class="ranked-sub">${r.sublabel}</div>` : ""}
          <div class="ranked-track"><div class="ranked-fill" style="width:${pct}%;"></div></div>
        </div>
      </div>`;
    })
    .join("");
}

// กราฟแท่งแนวนอนแสดงแนวโน้มตามช่วงเวลา (เช่น จำนวนงานที่ปิดสำเร็จรายเดือนของช่างรังวัดคนหนึ่ง — หน้าโปรไฟล์ช่างรังวัด
// surveyor-profile.html) เรียงตามลำดับเวลาเก่า->ใหม่ตามที่ส่งเข้ามาเสมอ (ไม่เรียงตามขนาดเหมือน renderOfficeBarChart
// ด้านบน เพราะจุดประสงค์คือดู "แนวโน้ม" ตามเวลา ไม่ใช่เปรียบเทียบอันดับ) — rows: [{label, count}]
function renderMonthlyTrendChart(container, rows, opts) {
  if (!rows || rows.length === 0) {
    container.innerHTML = `<div class="empty-state">ยังไม่มีข้อมูล</div>`;
    return;
  }
  opts = opts || {};
  // opts.threshold (ตัวเลข, optional): วาดเส้นประ "เกณฑ์" อ้างอิงในกราฟ พร้อมป้ายกำกับ opts.thresholdLabel และ
  // เปลี่ยนสีแท่งที่มีค่าถึง/เกินเกณฑ์เป็น var(--danger) แทน var(--primary) — ใช้กับกราฟงานค้างรายเดือนที่มีเกณฑ์
  // สูงสุดต่อช่าง (ดู surveyor-profile.html) ไม่กระทบกราฟอื่นที่เรียกฟังก์ชันนี้โดยไม่ส่ง opts
  const threshold = opts.threshold;
  const maxVal = Math.max(...rows.map((r) => r.count), threshold || 0, 1);
  const barH = 20, gap = 12, labelW = 90, chartW = 320, rowH = barH + gap;
  const svgW = labelW + chartW + 40;
  const topPad = threshold != null ? 16 : 4;
  const svgH = rows.length * rowH + topPad + 4;
  const scale = chartW / maxVal;

  const parts = rows
    .map((r, i) => {
      const y = i * rowH + topPad;
      const w = r.count > 0 ? Math.max(r.count * scale, 2) : 0;
      const overCap = threshold != null && r.count >= threshold;
      const barColor = overCap ? "var(--danger)" : "var(--primary)";
      return `
      <text x="${labelW - 10}" y="${y + barH / 2 + 4}" text-anchor="end" font-size="12" fill="var(--text)">${r.label}</text>
      <rect x="${labelW}" y="${y}" width="${w}" height="${barH}" rx="3" fill="${barColor}"></rect>
      <text x="${labelW + w + 8}" y="${y + barH / 2 + 4}" font-size="12" font-weight="700" fill="${overCap ? "var(--danger)" : "var(--text)"}">${r.count}</text>
    `;
    })
    .join("");

  let thresholdLine = "";
  if (threshold != null) {
    const tx = labelW + threshold * scale;
    thresholdLine = `
      <line x1="${tx}" y1="2" x2="${tx}" y2="${svgH - 2}" stroke="var(--danger)" stroke-width="1.5" stroke-dasharray="4,3"></line>
      <text x="${tx}" y="10" text-anchor="middle" font-size="10.5" font-weight="700" fill="var(--danger)">${opts.thresholdLabel || threshold}</text>
    `;
  }

  container.innerHTML = `
    <svg viewBox="0 0 ${svgW} ${svgH}" width="100%" height="${svgH}" style="max-width:520px; display:block;">
      ${parts}
      ${thresholdLine}
    </svg>
  `;
}
