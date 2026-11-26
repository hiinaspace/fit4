import { best, bestSplit, complexity } from "../shared/predict.js";

const P = await (await fetch("../data/probe.json")).json();
const D = P.duration, INPUT = "/mnt/pool/videos/2026-11-25 22-05-57.mp4";
const COLORS = ["#4cc9f0", "#f4a261", "#b5e48c", "#c77dff", "#ff6b9d", "#ffd166"];
const LADDER = [{ db: 26.0, x: 2, y: 0 }, { db: 28.0, x: 0, y: 1 }, { db: 29.8, x: 1, y: 1 }, { db: 31.2, x: 2, y: 1 }, { db: 99, x: 0, y: 0, name: "source" }];
const $ = (id) => document.getElementById(id);
const vid = $("vid"), track = $("track");
const r1 = (x) => Math.round(x * 10) / 10;
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const fmt = (x) => x.toFixed(1);

let nextId = 1;
const st = { parts: [], sel: null, limitMiB: 4, audio: false, from: "head", pin: null, curve: null, stopAt: null };
try { const s = JSON.parse(localStorage.getItem("fit4-a") || "null"); if (s?.parts?.length) { st.parts = s.parts; st.limitMiB = s.limitMiB ?? 4; st.audio = !!s.audio; nextId = Math.max(...s.parts.map((p) => p.id)) + 1; } } catch {}
if (!st.parts.length) st.parts = [{ id: nextId++, s: 0, e: 51 }];
st.sel = st.parts[0].id;
const save = () => { try { localStorage.setItem("fit4-a", JSON.stringify({ parts: st.parts, limitMiB: st.limitMiB, audio: st.audio })); } catch {} };
const limit = () => Math.round(st.limitMiB * 1048576);
const colorOf = (p) => COLORS[st.parts.indexOf(p) % COLORS.length];
const selPart = () => st.parts.find((p) => p.id === st.sel);
const pred = (p) => (p.e - p.s >= 0.4 ? best(P, p.s, p.e, limit(), st.audio) : null);

function tier(db) {
  if (db == null) return { cls: "t-na", txt: "cannot fit" };
  if (db < 27) return { cls: "t-bad", txt: "rough: textures smeared" };
  if (db < 29.5) return { cls: "t-warn", txt: "rough: texture mostly gone" };
  if (db < 31) return { cls: "t-ok", txt: "texture readable" };
  return { cls: "t-ok", txt: "close to source" };
}

/* ---------- timeline drawing ---------- */
const X = (t) => (t / D) * 100 + "%";
function buildTrack() {
  const W = track.clientWidth;
  const ruler = $("ruler"); ruler.innerHTML = "";
  for (let t = 0; t < D; t += 10) { const s = document.createElement("span"); s.style.left = X(t); s.textContent = t + "s"; ruler.append(s); }
  const film = $("film"); film.innerHTML = "";
  const n = Math.max(8, Math.round(W / 56)), w = W / n;
  for (let i = 0; i < n; i++) {
    const k = clamp(Math.floor(((i + 0.5) * D) / n) + 1, 1, 120), e = document.createElement("i");
    e.style.width = w + "px"; e.style.backgroundImage = `url(../data/thumbs/${String(k).padStart(3, "0")}.jpg)`; film.append(e);
  }
  const c = $("cost"), dpr = devicePixelRatio || 1, H = 52;
  c.width = W * dpr; c.height = H * dpr;
  const g = c.getContext("2d"); g.scale(dpr, dpr);
  const mx = Math.max(...P.timeline), sw = W / D;
  P.timeline.forEach((v, i) => {
    const f = v / mx, h = Math.max(2, f * (H - 14));
    g.fillStyle = `hsl(${190 - 160 * f} ${55 + 25 * f}% ${28 + 22 * f}%)`;
    g.fillRect(i * sw, H - h, sw + 0.5, h);
  });
  const ay = H - (P.reel_complexity / mx) * (H - 14);
  g.strokeStyle = "#ffffff77"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(0, ay); g.lineTo(W, ay); g.stroke();
  g.setLineDash([]); g.fillStyle = "#ffffffaa"; g.font = "10px system-ui"; g.fillText("content cost per second: taller/hotter = more bytes for the same quality", 6, 11);
}

function renderTrack() {
  const lane = $("lane"), bands = $("bands");
  const seen = new Set();
  for (const p of st.parts) {
    seen.add(p.id);
    let el = lane.querySelector(`[data-id="${p.id}"]`);
    if (!el) {
      el = document.createElement("div"); el.className = "part"; el.dataset.id = p.id;
      el.innerHTML = '<div class="h l"></div><div class="lab"></div><div class="h r"></div>';
      lane.append(el);
      const b = document.createElement("div"); b.className = "band"; b.dataset.id = p.id; bands.append(b);
    }
    const b = bands.querySelector(`[data-id="${p.id}"]`);
    const col = colorOf(p), pr = pred(p), sel = p.id === st.sel;
    for (const e of [el, b]) { e.style.setProperty("--c", col); e.style.left = X(p.s); e.style.width = ((p.e - p.s) / D) * 100 + "%"; e.classList.toggle("sel", sel); }
    el.querySelector(".lab").textContent = `P${st.parts.indexOf(p) + 1} · ${fmt(p.e - p.s)}s · ${pr ? fmt(pr.db) + " dB" : "n/a"}`;
  }
  for (const el of [...lane.children, ...bands.children]) if (!seen.has(+el.dataset.id)) el.remove();
  placeHead();
}
function placeHead() { $("head").style.left = X(vid.currentTime || 0); $("clock").textContent = `${fmt(vid.currentTime || 0)} / ${fmt(D)} s`; }

/* ---------- side panel ---------- */
const cards = new Map();
function buildCards() {
  const box = $("parts"); box.innerHTML = ""; cards.clear();
  st.parts.forEach((p) => {
    const el = document.createElement("div"); el.className = "card";
    el.innerHTML = `<div class="row1"><span class="nm"></span>
      <input class="s" type="number" step="0.1" min="0"> – <input class="e" type="number" step="0.1" min="0">
      <span class="dur"></span><span class="sp"></span><button class="x" title="Remove part">✕</button></div>
      <div class="row2"><span class="db"></span><span class="tier"></span></div>
      <div class="meta"></div>`;
    box.append(el);
    el.onclick = () => { st.sel = p.id; st.pin = null; refresh(); };
    el.querySelector(".x").onclick = (ev) => { ev.stopPropagation(); removePart(p.id); };
    for (const k of ["s", "e"]) {
      const inp = el.querySelector("." + k);
      inp.oninput = () => { const v = parseFloat(inp.value); if (isNaN(v)) return; setRange(p, k === "s" ? v : p.s, k === "e" ? v : p.e, true); };
      inp.onfocus = () => { st.sel = p.id; refresh(true); };
    }
    cards.set(p.id, el);
  });
  if (!st.parts.length) box.innerHTML = '<div class="meta" style="padding:10px">No parts. Drag on the lane below the cost chart, or press + Part.</div>';
}
function updateCards() {
  st.parts.forEach((p, i) => {
    const el = cards.get(p.id); if (!el) return;
    const pr = pred(p), t = tier(pr?.db);
    el.style.setProperty("--c", colorOf(p)); el.classList.toggle("sel", p.id === st.sel);
    el.querySelector(".nm").textContent = "P" + (i + 1);
    for (const k of ["s", "e"]) { const inp = el.querySelector("." + k); if (document.activeElement !== inp) inp.value = fmt(p[k]); }
    el.querySelector(".dur").textContent = fmt(p.e - p.s) + " s";
    const db = el.querySelector(".db"); db.textContent = pr ? fmt(pr.db) + " dB" : "n/a"; db.className = "db " + t.cls;
    const tr = el.querySelector(".tier"); tr.textContent = t.txt; tr.className = "tier " + t.cls;
    el.querySelector(".meta").textContent = pr
      ? `${pr.label} · ${Math.round(pr.kbps)} kbps video${st.audio ? " + audio" : ""} · content ${complexity(P, p.s, p.e).toFixed(2)}× avg`
      : `no codec reaches ${Math.round(limit() / 1024)} KiB for this length`;
  });
}
function renderLadder() {
  const p = selPart(), pr = p && pred(p), db = pr?.db;
  $("ladtitle").textContent = p ? `What ${db != null ? fmt(db) : "n/a"} dB looks like (P${st.parts.indexOf(p) + 1})` : "What dB looks like";
  let near = 0;
  if (db != null) LADDER.forEach((l, i) => { if (Math.abs(Math.min(l.db, 32) - db) < Math.abs(Math.min(LADDER[near].db, 32) - db)) near = i; });
  const show = st.pin ?? near;
  const chips = $("chips"); chips.innerHTML = "";
  LADDER.forEach((l, i) => {
    const b = document.createElement("button"); b.className = "chip" + (i === show ? " on" : i === near && db != null ? " near" : "");
    b.textContent = l.name || fmt(l.db); b.onclick = () => { st.pin = i; renderLadder(); }; chips.append(b);
  });
  const l = LADDER[show];
  for (const id of ["t1", "t2"]) $(id).style.backgroundPosition = `${-l.x * 190}px ${-l.y * 107}px`;
  $("ladnote").textContent = db == null ? "" : show === near ? "nearest ladder tile to this part" : "pinned; nearest is " + (LADDER[near].name || fmt(LADDER[near].db));
}
function renderCmds() {
  const lim = limit();
  const lines = st.parts.map((p, i) => {
    const pr = pred(p), ext = pr?.curve.codec === "x264" ? "mp4" : "webm";
    return `fit4 '${INPUT}' --start ${fmt(p.s)} --end ${fmt(p.e)}${st.audio ? "" : " --no-audio"}${lim !== 4194304 ? " --limit " + lim : ""} -o part${i + 1}.${ext}`;
  });
  $("cmds").textContent = lines.join("\n") || "(no parts)";
}

/* ---------- model ops ---------- */
function setRange(p, s, e, fromInput = false) {
  s = clamp(r1(s), 0, D); e = clamp(r1(e), 0, D);
  if (e - s < 0.5) { if (fromInput) { p.s = s; p.e = Math.max(e, s); refresh(true); return; } if (s !== p.s) s = e - 0.5; else e = s + 0.5; }
  p.s = Math.max(0, s); p.e = Math.min(D, e); refresh(true);
}
function addPart(s, e) {
  const p = { id: nextId++, s: r1(s), e: r1(e) }; st.parts.push(p); st.parts.sort((a, b) => a.s - b.s); st.sel = p.id; st.pin = null; refresh(); return p;
}
function removePart(id) {
  st.parts = st.parts.filter((p) => p.id !== id); if (st.sel === id) st.sel = st.parts[0]?.id ?? null; refresh();
}
/* ---------- quality curve ----------
   For an anchor a (the playhead, or the selected part's start), each x on the
   timeline gets the best predicted dB for encoding [a, x] (x after a) or
   [x, a] (x before a, playhead mode only). Peaks near the anchor, falls off
   with length, and dips over costly content. */
const QMIN = 22, QMAX = 34, BANDS = [[30, "#7bd88f", "30 readable"], [28, "#e9d46b", "28 texture going"], [26, "#f4a261", "26 smeared"], [-1, "#ff6b6b", "below 26"]];
const qcol = (db) => BANDS.find((b) => db >= b[0])[1];
let curveTimer, lastCurveAt = 0;
function anchor() { return st.from === "part" && selPart() ? selPart().s : vid.currentTime || 0; }
function computeCurve() {
  const a = anchor(), step = 0.5, pts = [];
  for (let x = 0; x <= D + 1e-6; x += step) {
    const lo = Math.min(a, x), hi = Math.max(a, x);
    if (st.from === "part" && x < a) { pts.push([x, undefined]); continue; }
    if (hi - lo < 1) { pts.push([x, QMAX]); continue; }
    const r = best(P, lo, hi, limit(), st.audio);
    pts.push([x, r ? r.db : null, r]);
  }
  st.curve = { a, pts };
  drawCurve();
}
function scheduleCurve(now = false) {
  clearTimeout(curveTimer);
  if (now) return computeCurve();
  curveTimer = setTimeout(computeCurve, 40);
}
function drawCurve() {
  const c = $("qc"), W = track.clientWidth, H = 92, dpr = devicePixelRatio || 1;
  if (c.width !== W * dpr) { c.width = W * dpr; c.height = H * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const Y = (db) => H - 4 - ((Math.min(QMAX, Math.max(QMIN, db)) - QMIN) / (QMAX - QMIN)) * (H - 10);
  g.font = "10px system-ui";
  for (const [lv, col] of BANDS.slice(0, 3)) {
    g.strokeStyle = col + "55"; g.setLineDash([3, 4]); g.beginPath(); g.moveTo(0, Y(lv)); g.lineTo(W, Y(lv)); g.stroke();
  }
  g.setLineDash([]);
  const cv = st.curve; if (!cv) return;
  const sx = (t) => (t / D) * W;
  // fill under the curve per segment, coloured by band
  for (let i = 0; i < cv.pts.length - 1; i++) {
    const [x0, d0] = cv.pts[i], [x1, d1] = cv.pts[i + 1];
    if (d0 === undefined || d1 === undefined) continue;
    if (d0 === null || d1 === null) { g.fillStyle = "#ff6b6b22"; g.fillRect(sx(x0), 0, sx(x1) - sx(x0) + 0.5, H); continue; }
    const col = qcol((d0 + d1) / 2);
    g.fillStyle = col + "30"; g.beginPath(); g.moveTo(sx(x0), H); g.lineTo(sx(x0), Y(d0)); g.lineTo(sx(x1), Y(d1)); g.lineTo(sx(x1), H); g.fill();
    g.strokeStyle = col; g.lineWidth = 2; g.beginPath(); g.moveTo(sx(x0), Y(d0)); g.lineTo(sx(x1), Y(d1)); g.stroke();
  }
  g.lineWidth = 1;
  // anchor marker
  g.strokeStyle = "#ffffffaa"; g.beginPath(); g.moveTo(sx(cv.a), 0); g.lineTo(sx(cv.a), H); g.stroke();
  // labels
  g.fillStyle = "#ffffffaa";
  g.fillText(st.from === "part" ? "predicted dB if the selected part ended here" : "predicted dB for a part from the playhead to here (either side)", 6, 11);
  g.textAlign = "right"; for (const [lv, col, name] of BANDS.slice(0, 3)) { g.fillStyle = col; g.fillText(name, W - 4, Y(lv) + 11); } g.textAlign = "left";
  // where the selected part's end sits on the curve
  const p = selPart();
  if (p && st.from === "part") { const pr = pred(p); if (pr) { g.fillStyle = "#fff"; g.beginPath(); g.arc(sx(p.e), Y(pr.db), 3.5, 0, 7); g.fill(); } }
}
function curveReadout(t) {
  const cv = st.curve; if (!cv) return;
  const i = Math.round(t / 0.5), pt = cv.pts[Math.min(cv.pts.length - 1, Math.max(0, i))];
  if (!pt || pt[1] === undefined) { $("fitsout").textContent = ""; return; }
  const lo = Math.min(cv.a, pt[0]), hi = Math.max(cv.a, pt[0]);
  $("fitsout").textContent = pt[1] === null ? `${fmt(lo)}–${fmt(hi)} s (${fmt(hi - lo)} s): no codec fits` : hi - lo < 1 ? "" :
    `${fmt(lo)}–${fmt(hi)} s (${fmt(hi - lo)} s) → ${fmt(pt[1])} dB · ${pt[2].label}`;
}
function refresh(keepCards = false) {
  if (!keepCards || cards.size !== st.parts.length) buildCards();
  st.parts.sort((a, b) => a.s - b.s);
  updateCards(); renderTrack(); renderLadder(); renderCmds(); save();
  scheduleCurve();
}

/* ---------- interaction: track ---------- */
const tAt = (ev) => clamp(((ev.clientX - track.getBoundingClientRect().left) / track.clientWidth) * D, 0, D);
function seek(t) { vid.currentTime = clamp(t, 0, D - 0.05); placeHead(); }
let scrubbing = false;
for (const id of ["cost", "film", "ruler", "qc"]) {
  $(id).addEventListener("pointerdown", (ev) => { scrubbing = true; $(id).setPointerCapture(ev.pointerId); seek(tAt(ev)); });
  $(id).addEventListener("pointermove", (ev) => { if (scrubbing) seek(tAt(ev)); });
  $(id).addEventListener("pointerup", () => { scrubbing = false; scheduleCurve(); });
}
vid.addEventListener("seeked", () => { if (st.from === "head") scheduleCurve(); });
$("qc").addEventListener("mousemove", (ev) => curveReadout(tAt(ev)));
$("qc").addEventListener("mouseleave", () => ($("fitsout").textContent = ""));

$("lane").addEventListener("pointerdown", (ev) => {
  const partEl = ev.target.closest(".part");
  const lane = $("lane"); lane.setPointerCapture(ev.pointerId);
  const t0 = tAt(ev);
  if (partEl) {
    const p = st.parts.find((q) => q.id === +partEl.dataset.id); st.sel = p.id; st.pin = null;
    const mode = ev.target.classList.contains("l") ? "l" : ev.target.classList.contains("r") ? "r" : "m";
    const s0 = p.s, e0 = p.e;
    lane._drag = { mode, p, t0, s0, e0 }; refresh(true);
  } else {
    lane._drag = { mode: "new", t0, p: null };
  }
});
$("lane").addEventListener("pointermove", (ev) => {
  const d = $("lane")._drag; if (!d) return;
  const t = tAt(ev);
  if (d.mode === "new") {
    if (Math.abs(t - d.t0) < 0.4 && !d.p) return;
    if (!d.p) d.p = addPart(Math.min(d.t0, t), Math.max(d.t0, t) + 0.5);
    setRange(d.p, Math.min(d.t0, t), Math.max(d.t0, t));
  } else if (d.mode === "l") setRange(d.p, Math.min(t, d.e0 - 0.5), d.e0);
  else if (d.mode === "r") setRange(d.p, d.s0, Math.max(t, d.s0 + 0.5));
  else { const len = d.e0 - d.s0, s = clamp(d.s0 + t - d.t0, 0, D - len); setRange(d.p, s, s + len); }
});
$("lane").addEventListener("pointerup", (ev) => {
  const d = $("lane")._drag; $("lane")._drag = null;
  if (d && d.mode === "new" && !d.p) { seek(tAt(ev)); st.sel = null; refresh(); }
});

/* ---------- buttons ---------- */
$("play").onclick = () => { st.stopAt = null; vid.paused ? vid.play() : vid.pause(); };
$("playpart").onclick = () => { const p = selPart(); if (!p) return; seek(p.s); st.stopAt = p.e; vid.play(); };
vid.onclick = $("play").onclick;
vid.addEventListener("play", () => ($("play").textContent = "Pause"));
vid.addEventListener("pause", () => { $("play").textContent = "Play"; scheduleCurve(); });
(function tick(now) { if (!vid.paused) { placeHead(); if (st.from === "head" && now - lastCurveAt > 120) { lastCurveAt = now; computeCurve(); } if (st.stopAt != null && vid.currentTime >= st.stopAt) { vid.pause(); st.stopAt = null; } } requestAnimationFrame(tick); })();

$("addpart").onclick = () => { const t = vid.currentTime; addPart(Math.min(t, D - 5), Math.min(D, t + 10)); };
$("limit").value = st.limitMiB; $("audio").checked = st.audio;
$("limit").oninput = () => { const v = parseFloat($("limit").value); if (v > 0) { st.limitMiB = v; refresh(true); } };
$("audio").onchange = () => { st.audio = $("audio").checked; refresh(true); };
for (const b of $("anchor").querySelectorAll("button")) b.onclick = () => {
  st.from = b.dataset.v; for (const o of $("anchor").querySelectorAll("button")) o.classList.toggle("on", o === b); scheduleCurve(true);
};

function split(n) {
  const p = selPart(); if (!p) return;
  const btn = $("split" + n), old = btn.textContent; btn.textContent = "Working…"; btn.disabled = true;
  setTimeout(() => {
    const r = bestSplit(P, p.s, p.e, limit(), n, st.audio, n === 3 ? 1 : 0.5);
    btn.textContent = old; btn.disabled = false;
    if (!r) { btn.title = "No split fits"; $("fitsout").textContent = `no ${n}-way split fits in this range`; return; }
    const edges = [p.s, ...r.cuts, p.e];
    st.parts = st.parts.filter((q) => q !== p);
    for (let i = 0; i < n; i++) st.parts.push({ id: nextId++, s: edges[i], e: edges[i + 1] });
    st.parts.sort((a, b) => a.s - b.s); st.sel = st.parts.find((q) => q.s === edges[0]).id; st.pin = null;
    refresh();
    $("fitsout").textContent = `split ${n} ways: worst part ${fmt(r.worst)} dB`;
  }, 20);
}
$("split2").onclick = () => split(2);
$("split3").onclick = () => split(3);
$("copy").onclick = async () => {
  const txt = $("cmds").textContent;
  try { await navigator.clipboard.writeText(txt); } catch { const r = document.createRange(); r.selectNodeContents($("cmds")); getSelection().removeAllRanges(); getSelection().addRange(r); document.execCommand("copy"); }
  $("copy").textContent = "Copied"; setTimeout(() => ($("copy").textContent = "Copy"), 1200);
};

addEventListener("keydown", (ev) => {
  if (ev.target.matches("input,select,textarea")) return;
  const p = selPart();
  if (ev.code === "Space") { ev.preventDefault(); $("play").click(); }
  else if (ev.key === "i" && p) setRange(p, vid.currentTime, Math.max(p.e, vid.currentTime + 0.5));
  else if (ev.key === "o" && p) setRange(p, Math.min(p.s, vid.currentTime - 0.5), vid.currentTime);
  else if ((ev.key === "Delete" || ev.key === "Backspace") && p) removePart(p.id);
  else if (ev.key === "ArrowLeft") { ev.preventDefault(); seek(vid.currentTime - (ev.shiftKey ? 1 : 0.1)); }
  else if (ev.key === "ArrowRight") { ev.preventDefault(); seek(vid.currentTime + (ev.shiftKey ? 1 : 0.1)); }
});

new ResizeObserver(() => { buildTrack(); renderTrack(); drawCurve(); }).observe(track);
buildTrack(); refresh();
