/* Access-key gate: any /api 401 with need_key shows the key screen (cookie-based, set by POST /api/auth). */
(() => {
  const f0 = window.fetch.bind(window);
  let shown = false;
  window.fetch = async (u, o) => {
    const r = await f0(u, o);
    if (r.status === 401 && String(u).startsWith("/api/") && String(u) !== "/api/auth" && !shown) {
      const d = await r.clone().json().catch(() => ({}));
      if (d.need_key) {
        shown = true; const l = document.getElementById("lock"); l.hidden = false;
        setTimeout(() => document.getElementById("lock-key").focus(), 50);
      }
    }
    return r;
  };
  document.addEventListener("DOMContentLoaded", () => document.getElementById("lock-form").addEventListener("submit", async e => {
    e.preventDefault();
    const err = document.getElementById("lock-err"); err.hidden = true;
    const r = await f0("/api/auth", { method: "POST", headers: { "content-type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ key: document.getElementById("lock-key").value }) }).catch(() => null);
    if (r && r.ok) { location.reload(); return; }
    const d = r ? await r.json().catch(() => ({})) : { error: "Server unreachable (it may still be waking up). Try again." };
    err.textContent = d.error || `Failed (${r.status})`; err.hidden = false;
  }));
})();
/* Goop front end: Home (WHOOP-style), #health and #stress detail pages. */
const $ = id => document.getElementById(id);
/* WHOOP palette (brand guidelines): recovery #16EC06/#FFDE00/#FF0026, strain #0093E7, sleep #7BA1BB; mirrors the CSS tokens */
const C = { green: "#16ec06", ok: "#6eeda5", yellow: "#ffde00", red: "#ff0026", orange: "#f2ab45", strain: "#0093e7",
  sleep: "#7ba1bb", track: "#363b40", mut: "#9aa4ad", stress: "#7fb8ff", bb: "#5ad1c9", hr: "#ff5a6a", spo2: "#8fd3ff", resp: "#b9a4ff", hrv: "#3ddc84" };
const zoneColor = z => ({ green: C.green, yellow: C.yellow, red: C.red })[z] || C.mut;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const hm = s => s == null ? "—" : `${Math.floor(s / 3600)}:${String(Math.round(s % 3600 / 60) % 60).padStart(2, "0")}`;
const fmt = (v, d = 0) => v == null ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const ampm = t => { if (!t) return ""; const [h, m] = t.split(":").map(Number); return `${(h % 12) || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "a.m." : "p.m."}`; };
const timeOf = iso => iso && iso.length >= 16 ? iso.slice(11, 16) : null;
const wd = iso => new Date(iso.slice(0, 10) + "T12:00").toLocaleDateString("en-US", { weekday: "short" });
const key = new URLSearchParams(location.search).get("key") || localStorage.getItem("goopKey");
if (key) localStorage.setItem("goopKey", key);
let DATA = null;

/* ---------- rings ---------- */
function ringSvg(frac, color, sw = 8) {
  const r = 50 - sw / 2 - 1, c = 2 * Math.PI * r, f = Math.max(0, Math.min(1, frac || 0));
  return `<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="${r}" fill="none" stroke="${C.track}" stroke-width="${sw}"/>
    <circle cx="50" cy="50" r="${r}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round"
      stroke-dasharray="${c * f} ${c}" transform="rotate(-90 50 50)" style="transition:stroke-dasharray .8s"/></svg>`;
}
function ring(el, frac, color, value, label, pct) {
  el.innerHTML = `<div class="dial">${ringSvg(frac, color, 6.4)}<div class="val">${value}${pct ? "<small>%</small>" : ""}</div></div>
    <div class="lbl">${label}<i>›</i></div>`;
}

/* ---------- charts ---------- */
const toMin = t => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
function unwrap(series) { // overnight series cross midnight: keep x monotonic
  let off = 0, prev = -1;
  return series.map(([t, v]) => { let m = toMin(t) + off; if (prev >= 0 && m < prev - 600) { off += 1440; m += 1440; } prev = m; return [m, v, t]; });
}
function seriesChart(series, o = {}) {
  if (!series || series.length < 2) return `<div class="empty">No readings for this day.</div>`;
  const pts = unwrap(series), W = 340, H = 130, L = 28, R = 6, T = 8, B = 20;
  const vals = pts.map(p => p[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (o.band) { lo = Math.min(lo, o.band[0]); hi = Math.max(hi, o.band[1]); }
  if (o.yMin != null) lo = o.yMin; if (o.yMax != null) hi = o.yMax;
  const pad = (hi - lo) * 0.08 || 1; lo -= o.yMin != null ? 0 : pad; hi += o.yMax != null ? 0 : pad;
  let x0 = pts[0][0], x1 = Math.max(pts[pts.length - 1][0], x0 + 1);
  let shade = null;
  if (o.shade && o.shade[0] && o.shade[1]) {  // [HH:MM, HH:MM] window, aligned to the unwrapped axis
    let a = toMin(o.shade[0]), b = toMin(o.shade[1]);
    if (a - x0 > 600) a -= 1440; if (b < a) b += 1440;
    shade = [a, b]; x0 = Math.min(x0, a); x1 = Math.max(x1, b);
  }
  if (o.hline != null) { lo = Math.min(lo, o.hline); hi = Math.max(hi, o.hline); }
  const X = m => L + (m - x0) / (x1 - x0) * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let g = "";
  (o.zones || []).forEach(z => g += `<rect x="${L}" width="${W - L - R}" y="${Y(Math.min(z[1], hi))}" height="${Math.max(0, Y(Math.max(z[0], lo)) - Y(Math.min(z[1], hi)))}" fill="${z[2]}"/>`);
  (o.grid || []).forEach(v => g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="rgba(255,255,255,.28)" stroke-dasharray="3 3"/><text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" font-size="9" fill="${C.mut}">${v}</text>`);
  if (shade) g += `<rect x="${X(shade[0])}" width="${X(shade[1]) - X(shade[0])}" y="${T}" height="${H - T - B}" fill="${C.sleep}" opacity=".13"/><text x="${X(shade[0]) + 3}" y="${T + 9}" font-size="8" fill="${C.sleep}">ASLEEP</text>`;
  if (o.band) g += `<rect x="${L}" width="${W - L - R}" y="${Y(o.band[1])}" height="${Math.max(1, Y(o.band[0]) - Y(o.band[1]))}" fill="${C.ok}" opacity=".13"/>`;
  [o.yMax != null ? hi : hi - pad, o.yMin != null ? lo : lo + pad].forEach(v => g += `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" font-size="9" fill="${C.mut}">${fmt(v, o.nd || 0)}</text>`);
  const nT = 5;
  for (let i = 0; i < nT; i++) {
    const m = x0 + (x1 - x0) * i / (nT - 1), mm = Math.round(m) % 1440;
    const lab = `${String(Math.floor(mm / 60)).padStart(2, "0")}:${String(mm % 60).padStart(2, "0")}`;
    g += `<text x="${X(m)}" y="${H - 5}" text-anchor="${i === 0 ? "start" : i === nT - 1 ? "end" : "middle"}" font-size="9" fill="${C.mut}">${lab}</text>`;
  }
  const line = o.bars
    ? pts.map(p => `<rect x="${X(p[0]) - 1}" y="${Y(p[1])}" width="2" height="${Y(lo) - Y(p[1])}" fill="${o.colorFn ? o.colorFn(p[1]) : o.color}"/>`).join("")
    : `<path d="${pts.map((p, i) => `${i && !(o.gapMin && p[0] - pts[i - 1][0] > o.gapMin) ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("")}" fill="none" stroke="${o.color || C.stress}" stroke-width="1.8" stroke-linejoin="round"/>`
      + (o.hline != null ? `<line x1="${L}" x2="${W - R}" y1="${Y(o.hline)}" y2="${Y(o.hline)}" stroke="#f2f5f7" stroke-opacity=".6" stroke-dasharray="4 3"/><text x="${W - R}" y="${Y(o.hline) - 3}" text-anchor="end" font-size="9" fill="#f2f5f7">${esc(o.hlineLabel || "avg")} ${fmt(o.hline, o.nd || 0)}</text>` : "")
      + (o.markExtremes ? [Math.min(...vals), Math.max(...vals)].map(v => { const p = pts.find(q => q[1] === v); return `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="2.6" fill="${o.color}"/>`; }).join("") : "");
  const mn = Math.min(...vals), mx = Math.max(...vals), av = o.hline != null ? o.hline : vals.reduce((a, b) => a + b, 0) / vals.length;
  const sc = scrubAttr(pts.map(p => [X(p[0]), [Y(p[1])], `<b>${fmt(p[1], o.nd || 0)}${o.unit ? " " + o.unit : ""}</b> · ${ampm(p[2])}${o.dayLabel ? " · " + o.dayLabel : ""}`, [o.colorFn ? o.colorFn(p[1]) : o.color || C.stress]]), T, H - B);
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}"${sc}>${g}${line}</svg>
    <div class="stats"><span>Min <b>${fmt(mn, o.nd || 0)}</b></span><span>Avg <b>${fmt(av, o.nd || 0)}</b></span><span>Max <b>${fmt(mx, o.nd || 0)}</b></span><span>${o.note ? esc(o.note) : ""}${o.band ? `<i style="display:inline-block;width:8px;height:8px;background:${C.ok};opacity:.5;margin:0 4px 0 8px"></i>${o.bandLabel || "normal range"}` : ""}</span></div></div>`;
}
function trendChart7(trend, o = {}) {
  const pts = trend.filter(p => p[1] != null);
  if (!pts.length) return `<div class="empty">No readings in the last 7 days.</div>`;
  const W = 340, H = 120, L = 28, R = 10, T = 14, B = 20, vals = pts.map(p => p[1]);
  let lo = Math.min(...vals, ...(o.band || [])), hi = Math.max(...vals, ...(o.band || [])); const pad = (hi - lo) * 0.15 || 1; lo -= pad; hi += pad;
  const X = i => L + i / Math.max(1, trend.length - 1) * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let g = o.band ? `<rect x="${L}" width="${W - L - R}" y="${Y(o.band[1])}" height="${Y(o.band[0]) - Y(o.band[1])}" fill="${C.ok}" opacity=".13"/>` : "";
  const xy = trend.map((p, i) => p[1] == null ? null : [X(i), Y(p[1]), p[1]]);
  g += `<polyline points="${xy.filter(Boolean).map(p => p[0] + "," + p[1]).join(" ")}" fill="none" stroke="${o.color}" stroke-width="2"/>`;
  xy.forEach((p, i) => {
    if (p) g += `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="${o.color}"/><text x="${p[0]}" y="${p[1] - 7}" text-anchor="middle" font-size="9" fill="#f2f5f7">${fmt(p[2], o.nd || 0)}</text>`;
    g += `<text x="${X(i)}" y="${H - 5}" text-anchor="middle" font-size="9" fill="${C.mut}">${wd(trend[i][0])}</text>`;
  });
  const mn = Math.min(...vals), mx = Math.max(...vals), av = vals.reduce((a, b) => a + b, 0) / vals.length;
  const sc = scrubAttr(xy.map((p, i) => p && [p[0], [p[1]], `<b>${fmt(p[2], o.nd || 0)}${o.unit ? " " + o.unit : ""}</b> · ${longDay(trend[i][0])}`, [o.color]]).filter(Boolean), T, H - B);
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}"${sc}>${g}</svg>
    <div class="stats"><span>Min <b>${fmt(mn, o.nd || 0)}</b></span><span>Avg <b>${fmt(av, o.nd || 0)}</b></span><span>Max <b>${fmt(mx, o.nd || 0)}</b></span><span>7-day trend</span></div></div>`;
}

const longDay = iso => new Date(iso.slice(0, 10) + "T12:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
/* ---------- press-and-hold scrubbing (Health Monitor charts) ----------
   Each scrubbable <svg> carries data-scrub = [[x, [y...], tooltipHtml, [colour...]], ...] in viewBox units. */
function scrubAttr(points, top, bottom) {
  return points.length ? ` class="scrub" data-top="${top}" data-bot="${bottom}" data-scrub="${esc(JSON.stringify(points))}"` : "";
}
const SCRUB = { svg: null, pts: null, idx: -1, hideT: 0 };
function scrubAt(svg, clientX) {
  if (SCRUB.svg !== svg) { scrubHide(true); SCRUB.svg = svg; SCRUB.pts = JSON.parse(svg.dataset.scrub); }
  clearTimeout(SCRUB.hideT);
  const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal, x = (clientX - r.left) / r.width * vb.width;
  let idx = 0, best = Infinity;
  SCRUB.pts.forEach((p, i) => { const d = Math.abs(p[0] - x); if (d < best) { best = d; idx = i; } });
  const p = SCRUB.pts[idx], top = +svg.dataset.top, bot = +svg.dataset.bot;
  let g = svg.querySelector("g.scrub-g");
  if (!g) { g = document.createElementNS("http://www.w3.org/2000/svg", "g"); g.setAttribute("class", "scrub-g"); svg.appendChild(g); }
  g.innerHTML = `<line x1="${p[0]}" x2="${p[0]}" y1="${top}" y2="${bot}" stroke="rgba(255,255,255,.55)" stroke-width="1"/>` +
    p[1].map((y, i) => y == null ? "" : `<circle cx="${p[0]}" cy="${y}" r="5" fill="${p[3][i] || "#fff"}" stroke="#1a1d20" stroke-width="2"/>`).join("");
  const chart = svg.closest(".chart");
  let tip = chart.querySelector(".scrub-tip");
  if (!tip) { tip = document.createElement("div"); tip.className = "scrub-tip"; chart.appendChild(tip); }
  tip.innerHTML = p[2];
  const px = p[0] / vb.width * r.width, w = tip.offsetWidth, cw = chart.clientWidth;
  tip.style.left = Math.max(0, Math.min(cw - w, px - w / 2)) + "px";
  tip.classList.add("show");
  if (idx !== SCRUB.idx && navigator.vibrate && SCRUB.touch) try { navigator.vibrate(4); } catch (e) {}
  SCRUB.idx = idx;
}
function scrubHide(now) {
  const svg = SCRUB.svg; if (!svg) return;
  const go = () => { svg.querySelector("g.scrub-g")?.remove(); svg.closest(".chart")?.querySelector(".scrub-tip")?.classList.remove("show"); if (SCRUB.svg === svg) { SCRUB.svg = null; SCRUB.idx = -1; } };
  clearTimeout(SCRUB.hideT); now ? go() : SCRUB.hideT = setTimeout(go, 1200);
}
function bindScrub(root) {
  root.addEventListener("pointerdown", e => {
    const svg = e.target.closest("svg.scrub"); if (!svg) return;
    SCRUB.touch = e.pointerType !== "mouse"; svg.setPointerCapture?.(e.pointerId); scrubAt(svg, e.clientX);
  });
  root.addEventListener("pointermove", e => {
    const svg = e.target.closest("svg.scrub") || (SCRUB.svg && SCRUB.svg.hasPointerCapture?.(e.pointerId) ? SCRUB.svg : null);
    if (svg && (e.pointerType === "mouse" || e.buttons || svg.hasPointerCapture?.(e.pointerId))) scrubAt(svg, e.clientX);
  });
  ["pointerup", "pointercancel"].forEach(t => root.addEventListener(t, () => scrubHide()));
  root.addEventListener("pointerout", e => { if (e.pointerType === "mouse" && e.target.closest("svg.scrub") && !e.relatedTarget?.closest?.("svg.scrub")) scrubHide(); });
}

/* ---------- combined Resting HR + overnight HRV (Health Monitor › Resting Heart Rate) ---------- */
const HRV_COL = st => ({ BALANCED: C.green, UNBALANCED: C.yellow, LOW: C.orange, POOR: C.red })[st] || C.hrv;
const stName = st => st === "NONE" ? "Baseline building" : st ? st.charAt(0) + st.slice(1).toLowerCase().replace(/_/g, " ") : null;
let hrHrvDays = 7;
function hrHrvChart(series, o) {
  const pts = series.slice(-hrHrvDays), n = pts.length;
  const rv = pts.map(p => p.rhr).filter(v => v != null), hv = pts.map(p => p.hrv).filter(v => v != null);
  if (!rv.length && !hv.length) return `<div class="empty">No resting HR or HRV in this range.</div>`;
  const W = 340, H = 150, L = 28, R = 30, T = 12, B = 20;
  const span = (a, extra = []) => { let lo = Math.min(...a, ...extra), hi = Math.max(...a, ...extra); const p = (hi - lo) * 0.15 || 2; return [lo - p, hi + p]; };
  const band = o.baseline_low && o.baseline_high ? [o.baseline_low, o.baseline_high] : null;
  const [rlo, rhi] = rv.length ? span(rv) : [40, 70], [hlo, hhi] = hv.length ? span(hv, band || []) : [30, 90];
  const X = i => L + (n === 1 ? (W - L - R) / 2 : i / (n - 1) * (W - L - R));
  const Yr = v => T + (1 - (v - rlo) / (rhi - rlo)) * (H - T - B), Yh = v => T + (1 - (v - hlo) / (hhi - hlo)) * (H - T - B);
  let g = band ? `<rect x="${L}" width="${W - L - R}" y="${Yh(band[1])}" height="${Math.max(1, Yh(band[0]) - Yh(band[1]))}" fill="${C.hrv}" opacity=".12"/>` : "";
  [rhi - (rhi - rlo) * 0.13, rlo + (rhi - rlo) * 0.13].forEach(v => g += `<text x="${L - 4}" y="${Yr(v) + 3}" text-anchor="end" font-size="9" fill="${C.hr}">${fmt(v)}</text>`);
  [hhi - (hhi - hlo) * 0.13, hlo + (hhi - hlo) * 0.13].forEach(v => g += `<text x="${W - R + 4}" y="${Yh(v) + 3}" font-size="9" fill="${C.hrv}">${fmt(v)}</text>`);
  const path = (k, Y) => { let d = "", pen = false; pts.forEach((p, i) => { if (p[k] == null) { pen = false; return; } d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(p[k]).toFixed(1)}`; pen = true; }); return d; };
  g += `<path d="${path("rhr", Yr)}" fill="none" stroke="${C.hr}" stroke-width="2" stroke-linejoin="round"/>`;
  g += `<path d="${path("hrv", Yh)}" fill="none" stroke="${C.hrv}" stroke-width="1.6" stroke-dasharray="4 3" opacity=".8"/>`;
  const r = n > 10 ? 2 : 3.2;
  pts.forEach((p, i) => {
    if (p.rhr != null) g += `<circle cx="${X(i)}" cy="${Yr(p.rhr)}" r="${r}" fill="${C.hr}"/>`;
    if (p.hrv != null) g += `<circle cx="${X(i)}" cy="${Yh(p.hrv)}" r="${r + .4}" fill="${HRV_COL(p.status)}" stroke="#2e3236" stroke-width="1"/>`;
  });
  const step = n > 10 ? Math.ceil(n / 6) : 1;
  pts.forEach((p, i) => { if ((n - 1 - i) % step === 0) g += `<text x="${X(i)}" y="${H - 5}" text-anchor="middle" font-size="9" fill="${C.mut}">${n > 10 ? p.date.slice(5).replace("-", "/") : wd(p.date)}</text>`; });
  const sc = scrubAttr(pts.map((p, i) => [X(i), [p.rhr != null ? Yr(p.rhr) : null, p.hrv != null ? Yh(p.hrv) : null],
    `<span class="d">${longDay(p.date)}</span><span style="color:${C.hr}">RHR <b>${p.rhr ?? "—"}</b> bpm</span><span style="color:${HRV_COL(p.status)}">HRV <b>${p.hrv ?? "—"}</b> ms${p.status ? " · " + stName(p.status) : ""}</span>`,
    [C.hr, HRV_COL(p.status)]]), T, H - B);
  const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}"${sc}>${g}</svg>
    <div class="stats"><span>RHR avg <b>${fmt(avg(rv))}</b></span><span>HRV avg <b>${fmt(avg(hv))}</b></span><span>${n} days</span></div>
    <div class="legend"><span><i style="background:${C.hr}"></i>Resting HR (bpm)</span><span><i style="background:${C.hrv}"></i>Overnight HRV (ms)</span>${band ? `<span><i style="background:${C.hrv};opacity:.35"></i>HRV baseline</span>` : ""}</div></div>`;
}
function hrHrvView(o) {
  if (!o) return "";
  const t = o.today, tone = { green: C.green, yellow: C.yellow, red: C.red }[o.tone] || C.mut;
  return `<div class="hrhrv"><div class="pair">
      <div><div class="k">Resting HR</div><div class="x" style="color:${C.hr}">${t.rhr ?? "—"}<small> bpm</small></div><div class="s">${o.rhr_avg != null ? `${fmt(o.rhr_avg, 1)} avg` : "&nbsp;"}</div></div>
      <div><div class="k">HRV · overnight</div><div class="x" style="color:${HRV_COL(t.status)}">${t.hrv ?? "—"}<small> ms</small></div><div class="s">${t.status ? stName(t.status) : "No status"}${!o.baseline_low && o.hrv_avg != null ? ` · ${fmt(o.hrv_avg, 0)} avg` : ""}${o.baseline_low ? ` · base ${o.baseline_low}–${o.baseline_high}` : ""}${t.hrv_weekly ? ` · 7-day ${t.hrv_weekly}` : ""}</div></div></div>
    ${o.insight ? `<div class="insight"><i style="background:${tone}"></i>${esc(o.insight)}</div>` : ""}
    <div class="seg" role="tablist">${[7, 30].map(d => `<button type="button" data-days="${d}" class="${d === hrHrvDays ? "on" : ""}" aria-selected="${d === hrHrvDays}">${d} DAYS</button>`).join("")}</div>
    <div class="hrhrv-chart">${hrHrvChart(o.series, o)}</div></div>`;
}
function openHrHrv() {
  location.hash = "#health";
  setTimeout(() => { const el = $("acc-rhr"); if (!el) return; if (!el.classList.contains("open")) toggleAcc("rhr"); el.scrollIntoView({ behavior: "smooth", block: "start" }); }, 60);
}

/* ---------- accordion ---------- */
function accordion(id, head, body) {
  return `<div class="acc" id="acc-${id}"><button class="acc-h" aria-expanded="false" onclick="toggleAcc('${id}')">${head}<span class="chev">›</span></button>
    <div class="acc-b"><div class="in">${body}</div></div></div>`;
}
const openAcc = new Set();
function toggleAcc(id) {
  const el = $("acc-" + id), b = el.querySelector(".acc-b"), open = !el.classList.contains("open");
  el.classList.toggle("open", open); el.querySelector(".acc-h").setAttribute("aria-expanded", open);
  open ? openAcc.add(id) : openAcc.delete(id);
  b.style.height = (open ? 0 : b.scrollHeight) + "px";
  requestAnimationFrame(() => { b.style.height = (open ? b.scrollHeight : 0) + "px"; });
  b.addEventListener("transitionend", () => { if (el.classList.contains("open")) b.style.height = "auto"; }, { once: true });
}
function restoreAcc() { openAcc.forEach(id => { const el = $("acc-" + id); if (el) { el.classList.add("open"); el.querySelector(".acc-b").style.height = "auto"; } }); }

/* ---------- icons for My Dashboard ---------- */
const ICON = {
  steps: '<path d="M7 3c2 0 3 2 3 5s-1 5-3 5-3-2-3-5 1-5 3-5zM5 15h4v3a2 2 0 0 1-4 0zM16 7c2 0 3 2 3 5s-1 5-3 5-3-2-3-5 1-5 3-5zM14 19h4v1a2 2 0 0 1-4 0z"/>',
  rhr: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/><path d="M15 13l2 2 2-2"/>',
  hrv: '<path d="M2 12h4l2-5 3 10 3-8 2 3h6"/>',
  vo2max: '<path d="M12 4v7M12 11c-2 0-3-2-5-2s-3 3-3 6 1 4 3 4 3-2 5-4M12 11c2 0 3-2 5-2s3 3 3 6-1 4-3 4-3-2-5-4"/>',
  resp: '<path d="M12 4v7M12 11c-2 0-3-2-5-2s-3 3-3 6 1 4 3 4 3-2 5-4M12 11c2 0 3-2 5-2s3 3 3 6-1 4-3 4-3-2-5-4"/><path d="M8 14h2M14 14h2"/>',
  activity_time: '<path d="M6 8v8M3 10v4M18 8v8M21 10v4M6 12h12"/>',
  z13: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/><path d="M8 11h8M9 14h6"/>',
  z45: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/><path d="M12 8v5M12 15.5v.5"/>',
  kcal: '<path d="M12 3c1 4 5 5 5 10a5 5 0 0 1-10 0c0-3 2-4 2-7 2 1 3 3 3 3s0-3 0-6z"/>',
};
const MOON = '<svg viewBox="0 0 24 24"><path d="M20 15A8 8 0 0 1 9 4a8 8 0 1 0 11 11z"/></svg>';
const BOLT = '<svg viewBox="0 0 24 24"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>';

/* ---------- HOME ---------- */
function renderHome(d) {
  const t = d.today, rec = d.recovery, st = d.strain, sl = t.sleep || {};
  ring($("ring-sleep"), (sl.score || 0) / 100, C.sleep, sl.score ?? "—", "Sleep", sl.score != null);
  ring($("ring-recovery"), (rec.score || 0) / 100, zoneColor(rec.zone), rec.score ?? "—", "Recovery", rec.score != null);
  ring($("ring-strain"), st.score / 21, C.strain, st.score.toFixed(1), "Strain", false);
  const mini = (f, c, l) => `<span class="m">${ringSvg(f, c, 12)}${l}</span>`;
  $("mini").innerHTML = mini((sl.score || 0) / 100, C.sleep, "SLEEP") + mini((rec.score || 0) / 100, zoneColor(rec.zone), "RECOVERY") + mini(st.score / 21, C.strain, "STRAIN");

  const h = d.health_monitor;
  const hmState = !h.total ? ["warn", "–", "NO DATA", "orange"] : h.within_range ? ["ok", "✓", "WITHIN RANGE", "green"] : ["warn", "!", "OUT OF RANGE", "orange"];
  $("hm-card").innerHTML = `<h3>HEALTH MONITOR<i>›</i></h3><div class="st"><span class="badge ${hmState[0]}">${hmState[1]}</span>
    <div><div class="k ${hmState[3]}">${hmState[2]}</div><div class="s">${h.ok}/${h.total} Metrics</div></div></div>`;
  const s = d.stress_monitor;
  $("sm-card").innerHTML = `<h3>STRESS MONITOR<i>›</i></h3>` + (s ? `<div class="st"><span class="badge stress" style="${bandStyle(s.whoop_scale)}">${s.whoop_scale.toFixed(1)}</span>
    <div><div class="k" style="color:${band3(s.whoop_scale).col}">${band3(s.whoop_scale).label}</div><div class="s">${s.at ? ampm(timeOf(s.at)) : "daily avg"}</div></div></div>` : `<div class="s">No stress data</div>`);

  // Today's activities: sleep first, then workouts
  let tiles = "";
  if (sl.total_s) {
    const st0 = sl.start_local, en = sl.end_local;
    tiles += `<div class="atile"><div class="chip sleep">${MOON}${hm(sl.total_s)}</div><div class="name">Sleep</div>
      <div class="times">${st0 ? `<b>[${wd(st0)}.]</b> ${ampm(timeOf(st0))}` : ""}<br>${en ? ampm(timeOf(en)) : ""}</div></div>`;
  }
  t.activities.forEach(a => {
    const s0 = timeOf(a.start_local), e0 = s0 && a.duration_s ? (() => { const m = (toMin(s0) + Math.round(a.duration_s / 60)) % 1440; return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; })() : null;
    const val = a.strain != null ? a.strain.toFixed(1) : a.load != null ? fmt(a.load) : hm(a.duration_s);
    tiles += `<div class="atile"><div class="chip work">${BOLT}${val}</div><div class="name">${esc(a.name || a.type)}</div>
      <div class="times">${s0 ? ampm(s0) : ""}<br>${e0 ? ampm(e0) : ""}</div></div>`;
  });
  $("activities").innerHTML = tiles || `<div class="empty">No sleep or workouts recorded.</div>`;

  renderDashboard();

  $("trend").innerHTML = recStrainChart(d.trend);
}
/* ---------- My Dashboard: WHOOP-style customize (order + hide), saved in localStorage ---------- */
const LS_ORDER = "goopDashOrder", LS_HIDDEN = "goopDashHidden";
const lsGet = (k, def) => { try { const v = JSON.parse(localStorage.getItem(k)); return Array.isArray(v) ? v : def; } catch { return def; } };
let dashEdit = false;

/* Saved order first; rows the server sends that we've never seen (new/unknown) keep server order at the end. */
function orderedRows(rows) {
  const order = lsGet(LS_ORDER, []), pos = new Map(order.map((k, i) => [k, i]));
  const known = rows.filter(r => pos.has(r.key)).sort((a, b) => pos.get(a.key) - pos.get(b.key));
  return known.concat(rows.filter(r => !pos.has(r.key)));
}
/* Save the edited order, re-inserting saved keys absent today (e.g. no VO2 max) after their old predecessor. */
function saveOrder(keys) {
  const old = lsGet(LS_ORDER, []), out = keys.slice();
  old.forEach((k, i) => {
    if (out.includes(k)) return;
    let j = i - 1; while (j >= 0 && !out.includes(old[j])) j--;
    out.splice(j < 0 ? 0 : out.indexOf(old[j]) + 1, 0, k);
  });
  localStorage.setItem(LS_ORDER, JSON.stringify(out));
}
const rowKeys = () => [...$("dashboard").querySelectorAll(".drow[data-key]")].map(el => el.dataset.key);

function renderDashboard() {
  if (!DATA) return;
  const hidden = new Set(lsGet(LS_HIDDEN, []));
  const rows = orderedRows(DATA.my_dashboard).filter(r => dashEdit || !hidden.has(r.key));
  const box = $("dashboard");
  box.classList.toggle("editing", dashEdit);
  $("customize").innerHTML = dashEdit ? "DONE" : 'CUSTOMIZE <span aria-hidden="true">&#9998;</span>';
  $("customize").setAttribute("aria-pressed", dashEdit);
  box.innerHTML = rows.map((r, i) => {
    const off = hidden.has(r.key);
    const edit = dashEdit ? `<span class="handle" aria-label="Drag to reorder ${esc(r.label)}">&#8801;</span>` : "";
    const ctl = dashEdit ? `<span class="ctl">
        <button type="button" class="mv" data-mv="-1" aria-label="Move up" ${i === 0 ? "disabled" : ""}>&#9650;</button>
        <button type="button" class="mv" data-mv="1" aria-label="Move down" ${i === rows.length - 1 ? "disabled" : ""}>&#9660;</button>
        <button type="button" class="eye ${off ? "off" : ""}" aria-label="${off ? "Show" : "Hide"} ${esc(r.label)}" aria-pressed="${!off}">${off ? "HIDDEN" : "SHOWN"}</button></span>` : "";
    return `<div class="drow${off ? " is-hidden" : ""}" data-key="${esc(r.key)}">${edit}<svg class="ic" viewBox="0 0 24 24">${ICON[r.key] || ICON.hrv}</svg>
    <div class="lab">${esc(r.label)}</div><div class="num"><div class="v">${esc(r.display)}<span class="tri ${r.dir ? (r.good ? "good" : "bad") : "none"}">${r.dir === "down" ? "▼" : "▲"}</span></div>
    <div class="a" title="${esc(r.avg_label || "")}">${r.avg_display != null ? esc(r.avg_display) : ""}</div></div>${ctl}</div>`;
  }).join("") || `<div class="empty">${DATA.my_dashboard.length ? "All metrics are hidden. Tap CUSTOMIZE to show some." : "No metrics for this day."}</div>`;
}

$("customize").onclick = () => { dashEdit = !dashEdit; renderDashboard(); };
$("dashboard").addEventListener("click", e => {
  const hr = !dashEdit && e.target.closest('.drow[data-key="rhr"], .drow[data-key="hrv"]');
  if (hr) return openHrHrv();
  const b = e.target.closest("button"); if (!b || !dashEdit) return;
  const key = b.closest(".drow").dataset.key;
  if (b.dataset.mv) {
    const keys = rowKeys(), i = keys.indexOf(key), j = i + Number(b.dataset.mv);
    if (j < 0 || j >= keys.length) return;
    [keys[i], keys[j]] = [keys[j], keys[i]];
    saveOrder(keys); renderDashboard();
    $("dashboard").querySelector(`.drow[data-key="${CSS.escape(key)}"] .mv[data-mv="${b.dataset.mv}"]`)?.focus();
  } else if (b.classList.contains("eye")) {
    const h = new Set(lsGet(LS_HIDDEN, []));
    h.has(key) ? h.delete(key) : h.add(key);
    localStorage.setItem(LS_HIDDEN, JSON.stringify([...h])); renderDashboard();
  }
});

/* Touch/mouse drag via Pointer Events (no library). The handle has touch-action:none so iOS Safari
   doesn't scroll; the row follows the finger and swaps with neighbours as it crosses their midpoints. */
(() => {
  const box = $("dashboard");
  let row = null, startY = 0, lastY = 0, raf = 0, pid = null;
  const pageTop = el => el.getBoundingClientRect().top + window.scrollY;
  function update() {
    if (!row) return;
    row.style.transform = "";
    let moved = true;
    while (moved) {
      moved = false;
      const prev = row.previousElementSibling, next = row.nextElementSibling;
      const before = pageTop(row);
      if (next && lastY > pageTop(next) + next.offsetHeight / 2) { box.insertBefore(next, row); moved = true; }
      else if (prev && lastY < pageTop(prev) + prev.offsetHeight / 2) { box.insertBefore(row, prev); moved = true; }
      if (moved) startY += pageTop(row) - before;
    }
    row.style.transform = `translateY(${lastY - startY}px)`;
  }
  function autoscroll() {  // keep scrolling while the finger rests near the top/bottom edge
    if (!row) return;
    const y = lastY - window.scrollY, edge = 90, bottom = window.innerHeight - 120;
    const v = y < edge ? -Math.ceil((edge - y) / 8) : y > bottom ? Math.ceil((y - bottom) / 8) : 0;
    if (v) { window.scrollBy(0, v); lastY += v; update(); }
    raf = requestAnimationFrame(autoscroll);
  }
  box.addEventListener("pointerdown", e => {
    const h = e.target.closest(".handle"); if (!h || !dashEdit || row) return;
    e.preventDefault();
    row = h.closest(".drow"); pid = e.pointerId;
    h.setPointerCapture(pid);
    startY = lastY = e.clientY + window.scrollY;
    row.classList.add("dragging"); box.classList.add("is-dragging");
    raf = requestAnimationFrame(autoscroll);
  });
  box.addEventListener("pointermove", e => { if (row && e.pointerId === pid) { e.preventDefault(); lastY = e.clientY + window.scrollY; update(); } });
  const end = e => {
    if (!row || e.pointerId !== pid) return;
    cancelAnimationFrame(raf);
    row.style.transform = ""; row.classList.remove("dragging"); box.classList.remove("is-dragging");
    row = null; pid = null;
    saveOrder(rowKeys()); renderDashboard();
  };
  box.addEventListener("pointerup", end);
  box.addEventListener("pointercancel", end);
})();

const lvlCls = l => ({ LOW: "blue", MED: "yellow", MEDIUM: "yellow", HIGH: "orange" })[l] || "";
/* WHOOP-style 0-3 stress bands: LOW 0-<1, MEDIUM 1-<2, HIGH 2-3 (0-3 = Garmin 0-100 x 3/100) */
const BANDS = [
  { key: "low", label: "LOW", lo: 0, hi: 1, col: "#4cc9d8", fill: "rgba(76,201,216,.10)" },
  { key: "medium", label: "MEDIUM", lo: 1, hi: 2, col: "#ffd23f", fill: "rgba(255,210,63,.10)" },
  { key: "high", label: "HIGH", lo: 2, hi: 3, col: "#ff6a3d", fill: "rgba(255,106,61,.12)" },
];
const band3 = x => x < 1 ? BANDS[0] : x < 2 ? BANDS[1] : BANDS[2];
const bandStyle = x => { const b = band3(x); return `background:${b.fill.replace(/[\d.]+\)$/, ".22)")};color:${b.col}`; };
const to3 = series => (series || []).map(([t, v]) => [t, Math.round(v * 3) / 100]);
function recStrainChart(trend) {
  const W = 340, H = 140, pad = 6, bw = (W - pad) / trend.length;
  let g = "", pts = [];
  trend.forEach((t, i) => {
    const x = pad + i * bw + bw * .22, w = bw * .56, h = (t.recovery || 0) / 100 * (H - 40);
    const z = t.recovery >= 67 ? C.green : t.recovery >= 34 ? C.yellow : C.red;
    g += `<rect x="${x}" y="${H - 20 - h}" width="${w}" height="${h}" rx="3" fill="${z}" opacity=".85"/>
      <text x="${x + w / 2}" y="${H - 24 - h}" text-anchor="middle" font-size="9" fill="#f2f5f7">${t.recovery ?? ""}</text>
      <text x="${x + w / 2}" y="${H - 6}" text-anchor="middle" font-size="9" fill="${C.mut}">${wd(t.date)}</text>`;
    pts.push([x + w / 2, H - 20 - (t.strain || 0) / 21 * (H - 40)]);
  });
  g += `<polyline points="${pts.map(p => p.join(",")).join(" ")}" fill="none" stroke="${C.strain}" stroke-width="2.5"/>` + pts.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${C.strain}"/>`).join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}">${g}</svg></div><div class="legend"><span><i style="background:${C.green}"></i>Recovery %</span><span><i style="background:${C.strain}"></i>Strain (0–21)</span></div>`;
}

/* ---------- HEALTH MONITOR ---------- */
const MCOL = { resp: C.resp, spo2: C.spo2, rhr: C.hr, hrv: C.hrv, skin: C.orange };
function renderHealth(d) {
  const h = d.health_monitor, t = d.today, cur = h.current_hr, rhrRow = h.metrics.find(m => m.key === "rhr");
  const isToday = d.is_today;
  let html = `<div class="card hero"><div class="big">${cur ? cur.bpm : "—"}<small>bpm</small></div>
    <div class="sub">${cur ? `${isToday ? "Latest reading" : "Last reading of the day"} · ${ampm(timeOf(cur.at))}` : "No heart-rate readings"}</div></div>`;
  html += accordion("hr", `<div class="t"><div class="n">Heart Rate · all day</div><div class="r">Tap for today's curve</div></div>`,
    seriesChart(t.series.hr, { color: C.hr, unit: "bpm", band: rhrRow && rhrRow.low != null ? [rhrRow.low, rhrRow.high] : null, bandLabel: "resting range" })
    + (d.hr_hrv ? `<button type="button" class="link-row" onclick="openHrHrv()">Resting HR + HRV trend<span>›</span></button>` : ""));
  html += `<div class="summary-line"><span class="chk ${h.within_range ? "ok" : h.total ? "flag" : "na"}">${h.within_range ? "✓" : "!"}</span>${h.ok}/${h.total} WITHIN RANGE</div>`;
  h.metrics.forEach(m => {
    const nd = m.key === "resp" || m.key === "skin" ? 1 : 0;
    const val = m.value == null ? `<span style="font-size:14px;color:${C.mut}">Not recorded</span>`
      : `${m.key === "skin" && m.value > 0 ? "+" : ""}${fmt(m.value, nd)}<small>${m.unit}</small>`;
    const range = m.low != null ? `Range ${fmt(m.low, nd)}–${fmt(m.high, nd)} ${m.unit} · ${m.baseline_days}-day baseline` : "Not enough history for a range";
    const chk = m.ok == null ? `<span class="chk na">–</span>` : m.ok ? `<span class="chk ok">✓</span>` : `<span class="chk flag">!</span>`;
    const band = m.low != null ? [m.low, m.high] : null;
    const hn = t.hrv_night;
    const body = m.key === "rhr" && d.hr_hrv ? hrHrvView(d.hr_hrv)
      : m.key === "hrv" ? (hn && hn.n >= 2
          ? `<div class="hrv-night-h">Last night · ${hn.n} readings every 5 min · ${ampm(hn.sleep_start || hn.start)} – ${ampm(hn.sleep_end || hn.end)}</div>`
            + seriesChart(t.series.hrv, { color: C.hrv, unit: "ms", nd: 0, gapMin: 7, hline: hn.avg, hlineLabel: "night avg", markExtremes: true,
                shade: [hn.sleep_start, hn.sleep_end], note: "overnight" })
          : `<div class="empty">No overnight HRV readings for this night. Garmin only measures HRV while you sleep with the watch on.</div>`)
      : m.series ? seriesChart(t.series[m.series], { color: MCOL[m.key], unit: m.unit, band, nd, note: m.series_note, yMax: m.key === "spo2" ? 100 : null })
      : trendChart7(m.trend, { color: MCOL[m.key], unit: m.unit, band, nd });
    html += accordion(m.key, `${chk}<div class="t"><div class="n">${esc(m.label)}</div><div class="r">${range}</div></div><div class="v">${val}</div>`, body);
  });
  html += `<p class="note">Ranges are your mean ± 2 SD over up to 30 prior days. Skin temperature is Garmin's deviation from your own baseline (newer watches only).</p>`;
  $("health").innerHTML = html;
}
bindScrub($("health"));
$("health").addEventListener("click", e => {
  const b = e.target.closest(".hrhrv .seg button"); if (!b || !DATA?.hr_hrv) return;
  hrHrvDays = +b.dataset.days;
  const box = b.closest(".hrhrv");
  box.querySelectorAll(".seg button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
  box.querySelector(".hrhrv-chart").innerHTML = hrHrvChart(DATA.hr_hrv.series, DATA.hr_hrv);
});

/* ---------- STRESS MONITOR ---------- */
const STRESS_ZONES = BANDS.map(b => [b.lo, b.hi, b.fill]);
function bbCard(bb, demo) {
  if (!bb || bb.current == null) return `<div class="card bbcard"><h2>Body Battery</h2><div class="empty">No Body Battery readings for this day.</div></div>`;
  const v = bb.current, col = v >= 50 ? "#5ad1c9" : v >= 25 ? C.yellow : C.orange;
  return `<div class="card bbcard"><div class="bb-ring"><div class="dial">${ringSvg(v / 100, col, 9)}<div class="val">${v}</div></div></div>
    <div class="bb-info"><h2>Body Battery${demo ? ' <span class="tag">sample</span>' : ""}</h2>
      <div class="bb-row"><span class="up">▲ Charged</span><b>+${bb.charged ?? "—"}</b></div>
      <div class="bb-row"><span class="down">▼ Drained</span><b>−${bb.drained ?? "—"}</b></div>
      <div class="bb-at">${bb.latest_at ? `Latest reading ${ampm(bb.latest_at.length > 5 ? timeOf(bb.latest_at) : bb.latest_at)}` : ""}${bb.high != null ? ` · high ${bb.high} · low ${bb.low}` : ""}</div></div></div>`;
}
function renderStress(d) {
  const s = d.stress_monitor, t = d.today;
  if (!s) { $("stress").innerHTML = `<div class="card empty">No stress readings for this day.</div>` + bbCard(t.body_battery, d.mode === "demo"); return; }
  const x = s.whoop_scale, b = band3(x), tl3 = to3(s.timeline);
  let html = `<div class="card hero"><div class="big" style="color:${b.col}">${x.toFixed(1)}<small>/ 3</small></div>
    <div class="lvl" style="color:${b.col}">${b.label}</div>
    <div class="sub">${s.at ? (d.is_today ? "Latest " : "Last reading ") + ampm(timeOf(s.at)) : "Daily average"} · <span class="g100">Garmin ${s.value}/100${s.avg != null ? ` · day avg ${s.avg}` : ""}</span></div></div>`;
  html += bbCard(t.body_battery, d.mode === "demo");
  html += `<div class="card"><h2>Stress through the day</h2>${seriesChart(tl3, { bars: true, colorFn: v => band3(v).col, yMin: 0, yMax: 3, nd: 1, zones: STRESS_ZONES, grid: [1, 2] })}
    <div class="legend">${BANDS.map(z => `<span><i style="background:${z.col}"></i>${z.label} ${z.lo}–${z.hi === 3 ? 3 : "<" + z.hi}</span>`).join("")}</div></div>`;
  const bd = s.bands, tot = bd ? bd.low + bd.medium + bd.high : 0;
  if (tot) {
    html += `<div class="card"><h2>Time in stress zones</h2><div class="stackbar">${BANDS.map(z => `<div style="width:${100 * bd[z.key] / tot}%;background:${z.col}"></div>`).join("")}</div>
      <div class="durs three">${BANDS.map(z => `<div><div class="v">${hm(bd[z.key])}</div><div class="l" style="color:${z.col}">${z.label}</div></div>`).join("")}</div></div>`;
  }
  const bb = t.body_battery || {};
  html += accordion("stress-series", `<div class="t"><div class="n">Stress</div><div class="r">0–3 scale · LOW &lt;1 · MEDIUM 1–2 · HIGH 2–3</div></div><div class="v" style="color:${b.col}">${x.toFixed(1)}</div>`,
    seriesChart(tl3, { color: C.stress, yMin: 0, yMax: 3, nd: 1, zones: STRESS_ZONES, grid: [1, 2] }));
  html += accordion("bb-series", `<div class="t"><div class="n">Body Battery</div><div class="r">${bb.high != null ? `High ${bb.high} · low ${bb.low}` : "Garmin energy reserve"}</div></div><div class="v">${bb.current ?? "—"}</div>`,
    seriesChart(t.series.bb, { color: C.bb, yMin: 0, yMax: 100 }));
  html += `<p class="note">Garmin measures stress from heart-rate variability (0–100). Goop shows it on a WHOOP-like 0–3 scale (value × 3 ÷ 100); zone times are recomputed from those 0–3 bands.</p>`;
  $("stress").innerHTML = html;
}

/* ---------- Morning Report (Garmin Training Readiness after wake-up + last night + yesterday) ---------- */
const SUNRISE = '<svg viewBox="0 0 24 24"><path d="M3 18h18M6.5 18a5.5 5.5 0 0 1 11 0M12 4v4M4.6 9.6l2 2M19.4 9.6l-2 2M1.5 14h2.5M20 14h2.5M9.5 6.5L12 4l2.5 2.5"/></svg>';
const LVL_COL = { PRIME: "#3ddc84", HIGH: "#3ddc84", MODERATE: "#ffde00", LOW: "#ff9f0a", POOR: "#ff4d5e" };
const pctCol = p => p >= 70 ? "#3ddc84" : p >= 40 ? "#ffde00" : "#ff9f0a";
let AM = null, amOpen = false, amReq = null;
async function loadMorning() {
  const want = day; amReq = want;
  try {
    const r = await fetch("/api/morning" + `?day=${day}`, { headers: key ? { "x-goop-key": key } : {} });
    if (!r.ok) throw new Error(`Server error ${r.status}`);
    const d = await r.json();
    if (amReq !== want) return;
    AM = d; renderMorning(d);
  } catch (e) {
    if (amReq !== want) return;
    AM = null; $("am-card").hidden = false;
    $("am-bar").innerHTML = `<span class="sun">${SUNRISE}</span><span class="txt"><span class="ttl">MORNING REPORT</span><span class="fb">Couldn't load: ${esc(e.message)}</span></span>`;
    $("am-in").innerHTML = "";
  }
}
function renderMorning(d) {
  const rd = d.readiness, m = d.metrics, y = m.yesterday || {}, demo = d.mode === "demo";
  const col = rd ? (LVL_COL[(rd.level || "").toUpperCase()] || "#fff") : "#fff";
  const fb = rd ? (rd.feedback_text || rd.feedback_long_text) : null;
  $("am-card").hidden = false;
  $("am-bar").innerHTML = `<span class="sun">${SUNRISE}</span><span class="txt">
      <span class="ttl">MORNING REPORT${demo ? ' <span class="tag">SAMPLE</span>' : ""}</span>
      ${rd && rd.score != null ? `<span class="line"><span class="sc">${rd.score}</span><span class="lv" style="color:${col}">${esc((rd.level_text || "").toUpperCase())}</span><span class="fb" style="margin:0">${fb ? "· " + esc(fb) : ""}</span></span>`
        : `<span class="fb">${d.partial ? "Training Readiness didn't load" : "No Training Readiness from Garmin for this morning"}</span>`}
    </span><span class="chev">›</span>`;
  const tile = (l, v, u, s, txt) => `<div class="am-tile"><div class="l">${l}</div><div class="v${txt ? " t" : ""}">${v ?? "—"}${v != null && u ? `<small>${u}</small>` : ""}</div><div class="s">${s || "&nbsp;"}</div></div>`;
  const rec = rd && rd.recovery_min != null ? (rd.recovery_phrase === "REACHED_ZERO" || rd.recovery_min === 0 ? "0" : String(Math.round(rd.recovery_min / 60))) : null;
  const hrvSt = m.hrv_status ? m.hrv_status.charAt(0) + m.hrv_status.slice(1).toLowerCase().replace(/_/g, " ") : null;
  let h = `<div class="am-tiles">
    ${tile("Readiness", rd ? rd.score : null, "", rd ? esc(rd.level_text || "") : "")}
    ${tile("Sleep", m.sleep_s ? hm(m.sleep_s) : null, "", m.sleep_score != null ? `score ${m.sleep_score}` : "")}
    ${tile("HRV", m.hrv, "ms", esc(hrvSt || "overnight"))}
    ${tile("BB at wake", m.bb_wake, "", "Body Battery")}
    ${tile("Resting HR", m.rhr, "bpm", "")}
    ${tile("Recovery", rec, "h", "time left")}
    ${tile("Training", m.training_status ? esc(m.training_status) : null, "", m.acute_load != null ? `acute load ${fmt(m.acute_load)}` : "status", true)}
    ${tile("Steps", y.steps != null ? fmt(y.steps) : null, "", "yesterday")}
    ${tile("Calories", y.kcal != null ? fmt(y.kcal) : null, "", "yesterday")}
  </div>`;
  const fs = rd ? rd.factors.filter(f => f.pct != null) : [];
  if (fs.length) {
    h += `<div class="am-h">READINESS FACTORS</div>` + fs.map(f => `<div class="am-f"><div class="n">${esc(f.label)}${f.feedback_text ? `<small>${esc(f.feedback_text)}</small>` : ""}</div>
      <div class="bar"><i style="width:${Math.max(2, Math.min(100, f.pct))}%;background:${pctCol(f.pct)}"></i></div><div class="p">${f.pct}%</div></div>`).join("");
  }
  if (d.summary) h += `<p class="am-sum">${esc(d.summary)}</p>`;
  if (d.partial && d.note) h += `<div class="am-note">${esc(d.note)}</div>`;
  h += `<div class="am-foot">${demo ? "Sample data, not yours. " : ""}Readiness from Garmin's after-wake-up reading${rd && rd.at && rd.at.length >= 16 ? ` (${ampm(rd.at.slice(11, 16))})` : ""}; steps and calories are yesterday's totals.</div>`;
  $("am-in").innerHTML = h;
  setAm(amOpen, false);
}
function setAm(open, animate = true) {
  amOpen = open;
  const b = $("am-body"), bar = $("am-bar");
  bar.setAttribute("aria-expanded", open);
  if (!animate) { b.style.height = open ? "auto" : "0px"; return; }
  b.style.height = (open ? 0 : b.scrollHeight) + "px";
  requestAnimationFrame(() => { b.style.height = (open ? b.scrollHeight : 0) + "px"; });
  b.addEventListener("transitionend", () => { if (amOpen) b.style.height = "auto"; }, { once: true });
}
$("am-bar").onclick = () => setAm(!amOpen);

/* ---------- SLEEP: home coach card + #sleep details (Garmin get_sleep_data; Goop-computed values labelled) ---------- */
const STAGE_COL = { awake: "#f28b9a", rem: "#a68cf0", light: "#7ba1bb", deep: "#3f6fd8" };
const QUAL_COL = { EXCELLENT: "#6eeda5", GOOD: "#a8e06a", FAIR: "#ffde00", POOR: "#f2ab45" };
const qcol = q => QUAL_COL[(q || "").toUpperCase()] || "var(--mut)";
const minHM = m => m == null ? "—" : `${Math.floor(m / 60)}:${String(Math.round(m % 60)).padStart(2, "0")}`;
const GOOP = '<span class="goop-tag" title="Computed by Goop, not Garmin">GOOP</span>';
let SLP = null, slpReq = null;
async function loadSleep() {
  const want = day; slpReq = want;
  try {
    const r = await fetch("/api/sleep" + `?day=${day}`, { headers: key ? { "x-goop-key": key } : {} });
    if (!r.ok) throw new Error(`Server error ${r.status}`);
    const d = await r.json();
    if (slpReq !== want) return;
    SLP = d; renderSleepCard(d); renderSleepPage(d);
  } catch (e) { if (slpReq === want) { $("sleep-card").hidden = false; $("sc-bar").innerHTML = `<span class="sun">${MOON_I}</span><span class="txt"><span class="ttl">SLEEP COACH</span><span class="sc-sub">Couldn't load sleep: ${esc(e.message)}</span></span>`; $("sc-in").innerHTML = ""; } }
}
function sleepBars(hist, H = 64, fill = "#cfe0ec") {
  const pts = hist.filter(h => h.total_s != null || h.need_min != null);
  if (!pts.length) return "";
  const W = 320, top = Math.max(9, ...hist.map(h => ((h.total_s || 0) + (h.nap_s || 0)) / 3600), ...hist.map(h => (h.need_min || 0) / 60)) * 1.08;
  const bw = W / hist.length, Y = v => H - 14 - v / top * (H - 18);
  let g = "";
  hist.forEach((h, i) => {
    const x = i * bw + bw * .2, w = bw * .6, sl = (h.total_s || 0) / 3600, nap = (h.nap_s || 0) / 3600;
    if (sl) g += `<rect x="${x}" y="${Y(sl)}" width="${w}" height="${Y(0) - Y(sl)}" rx="3" fill="${fill}"/>`;
    if (nap) g += `<rect x="${x}" y="${Y(sl + nap)}" width="${w}" height="${Y(sl) - Y(sl + nap)}" rx="2" fill="${fill}" opacity=".45"/>`;
    if (h.need_min) g += `<line x1="${x - 3}" x2="${x + w + 3}" y1="${Y(h.need_min / 60)}" y2="${Y(h.need_min / 60)}" stroke="#fff" stroke-width="1.6" stroke-dasharray="3 2"/>`;
    g += `<text x="${x + w / 2}" y="${H - 2}" text-anchor="middle" font-size="9" fill="${fill === C.sleep ? C.mut : "rgba(255,255,255,.8)"}">${wd(h.date).slice(0, 1)}</text>`;
  });
  return `<svg class="sl-bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg>`;
}
const MOON_I = '<svg viewBox="0 0 24 24"><path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/><path d="M15 4.5h3M16.5 3v3"/></svg>';
let scOpen = false;
function renderSleepCard(d) {
  const c = d.coach || {}, n = d.night, el = $("sleep-card"), demo = d.mode === "demo";
  el.hidden = false;
  const tn = c.tonight_need_min, cons = c.consistency;
  const needTxt = tn != null ? `Need ${Math.floor(tn / 60)}h ${String(tn % 60).padStart(2, "0")}m ${d.is_today ? "tonight" : "next night"}` : "No sleep need from Garmin";
  const head = needTxt + (c.bedtime ? ` · Bed by ${ampm(c.bedtime)}` : "");
  const sub = c.debt_s ? `${hm(c.debt_s)} sleep debt (7 nights)` + (c.chips && c.chips[0] ? ` · ${c.chips[0]}` : "") : (c.chips && c.chips[0]) || (n && n.score != null ? `Last night ${n.score} · ${n.qualifier_text || ""}` : "");
  $("sc-bar").innerHTML = `<span class="sun">${MOON_I}</span><span class="txt">
      <span class="ttl">SLEEP COACH${demo ? ' <span class="tag">SAMPLE</span>' : ""}</span>
      <span class="fb sc-hl">${esc(head)}</span>${sub ? `<span class="sc-sub">${esc(sub)}</span>` : ""}</span><span class="chev">›</span>`;
  $("sc-in").innerHTML = `
    ${(c.chips || []).length ? `<div class="sc-chips">${c.chips.map(x => `<span class="chip-s">${esc(x)}</span>`).join("")}</div>` : ""}
    <div class="sc-tiles">
      <div><div class="l">Bedtime ${GOOP}</div><div class="v">${c.bedtime ? ampm(c.bedtime) : "—"}</div><div class="s">${c.usual_wake ? `wake ~${ampm(c.usual_wake)}` : "no wake data"}</div></div>
      <div><div class="l">Sleep debt ${GOOP}</div><div class="v">${c.debt_s != null ? hm(c.debt_s) : "—"}</div><div class="s">last ${c.debt_nights || 7} nights</div></div>
      <div><div class="l">Consistency ${GOOP}</div><div class="v">${cons ? cons.score : "—"}</div><div class="s">${cons ? `± ${Math.round((cons.sd_bed_min + cons.sd_wake_min) / 2)} min` : "needs 3+ nights"}</div></div>
    </div>
    ${sleepBars(d.history)}
    <div class="sc-leg"><span><i style="background:#cfe0ec"></i>Sleep (+ naps)</span><span><i class="dash"></i>Garmin sleep need</span></div>
    <a class="sc-link" href="#sleep">Full sleep details ›</a>`;
  setSc(scOpen, false);
}
function setSc(open, animate = true) {
  scOpen = open;
  const b = $("sc-body"); $("sc-bar").setAttribute("aria-expanded", open);
  if (!animate) { b.style.height = open ? "auto" : "0px"; return; }
  b.style.height = (open ? 0 : b.scrollHeight) + "px";
  requestAnimationFrame(() => { b.style.height = (open ? b.scrollHeight : 0) + "px"; });
  b.addEventListener("transitionend", () => { if (scOpen) b.style.height = "auto"; }, { once: true });
}
$("sc-bar").onclick = () => setSc(!scOpen);
function hypnogram(levels) {
  if (!levels || !levels.length) return `<div class="empty">Garmin didn't send a stage timeline for this night.</div>`;
  const W = 340, H = 132, L = 40, R = 6, rows = ["awake", "rem", "light", "deep"], rh = 26;
  const t = s => new Date(s).getTime(), x0 = t(levels[0][0]), x1 = Math.max(t(levels[levels.length - 1][1]), x0 + 1);
  const X = v => L + (v - x0) / (x1 - x0) * (W - L - R);
  let g = rows.map((r, i) => `<text x="0" y="${i * rh + 17}" font-size="9.5" fill="${C.mut}" letter-spacing=".08em">${r.toUpperCase()}</text><line x1="${L}" x2="${W - R}" y1="${i * rh + 13}" y2="${i * rh + 13}" stroke="rgba(255,255,255,.06)"/>`).join("");
  levels.forEach(([a, b, st]) => { const i = rows.indexOf(st); if (i < 0) return;
    g += `<rect x="${X(t(a))}" y="${i * rh + 4}" width="${Math.max(1, X(t(b)) - X(t(a)))}" height="18" rx="2" fill="${STAGE_COL[st]}"/>`; });
  [0, .25, .5, .75, 1].forEach((f, i) => { const v = x0 + (x1 - x0) * f; g += `<text x="${X(v)}" y="${H - 4}" font-size="9" fill="${C.mut}" text-anchor="${i === 0 ? "start" : i === 4 ? "end" : "middle"}">${ampm(new Date(v).toTimeString().slice(0, 5))}</text>`; });
  return `<svg class="hypno" viewBox="0 0 ${W} ${H}">${g}</svg>`;
}
function renderSleepPage(d) {
  const n = d.night, el = $("sleep");
  if (!n) { el.innerHTML = `<div class="card"><div class="empty">No sleep recorded by Garmin for this night.</div></div>`; return; }
  const m = n.measures || {}, st = n.stages || {}, demo = d.mode === "demo";
  const tile = (l, v, u, s) => `<div class="mt"><div class="l">${l}</div><div class="v">${v ?? "—"}${v != null && u ? `<small>${u}</small>` : ""}</div><div class="s">${s || "&nbsp;"}</div></div>`;
  const needCard = nd => !nd ? "" : `<div class="need-flow"><div><div class="v">${minHM(nd.baseline_min)}</div><div class="l">baseline</div></div><span class="arr">→</span>
      <div><div class="v">${minHM(nd.actual_min)}</div><div class="l">adjusted${nd.delta_min ? ` (${nd.delta_min > 0 ? "+" : "−"}${Math.abs(nd.delta_min)} min)` : ""}</div></div></div>
    <div class="adj">${Object.entries({ sleepHistoryAdjustment: "Sleep history", trainingFeedback: "Training", hrvAdjustment: "HRV", napAdjustment: "Naps" }).map(([k, lab]) => {
      const v = (nd.raw || {})[k]; const t = v === "INCREASING" ? ["▲ raises need", "up"] : v === "DECREASING" ? ["▼ lowers need", "down"] : v === "NO_DATA" || v == null ? ["no data", "na"] : ["no change", "na"];
      return `<div class="adj-r"><span>${lab}</span><span class="${t[1]}">${t[0]}</span></div>`; }).join("")}</div>
    ${nd.feedback_text ? `<p class="note" style="margin:8px 0 0">Garmin: ${esc(nd.feedback_text)}</p>` : ""}`;
  el.innerHTML = `
    <section class="sl-hero">
      <div class="big" style="color:${qcol(n.qualifier)}">${n.score ?? "—"}</div>
      <div class="q" style="color:${qcol(n.qualifier)}">${esc((n.qualifier_text || "").toUpperCase())}${demo ? ' <span class="tag">SAMPLE</span>' : ""}</div>
      <div class="when">${hm(n.total_s)} asleep · ${n.start_local ? ampm(timeOf(n.start_local)) : "?"} – ${n.end_local ? ampm(timeOf(n.end_local)) : "?"}</div>
      ${n.feedback_text ? `<p class="fb">${esc(n.feedback_text)}</p>` : ""}
      ${[n.insight_text, n.personal_text].filter(Boolean).map(x => `<p class="ins">${esc(x)}</p>`).join("")}
    </section>
    <section class="card"><h2>Sleep stages</h2>${hypnogram(n.levels)}
      <div class="stage-leg">${["deep", "light", "rem", "awake"].map(k => `<span><i style="background:${STAGE_COL[k]}"></i>${k === "rem" ? "REM" : k[0].toUpperCase() + k.slice(1)} <b>${hm(st[k])}</b></span>`).join("")}</div></section>
    <h3 class="fit-h">Sleep score factors · from Garmin</h3>
    ${(n.rows || []).map(r => `<div class="frow"><div class="t"><div class="n">${esc(r.label)}</div><div class="r">${r.optimal ? `Optimal ${esc(r.optimal)}` : "&nbsp;"}</div></div>
      <div class="v"><div class="x">${esc(r.display)}</div>${r.qualifier_text ? `<div class="y" style="color:${qcol(r.qualifier)}">${esc(r.qualifier_text)}</div>` : ""}</div></div>`).join("")}
    <h3 class="fit-h">Overnight</h3>
    <section class="mtiles">
      ${tile("HRV", m.hrv != null ? Math.round(m.hrv) : null, "ms", m.hrv_status ? m.hrv_status.charAt(0) + m.hrv_status.slice(1).toLowerCase() : "overnight avg")}
      ${tile("Breathing", m.resp, "rpm", m.resp_low != null ? `${m.resp_low}–${m.resp_high}` : "")}
      ${tile("SpO₂", m.spo2_avg != null ? Math.round(m.spo2_avg) : null, "%", m.spo2_low != null ? `low ${m.spo2_low}%` : "")}
      ${tile("Avg HR", m.avg_hr != null ? Math.round(m.avg_hr) : null, "bpm", "asleep")}
      ${tile("Resting HR", m.rhr, "bpm", "")}
      ${tile("Body Battery", m.bb_change != null ? `+${m.bb_change}` : null, "", "gained")}
      ${tile("Skin temp", m.skin_temp != null ? `${m.skin_temp > 0 ? "+" : ""}${m.skin_temp.toFixed(1)}` : null, "°C", "vs baseline")}
    </section>
    <h3 class="fit-h">Sleep need · from Garmin</h3>
    <section class="card">${n.need ? `<div class="need-h">Last night</div>${needCard(n.need)}` : `<div class="empty">No sleep need from Garmin.</div>`}
      ${n.next_need ? `<div class="need-h" style="margin-top:14px">Tonight</div>${needCard(n.next_need)}` : ""}</section>
    <h3 class="fit-h">Naps</h3>
    <section class="card">${(n.naps || []).length ? n.naps.map(x => `<div class="nap"><div><b>${hm(x.sec)}</b> <span class="mut">${x.start ? ampm(timeOf(x.start)) : ""}${x.end ? " – " + ampm(timeOf(x.end)) : ""}</span></div>${x.feedback_text ? `<div class="mut">${esc(x.feedback_text)}</div>` : ""}</div>`).join("") : `<div class="empty">No naps recorded.</div>`}</section>
    <h3 class="fit-h">Last 7 nights</h3>
    <section class="card">${sleepBars(d.history, 80, C.sleep)}
      ${d.history.slice().reverse().map(h => `<div class="hist-r"><span>${wd(h.date)} ${h.date.slice(5).replace("-", "/")}</span><span>${hm(h.total_s)}${h.nap_s ? ` <small>+${hm(h.nap_s)} nap</small>` : ""}</span><b style="color:${qcol(h.qualifier)}">${h.score ?? "—"}</b></div>`).join("")}</section>
    <p class="note">${demo ? "Sample data, not yours. " : ""}Score, stages, factors, overnight values and sleep need come from Garmin. Bedtime, sleep debt and consistency on the Home card are Goop calculations: bedtime = usual wake (median of 7 days) − tonight's need; debt = Σ max(0, need − sleep incl. naps) over 7 nights; consistency = 100 − average SD of bed/wake times (90 min SD ⇒ 0).</p>`;
}

/* ---------- standalone (iOS home screen): keep everything in-app ---------- */
const STANDALONE = navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
document.documentElement.classList.toggle("standalone", STANDALONE);
matchMedia("(display-mode: standalone)").addEventListener?.("change", e => document.documentElement.classList.toggle("standalone", e.matches || navigator.standalone === true));
// Every in-app link is a same-origin #hash route, so iOS keeps it inside the home-screen app; never let one open a new
// window (target=_blank would bounce out to Safari).
if (STANDALONE) document.addEventListener("click", e => { const a = e.target.closest("a[target]"); if (a) a.removeAttribute("target"); }, true);

/* ---------- routing (#health / #stress) ---------- */
const VIEWS = ["home", "health", "stress", "sleep", "fitness", "settings", "workout", "library"];
function route() {
  const r = location.hash.replace("#", "") || "home", v = VIEWS.includes(r) ? r : "home";  // #morning = home with the report open
  VIEWS.forEach(n => $("view-" + n).hidden = n !== v);
  document.body.dataset.view = v;
  if (v !== "home") $("mini").classList.remove("show");
  // Health tab = Fitness Age screen; Health/Stress monitors are sub-pages of Home
  $("tab-home").classList.toggle("active", !["fitness", "settings", "workout", "library"].includes(v));
  $("tab-health").classList.toggle("active", v === "fitness");
  $("tab-more").classList.toggle("active", ["settings", "workout", "library"].includes(v));
  if (v === "workout") setTimeout(woInit);
  if (v === "library") setTimeout(libInit);
  if (v === "settings") refreshGarmin();
  if (v === "fitness") { loadFitness(); startBlob(); }
  if (r === "morning") { setAm(true, false); requestAnimationFrame(() => $("am-card").scrollIntoView({ block: "start" })); return; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
let cameFromHome = false; // true when a detail page was opened from Home in this session, so ‹ can just go back
window.addEventListener("hashchange", e => { cameFromHome = !new URL(e.oldURL).hash && !!location.hash; });
document.querySelectorAll(".back").forEach(b => b.addEventListener("click", e => {
  e.preventDefault();
  if (cameFromHome) history.back(); else history.replaceState(null, "", location.pathname + location.search), route();
}));

/* sticky mini rings when the big rings scroll off (home only) */
new IntersectionObserver(([e]) => $("mini").classList.toggle("show", !e.isIntersecting && document.body.dataset.view === "home"),
  { rootMargin: "-40px 0px 0px 0px" }).observe($("rings"));

/* ---------- date switcher (‹ TODAY ›, tappable date opens the picker, ?day= support) ---------- */
const iso = dt => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
const todayIso = () => iso(new Date());
const urlDay = new URLSearchParams(location.search).get("day");
let day = /^\d{4}-\d{2}-\d{2}$/.test(urlDay || "") && urlDay <= todayIso() ? urlDay : todayIso();
const dayLabel = s => s === todayIso() ? "TODAY" : new Date(s + "T12:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }).toUpperCase();
function syncNav() {
  const p = $("picker"); p.max = todayIso(); p.value = day;
  $("next").disabled = day >= todayIso();
  $("date").textContent = dayLabel(day);
  const u = new URL(location.href);
  day === todayIso() ? u.searchParams.delete("day") : u.searchParams.set("day", day);
  u.searchParams.delete("key");
  history.replaceState(null, "", u.pathname + u.search + u.hash);
}
function shift(n) {
  const dt = new Date(day + "T12:00"); dt.setDate(dt.getDate() + n);
  const s = iso(dt); if (s > todayIso()) return;
  day = s; load();
}
async function load() {
  syncNav();
  try {
    const q = `?day=${day}`; // always send the phone's date: the server runs in UTC, so its "today" is tomorrow on evenings in the Americas
    const r = await fetch("/api/dashboard" + q, { headers: key ? { "x-goop-key": key } : {} });
    if (!r.ok) throw new Error(r.status === 401 ? "Unauthorized: open Goop with ?key=YOUR_KEY once" : `Server error ${r.status}`);
    const d = await r.json();
    if (d.date !== day) return; // a newer request superseded this one
    DATA = d;
    $("err").hidden = true;
    setMode(d.mode, d.demo_reason);
    renderHome(d); renderHealth(d); renderStress(d); restoreAcc(); loadMorning(); loadSleep();
    if (document.body.dataset.view === "fitness") loadFitness();
  } catch (e) {
    $("err").hidden = false; $("err").textContent = `Couldn't load data: ${e.message}`;
  }
}
$("prev").onclick = () => shift(-1);
$("next").onclick = () => shift(1);
$("picker").onchange = e => { if (e.target.value) { day = e.target.value > todayIso() ? todayIso() : e.target.value; load(); } };
$("refresh").onclick = load;
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && load());
if ("serviceWorker" in navigator && isSecureContext) navigator.serviceWorker.register("/sw.js");
/* ===================== HEALTH TAB: Fitness Age (#fitness) ===================== */
let FIT = null, fitReq = null;
async function loadFitness() {
  if (FIT && FIT.date === day) return renderFitness(FIT);
  const want = day;
  fitReq = want;
  try {
    const r = await fetch("/api/fitness" + `?day=${day}`, { headers: key ? { "x-goop-key": key } : {} });
    if (!r.ok) throw new Error(`Server error ${r.status}`);
    const d = await r.json();
    if (fitReq !== want) return;
    FIT = d; renderFitness(d); loadEcg(want);
    setMode(d.mode, d.demo_reason);
  } catch (e) { $("fit-note").textContent = `Couldn't load Fitness Age: ${e.message}`; }
}

const ST_LABEL = { optimal: "✓ Optimal", sufficient: "● Sufficient", out: "! Out of Range", missing: "No data" };
function renderFitness(d) {
  // hero
  $("fit-age").textContent = d.goop_age != null ? d.goop_age.toFixed(1) : "—";
  const yy = d.years_younger, del = $("fit-delta");
  del.className = "delta" + (yy != null && yy < 0 ? " older" : "");
  $("fit-src").textContent = d.source === "garmin" ? "from Garmin" : d.source === "demo" ? "sample data" : "";
  del.textContent = yy == null ? (d.goop_age == null ? (d.mode === "garmin" ? "Garmin hasn't calculated a fitness age yet" : "Not enough data yet") : "") :
    Math.abs(yy) < 0.05 ? "same as your age" : `${Math.abs(yy).toFixed(1)} years ${yy > 0 ? "younger" : "older"}`;

  // pace of aging: -1.0x .. 3.0x
  const pace = d.pace, pos = p => (Math.max(-1, Math.min(3, p)) + 1) / 4 * 100;
  const ch = d.pace_change, ph = d.pace_history || {};
  const chTxt = pace == null ? "Building history" : ch == null ? "first week" : Math.abs(ch) < 0.005 ? "no change vs. last week" : `${ch > 0 ? "▲" : "▼"} ${Math.abs(ch).toFixed(2)}x vs. last week`;
  const ticks = Array.from({ length: 61 }, (_, i) => `<i class="${i % 15 === 0 ? "maj" : ""}"></i>`).join("");
  const why = pace != null ? "" : `<p class="pace-why">Building history: needs ${ph.need_points ?? 6} weekly Fitness Age points over ${ph.need_weeks ?? 8}+ weeks with at least one change. Garmin has ${ph.points ?? 0} over ${ph.span_weeks ?? 0} week${ph.span_weeks === 1 ? "" : "s"}${ph.points && !ph.changes ? ", all the same value" : ""}.</p>`;
  $("fit-pace").innerHTML = `<div class="hd"><h2>Pace of Aging <span class="pace-src">· from Garmin Fitness Age</span></h2><span class="pill-note"><i></i>${chTxt}</span></div>
    <p class="pace-exp">1.0x means your Fitness Age is moving at calendar speed; below 1.0x is slower.</p>
    <div class="ends"><span><span class="dot"></span>Slow</span><span>Fast <svg width="14" height="14" viewBox="0 0 24 24"><path d="M12 3c1 4 5 5 5 10a5 5 0 0 1-10 0c0-3 2-4 2-7 2 1 3 3 3 3s0-3 0-6z" fill="none" stroke="#9aa4ad" stroke-width="2"/></svg></span></div>
    <div class="val">${pace != null ? `<b style="left:${Math.min(92, Math.max(8, pos(pace)))}%">${pace.toFixed(1)}x</b>` : `<b style="left:50%;color:#9aa4ad">—</b>`}</div>
    <div class="ticks">${ticks}${pace != null ? `<span class="marker" style="left:${pos(pace)}%"></span>` : ""}</div>
    <div class="tick-lbl"><span style="left:0">-1.0x</span><span style="left:50%">1.0x</span><span style="left:100%">3.0x</span></div>
    ${why}<button class="btn-wide" type="button" id="go-healthspan">GO TO HEALTHSPAN</button>`;
  $("go-healthspan").onclick = () => $("fit-factors").scrollIntoView({ behavior: "smooth", block: "start" });

  // factors summary card with segmented radial gauge
  const c = d.counts, measured = c.optimal + c.sufficient + c.out;
  $("fit-summary").innerHTML = `<div class="hd"><h2>Fitness Age Factors</h2><i>›</i></div><div class="body">
    <div class="counts">
      <span class="st-pill optimal">✓ Optimal</span><span class="n">${c.optimal}</span>
      <span class="st-pill sufficient">● Sufficient</span><span class="n">${c.sufficient}</span>
      <span class="st-pill out">! Out of Range</span><span class="n">${c.out}</span>
    </div>
    <div class="gauge">${gaugeSvg(c)}<div class="c"><b>${measured}</b><span>FACTORS ›</span></div></div></div>`;
  $("fit-summary").onclick = () => $("fit-factors").scrollIntoView({ behavior: "smooth", block: "start" });

  // factor rows
  $("fit-factors").innerHTML = `<div class="fit-h">HEALTHSPAN · CONTRIBUTING FACTORS</div>` + d.factors.map(f => {
    const y = f.years, yc = y == null ? "neutral" : y < -0.05 ? "younger" : y > 0.05 ? "older" : "neutral";
    const yt = y == null ? "" : Math.abs(y) < 0.05 ? "±0.0 yrs" : `${y < 0 ? "−" : "+"}${Math.abs(y).toFixed(1)} yrs`;
    return `<div class="frow"><div class="t"><div class="n">${esc(f.label)}</div><div class="r">Optimal ${esc(f.optimal_range)}${f.garmin && f.garmin.target != null ? ` · Garmin target ${esc(f.garmin.target)}` : ""}${f.garmin && f.garmin.potential_gain != null && f.garmin.potential_gain < -0.05 ? ` · reaching it: ${f.garmin.potential_gain.toFixed(1)} yrs` : ""}</div>
      <div style="margin-top:7px"><span class="st-pill ${f.status}">${ST_LABEL[f.status]}</span></div></div>
      <div class="v"><div class="x">${esc(f.display)}</div><div class="y ${yc}">${yt}</div></div></div>`;
  }).join("");

  // trend (weekly Fitness Age vs chronological age)
  $("fit-trend").innerHTML = ageTrend(d.history, d.chronological_age);
  const src = d.source === "garmin" ? `Fitness Age is Garmin's own value${d.achievable_age != null ? ` (achievable: ${d.achievable_age})` : ""}.`
    : d.source === "demo" ? "DEMO data: sample Fitness Age, not your Garmin."
    : d.mode === "garmin" ? "Garmin hasn't calculated a fitness age yet (it needs a VO₂ max estimate and a few weeks of data)."
    : "No fitness age available.";
  $("fit-note").innerHTML = `${esc(src)}${d.source === "demo" ? ` <a class="link" href="#settings">Connect Garmin in Settings</a>.` : ""} Factor values use Garmin's own components where available; the ± years are Goop's rough estimates. Pace of aging = slope of Garmin's weekly Fitness Age over ~12 weeks, in years per year (1.0x = normal).`;
}

function gaugeSvg(c) {  // full ring of ticks with a gap at the bottom, coloured by status share
  const N = 54, total = c.optimal + c.sufficient + c.out, cols = [];
  const n1 = total ? Math.round(N * c.out / total) : 0, n2 = total ? Math.round(N * c.sufficient / total) : 0;
  for (let i = 0; i < N; i++) cols.push(!total ? "#454c53" : i < n1 ? C.orange : i < n1 + n2 ? "#6fc3d6" : C.ok);
  const start = 140, sweep = 260; // degrees, clockwise from +x; leaves a gap at the bottom
  return `<svg viewBox="0 0 170 170">${cols.map((col, i) => {
    const a = (start + sweep * i / (N - 1)) * Math.PI / 180, r1 = 62, r2 = 78;
    return `<line x1="${85 + r1 * Math.cos(a)}" y1="${85 + r1 * Math.sin(a)}" x2="${85 + r2 * Math.cos(a)}" y2="${85 + r2 * Math.sin(a)}" stroke="${col}" stroke-width="3.2" stroke-linecap="round" opacity="${0.55 + 0.45 * (i % 3 === 0)}"/>`;
  }).join("")}</svg>`;
}

function ageTrend(hist, chrono) {
  if (!hist || hist.length < 2) return `<div class="empty">No Fitness Age history yet.</div>`;
  const W = 340, H = 130, L = 30, R = 8, T = 10, B = 20, vals = hist.map(h => h[1]).concat(chrono != null ? [chrono] : []);
  let lo = Math.min(...vals), hi = Math.max(...vals); const pad = Math.max(0.3, (hi - lo) * 0.15); lo -= pad; hi += pad;
  const X = i => L + i / (hist.length - 1) * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let g = chrono != null ? `<line x1="${L}" x2="${W - R}" y1="${Y(chrono)}" y2="${Y(chrono)}" stroke="${C.mut}" stroke-dasharray="4 4"/><text x="${W - R}" y="${Y(chrono) - 4}" text-anchor="end" font-size="9" fill="${C.mut}">your age ${Math.floor(chrono)}</text>` : "";
  g += `<polyline points="${hist.map((h, i) => `${X(i)},${Y(h[1])}`).join(" ")}" fill="none" stroke="#5fe3a1" stroke-width="2.2"/>`;
  hist.forEach((h, i) => {
    if (i % 3 === 0 || i === hist.length - 1) g += `<text x="${X(i)}" y="${H - 5}" text-anchor="middle" font-size="9" fill="${C.mut}">${new Date(h[0] + "T12:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}</text>`;
  });
  g += `<circle cx="${X(hist.length - 1)}" cy="${Y(hist[hist.length - 1][1])}" r="4" fill="#5fe3a1"/>`;
  [hi - pad, lo + pad].forEach(v => g += `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" font-size="9" fill="${C.mut}">${v.toFixed(1)}</text>`);
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}">${g}</svg></div>`;
}

/* Organic blob: radius = sum of slow sine harmonics; green→blue rim, dark centre, sparkles riding the rim. */
const BLOB = (() => {
  let rnd = 7; const rand = () => (rnd = (rnd * 16807) % 2147483647) / 2147483647;
  const harm = [2, 3, 4, 5].map(k => ({ k, a: 0.035 + rand() * 0.035, ph: rand() * 6.28, w: 0.25 + rand() * 0.35 }));
  const sparks = Array.from({ length: 90 }, () => ({ th: rand() * 6.283, f: 0.62 + Math.pow(rand(), 0.6) * 0.4, s: 0.5 + rand() * 1.9, d: rand() * 2.6 }));
  return { harm, sparks, raf: 0 };
})();
function blobR(th, t) { return 112 * (1 + BLOB.harm.reduce((s, h) => s + h.a * Math.sin(h.k * th + h.ph + h.w * t), 0)); }
function blobPath(t, scale = 1) {
  let d = "";
  for (let i = 0; i <= 120; i++) { const th = i / 120 * 6.2832, r = blobR(th, t) * scale; d += `${i ? "L" : "M"}${(150 + r * Math.cos(th)).toFixed(1)},${(150 + r * Math.sin(th)).toFixed(1)}`; }
  return d + "Z";
}
function initBlob() {
  const svg = $("blob"); if (svg.dataset.ready) return;
  svg.dataset.ready = "1";
  svg.innerHTML = `<defs>
    <linearGradient id="rim" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4be39a"/><stop offset=".55" stop-color="#3fbfa4"/><stop offset="1" stop-color="#4d8fe6"/></linearGradient>
    <radialGradient id="core" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#050807"/><stop offset=".62" stop-color="#060a09"/><stop offset=".86" stop-color="#060a09" stop-opacity=".55"/><stop offset="1" stop-color="#060a09" stop-opacity="0"/></radialGradient>
    <filter id="glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="14"/></filter>
    <filter id="soft"><feGaussianBlur stdDeviation="3"/></filter></defs>
    <path id="b-glow" fill="url(#rim)" opacity=".45" filter="url(#glow)"/>
    <path id="b-rim" fill="url(#rim)"/>
    <path id="b-core" fill="url(#core)" filter="url(#soft)"/>
    <g id="b-sparks">${BLOB.sparks.map((p, i) => `<circle class="sparkle" r="${p.s.toFixed(2)}" fill="${p.f > 0.9 ? "#d8fff0" : "#9ff5cf"}" style="animation-delay:-${p.d.toFixed(2)}s"/>`).join("")}</g>`;
}
function drawBlob(t) {
  $("b-glow").setAttribute("d", blobPath(t, 1.02));
  $("b-rim").setAttribute("d", blobPath(t));
  $("b-core").setAttribute("d", blobPath(t, 0.93));
  const cs = $("b-sparks").children;
  BLOB.sparks.forEach((p, i) => { const r = blobR(p.th, t) * p.f; cs[i].setAttribute("cx", (150 + r * Math.cos(p.th)).toFixed(1)); cs[i].setAttribute("cy", (150 + r * Math.sin(p.th)).toFixed(1)); });
}
function startBlob() {
  initBlob();
  cancelAnimationFrame(BLOB.raf);
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const tick = ms => { drawBlob(ms / 1000); if (!still && document.body.dataset.view === "fitness") BLOB.raf = requestAnimationFrame(tick); };
  BLOB.raf = requestAnimationFrame(tick);
}

/* ---------- ECG card (Health tab) ---------- */
let ECG = null;
async function loadEcg(want) {
  try {
    const r = await fetch("/api/ecg" + `?day=${want}`, { headers: key ? { "x-goop-key": key } : {} });
    if (!r.ok) throw new Error(`Server error ${r.status}`);
    ECG = await r.json(); if (want === day) renderEcg(ECG);
  } catch (e) { $("fit-ecg").innerHTML = `<div class="hd"><h2>ECG</h2></div><div class="empty">Couldn't load ECG: ${esc(e.message)}</div>`; }
}
const ecgCls = c => /sinus/i.test(c) ? "optimal" : /afib|fibrillation/i.test(c) ? "out" : /inconclusive|unclass|poor/i.test(c) ? "missing" : "sufficient";
function waveform(samples, hz) {
  if (!samples || samples.length < 10) return `<div class="empty">No waveform samples in this reading.</div>`;
  const W = 340, H = 110, n = samples.length, lo = Math.min(...samples), hi = Math.max(...samples);
  let grid = "";
  for (let x = 0; x <= W; x += 17) grid += `<line x1="${x}" x2="${x}" y1="0" y2="${H}" stroke="rgba(255,90,106,.12)"/>`;
  for (let y = 0; y <= H; y += 17) grid += `<line x1="0" x2="${W}" y1="${y}" y2="${y}" stroke="rgba(255,90,106,.12)"/>`;
  const pts = samples.map((v, i) => `${(i / (n - 1) * W).toFixed(1)},${(H - 6 - (v - lo) / Math.max(1e-6, hi - lo) * (H - 12)).toFixed(1)}`).join(" ");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}">${grid}<polyline points="${pts}" fill="none" stroke="#ff5a6a" stroke-width="1.4"/></svg>
    <div class="stats"><span>${(n / (hz || 128)).toFixed(1)} s shown</span><span>${hz || "?"} Hz</span></div></div>`;
}
function renderEcg(e) {
  let html = `<div class="hd"><h2>ECG${e.demo ? ' <span class="tag">sample</span>' : ""}</h2></div>`;
  if (e.readings && e.readings.length) {
    html += e.readings.slice(0, 6).map((r, i) => {
      const dt = r.time ? new Date(r.time.slice(0, 10) + "T12:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }) : "—";
      return accordion("ecg" + i, `<div class="t"><div class="n" style="letter-spacing:.04em">${dt} · ${r.time ? ampm(r.time.slice(11, 16)) : ""}</div>
        <div style="margin-top:6px"><span class="st-pill ${ecgCls(r.classification)}">${esc(r.classification)}</span></div></div>
        <div class="v">${r.avg_hr != null ? `${r.avg_hr}<small>bpm</small>` : ""}</div>`, waveform(r.samples, r.sample_rate));
    }).join("");
    if (e.demo) html += `<p class="note" style="margin:6px 0 0">Sample readings and synthetic waveforms, not your data.</p>`;
  } else {
    const f = e.fallback || {};
    html += `<div class="ecg-na">${esc(e.reason || "ECG not available from Garmin for this account.")}</div>
      <div class="fit-h" style="margin:14px 0 8px">Closest metrics</div>
      <div class="ecg-fb"><div><div class="k">Resting HR</div><div class="x">${f.rhr ?? "—"}<small> bpm</small></div><div class="s">7-day avg ${f.rhr_7d ?? "—"}</div></div>
      <div><div class="k">HRV (overnight)</div><div class="x">${f.hrv ?? "—"}<small> ms</small></div><div class="s">7-day avg ${f.hrv_7d ?? "—"}${f.hrv_status ? " · " + esc(String(f.hrv_status).toLowerCase()) : ""}</div></div></div>`;
  }
  $("fit-ecg").innerHTML = html;
}

/* ===================== Settings: Garmin Connect sign-in ===================== */
function setMode(mode, reason) {
  const live = mode === "garmin";
  $("demo").hidden = live;
  $("demo").innerHTML = `DEMO MODE · sample data · <a href="#settings">Connect Garmin in Settings</a>`;
  $("demo").title = reason || "";
  $("live").hidden = !live;
}
const api = (path, body) => fetch(path, {
  method: body ? "POST" : "GET",
  headers: { "content-type": "application/json", ...(key ? { "x-goop-key": key } : {}) },
  body: body ? JSON.stringify(body) : undefined,
}).then(async r => ({ ok: r.ok, status: r.status, data: await r.json().catch(() => ({})) }));

const ago = ts => { if (!ts) return "not yet"; const m = Math.round((Date.now() / 1000 - ts) / 60); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };
function renderGarmin(s) {
  const st = $("g-status");
  if (s.connected) {
    st.className = "g-status on";
    st.innerHTML = `<i class="dot"></i><span>Connected as ${esc(s.name || s.email || "your Garmin account")}</span>`;
    $("g-meta").innerHTML = `${s.email && s.name ? esc(s.email) + " · " : ""}Last sync ${ago(s.last_sync)} · tokens in ${s.token_store === "keychain" ? "Keychain" : "~/.goop (600)"}`;
  } else {
    st.className = "g-status off";
    st.innerHTML = `<i class="dot"></i><span>Not connected · showing sample data</span>`;
    $("g-meta").textContent = s.demo_reason && !/Connect Garmin in Settings/.test(s.demo_reason) ? s.demo_reason : "";
  }
  $("g-connect").textContent = s.connected ? "Reconnect" : "Connect Garmin";
  $("g-disconnect").hidden = !s.connected;
  setMode(s.mode, s.demo_reason);
}
async function refreshGarmin() {
  const r = await api("/api/garmin/status").catch(() => null);
  if (!r) return;
  if (r.ok) renderGarmin(r.data);
  else $("g-meta").textContent = r.data.detail || r.data.error || `Status unavailable (${r.status})`;
}

let pendingId = null;
function openSheet() {
  pendingId = null;
  $("step-creds").hidden = false; $("step-mfa").hidden = true; $("g-err").hidden = true;
  $("sheet-title").textContent = "Sign in to Garmin"; $("g-submit").textContent = "Sign in"; $("g-submit").disabled = false;
  $("g-pass").value = ""; $("g-code").value = "";
  $("sheet").hidden = false; requestAnimationFrame(() => $("sheet").classList.add("open"));
  setTimeout(() => $("g-email").focus(), 250);
}
function closeSheet() {
  $("g-pass").value = ""; $("g-code").value = "";
  $("sheet").classList.remove("open"); setTimeout(() => $("sheet").hidden = true, 250);
}
function sheetError(msg) { $("g-err").textContent = msg; $("g-err").hidden = false; }
async function connected(data) {
  closeSheet(); renderGarmin(data);
  FIT = null; DATA = null; load();  // caches cleared server-side; refetch live data
}
$("g-form").addEventListener("submit", async e => {
  e.preventDefault();
  const btn = $("g-submit"); $("g-err").hidden = true;
  if (pendingId) {
    const code = $("g-code").value.trim();
    if (!code) return sheetError("Enter the code from Garmin.");
    btn.disabled = true; btn.textContent = "Verifying…";
    const r = await api("/api/garmin/mfa", { pending_id: pendingId, code }).catch(() => ({ ok: false, data: { error: "Network error" } }));
    btn.disabled = false; btn.textContent = "Verify";
    if (r.ok && r.data.status === "connected") return connected(r.data);
    if (r.status === 410) { openSheet(); return sheetError(r.data.error); }
    return sheetError(r.data.error || r.data.detail || "Code rejected.");
  }
  const email = $("g-email").value.trim(), password = $("g-pass").value;
  if (!email || !password) return sheetError("Enter your Garmin email and password.");
  btn.disabled = true; btn.textContent = "Signing in…";
  const r = await api("/api/garmin/login", { email, password }).catch(() => ({ ok: false, data: { error: "Network error" } }));
  $("g-pass").value = "";  // don't keep the password in the page
  btn.disabled = false; btn.textContent = "Sign in";
  if (r.ok && r.data.status === "needs_mfa") {
    pendingId = r.data.pending_id;
    $("step-creds").hidden = true; $("step-mfa").hidden = false;
    $("sheet-title").textContent = "Verification code"; btn.textContent = "Verify";
    return setTimeout(() => $("g-code").focus(), 50);
  }
  if (r.ok && r.data.status === "connected") return connected(r.data);
  sheetError(r.data.error || r.data.detail || "Sign-in failed.");
});
$("g-connect").onclick = openSheet;
$("sheet").addEventListener("click", e => { if (e.target.closest("[data-close]")) closeSheet(); });
$("g-disconnect").onclick = async () => {
  if (!confirm("Disconnect Garmin? Goop will delete its saved Garmin tokens and show sample data.")) return;
  const r = await api("/api/garmin/logout", {});
  if (r.ok) { renderGarmin(r.data); FIT = null; DATA = null; load(); }
};

route();
load();

/* ---------- More › Send workout to watch: preview (server token) -> confirm sheet -> send {token, confirm:true} ---------- */
let WO = null;  // last preview
function woInit() {
  if (!$("wo-parts").children.length) woSetParts([{ v: 3, u: "min", z: 4 }]);
  const d = $("wo-date"); if (!d.value) d.value = todayIso();
  d.min = todayIso();
  api("/api/status").then(r => { $("wo-demo").hidden = r.ok && r.data.mode === "garmin"; });
}
function woSpec() {
  const f = new FormData($("wo-form")), o = Object.fromEntries(f.entries());
  ["pv", "pu", "pz"].forEach(k => delete o[k]);
  o.parts = [...$("wo-parts").children].map(r => ({ v: r.querySelector("[name=pv]").value, u: r.querySelector("[name=pu]").value, z: r.querySelector("[name=pz]").value }));
  o.push = $("wo-form").elements.push.checked; return o;
}
const ZOPT = z => `<option value="">No target</option>` + [1, 2, 3, 4, 5].map(n => `<option value="${n}"${+z === n ? " selected" : ""}>HR Z${n}</option>`).join("");
function woPartRow(p = { v: 3, u: "min", z: 4 }) {
  const ride = $("wo-form").elements.sport.value === "cycling";
  const d = document.createElement("div"); d.className = "wo-part";
  d.innerHTML = `<input name="pv" type="number" inputmode="decimal" step="any" min="0.25" value="${esc(p.v)}" aria-label="Amount">
    <select name="pu" aria-label="Unit"><option value="min">min</option>${ride ? "" : `<option value="m"${p.u === "m" ? " selected" : ""}>m</option>`}</select>
    <select name="pz" aria-label="Target">${ZOPT(p.z)}</select><button type="button" class="wo-x" aria-label="Remove part">&times;</button>`;
  return d;
}
function woSetParts(parts) {
  const box = $("wo-parts"); box.innerHTML = ""; parts.forEach(p => box.appendChild(woPartRow(p))); woPartsUi();
}
function woPartsUi() { const n = $("wo-parts").children.length; $("wo-add").hidden = n >= 6; $("wo-parts").classList.toggle("single", n === 1); }
$("wo-add").addEventListener("click", () => { $("wo-parts").appendChild(woPartRow()); woPartsUi(); });
$("wo-parts").addEventListener("click", e => { if (e.target.closest(".wo-x") && $("wo-parts").children.length > 1) { e.target.closest(".wo-part").remove(); woPartsUi(); } });
$("wo-form").addEventListener("change", e => { if (e.target.name === "sport") woSetParts(woSpec().parts.map(p => ({ ...p, u: e.target.value === "cycling" ? "min" : p.u }))); });
const fmtMin = m => m >= 60 ? `${Math.floor(m / 60)} h ${Math.round(m % 60)} min` : `${m} min`;
function woSheet(open) {
  const s = $("wo-sheet");
  if (open) { s.hidden = false; requestAnimationFrame(() => s.classList.add("open")); }
  else { s.classList.remove("open"); setTimeout(() => s.hidden = true, 250); }
}
$("wo-form").addEventListener("submit", async e => {
  e.preventDefault();
  const b = $("wo-preview"); b.disabled = true; $("wo-err").hidden = true;
  const r = await api("/api/workout/preview", woSpec()).catch(() => ({ ok: false, data: { detail: "Goop server unreachable." } }));
  b.disabled = false;
  if (!r.ok) { $("wo-err").textContent = r.data.detail || `Preview failed (${r.status})`; $("wo-err").hidden = false; return; }
  WO = r.data; const w = WO.workout;
  $("wo-s-name").textContent = w.name;
  const dt = new Date(w.date + "T12:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  $("wo-s-meta").textContent = `${w.sport === "cycling" ? "Ride" : "Run"} · ${w.estimated ? "~" : ""}${fmtMin(Math.round(w.total_min))} · ${dt}`;
  $("wo-s-steps").innerHTML = WO.steps.map(s => `<li>${esc(s)}</li>`).join("");
  $("wo-s-actions").innerHTML = `<b>Goop will:</b> 1. save it to Garmin Connect${WO.account ? ` (${esc(WO.account)})` : ""} · 2. schedule it for ${esc(dt)}` + (w.push ? " · 3. push it to your last-used watch" : "");
  $("wo-note").textContent = WO.note;
  $("wo-result").hidden = true; $("wo-result").className = "wo-result";
  const c = $("wo-confirm"); c.hidden = false;
  c.disabled = !WO.can_send; c.textContent = WO.can_send ? "Confirm & send" : (WO.mode === "garmin" ? "Sending only works on your home network" : "Sending disabled in demo mode");
  $("wo-cancel").textContent = "Cancel";
  woSheet(true);
});
$("wo-confirm").addEventListener("click", async () => {
  if (!WO?.can_send) return;
  const c = $("wo-confirm"); c.disabled = true; c.textContent = "Sending…";
  const r = await api("/api/workout/send", { token: WO.token, confirm: true }).catch(() => ({ ok: false, status: 0, data: { detail: "Lost connection to Goop. Check Garmin Connect → Training → Workouts before resending." } }));
  const st = r.data.status, el = $("wo-result");
  el.hidden = false;
  el.className = "wo-result " + (st === "ok" ? "ok" : st === "partial" ? "warn" : "bad");
  el.textContent = (st === "ok" ? "✓ " : st === "partial" ? "⚠ Partly sent. " : "✕ ") + (r.data.message || r.data.detail || `Send failed (${r.status})`);
  WO = null; c.hidden = true;  // token is single-use; a new preview is needed to send again
  $("wo-cancel").textContent = "Done";
});
$("wo-sheet").addEventListener("click", e => { if (e.target.closest("[data-close]")) woSheet(false); });

/* ---------- More › Workout library (#library): static Goop templates -> prefill #workout (same preview/confirm/send flow) ---------- */
let LIB = null, LIB_GOAL = "all";
const ZCOL = { 0: "#4a535b", 1: "#7f8a93", 2: "#4fa3e0", 3: "#16ec06", 4: "#ffb000", 5: "#ff3b4e" };
const PACE = { 0: 5.5, 1: 6.5, 2: 6.0, 3: 5.3, 4: 4.8, 5: 4.3 };
const partMin = p => p.u === "m" ? p.v / 1000 * PACE[p.z || 0] : p.v;
function tplSteps(t) {  // flat [{min, z}] for the mini-graph (warm-up/cool-down/recovery = untargeted)
  const s = []; if (t.warmup_min) s.push({ min: t.warmup_min, z: 0.6 });
  for (let i = 0; i < t.repeats; i++) { t.parts.forEach(p => s.push({ min: partMin(p), z: p.z || 0 })); if (t.recovery_min && i < t.repeats - 1) s.push({ min: t.recovery_min, z: 0.6 }); }
  if (t.cooldown_min) s.push({ min: t.cooldown_min, z: 0.6 });
  return s;
}
function tplGraph(t) {
  const s = tplSteps(t), tot = s.reduce((a, b) => a + b.min, 0), W = 300, H = 40, gap = s.length > 30 ? 0.6 : 1.2;
  let x = 0;
  return `<svg class="lib-graph" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">` + s.map(st => {
    const w = Math.max(1, st.min / tot * W - gap), h = 8 + (Math.max(st.z, 0.6) / 5) * (H - 8), r = `<rect x="${x.toFixed(1)}" y="${(H - h).toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${ZCOL[Math.round(st.z) || 0]}"/>`;
    x += st.min / tot * W; return r;
  }).join("") + "</svg>";
}
function tplTotal(t) { return tplSteps(t).reduce((a, b) => a + b.min, 0); }
const INTENS = z => z >= 5 ? ["Very hard", "vh"] : z >= 4 ? ["Hard", "h"] : z >= 3 ? ["Moderate", "m"] : ["Easy", "e"];
async function libInit() {
  if (!LIB) {
    const r = await api("/api/templates").catch(() => ({ ok: false }));
    if (!r.ok) { $("lib-list").innerHTML = `<p class="note">Couldn't load the library.</p>`; return; }
    LIB = r.data;
  }
  $("lib-chips").innerHTML = [{ key: "all", label: "All" }, ...LIB.goals].map(g => `<button type="button" class="chip${g.key === LIB_GOAL ? " on" : ""}" data-goal="${g.key}">${esc(g.label)}</button>`).join("");
  const gl = Object.fromEntries(LIB.goals.map(g => [g.key, g.label]));
  $("lib-list").innerHTML = [["running", "Run"], ["cycling", "Ride"]].map(([sp, label]) => {
    const ts = LIB.templates.filter(t => t.sport === sp && (LIB_GOAL === "all" || t.goal === LIB_GOAL));
    if (!ts.length) return "";
    return `<h2 class="lib-h">${label}</h2>` + ts.map(t => {
      const zmax = Math.max(...t.parts.map(p => p.z || 0)), [il, ic] = INTENS(zmax), est = t.parts.some(p => p.u === "m");
      return `<button type="button" class="card lib-card" data-id="${t.id}">
        <div class="lib-top"><b>${esc(t.name)}</b><span class="lib-goal">${esc(gl[t.goal])}</span></div>
        <div class="lib-meta"><span class="num">${est ? "~" : ""}${fmtMin(Math.round(tplTotal(t)))}</span> · <span class="int ${ic}">${il}</span> · up to HR Z${zmax}</div>
        ${tplGraph(t)}<p class="lib-p">${esc(t.purpose)}</p></button>`;
    }).join("");
  }).join("");
}
$("lib-chips").addEventListener("click", e => { const b = e.target.closest("[data-goal]"); if (b) { LIB_GOAL = b.dataset.goal; libInit(); } });
$("lib-list").addEventListener("click", e => {
  const c = e.target.closest(".lib-card"); if (!c) return;
  const t = LIB.templates.find(x => x.id === c.dataset.id), f = $("wo-form").elements;
  f.sport.value = t.sport; $("wo-name").value = t.name;
  ["warmup_min", "repeats", "recovery_min", "cooldown_min"].forEach(k => f[k].value = t[k]);
  woSetParts(t.parts);
  $("wo-from").textContent = `From template: ${t.name}. Tweak anything, then review.`; $("wo-from").hidden = false;
  location.hash = "#workout";
});
