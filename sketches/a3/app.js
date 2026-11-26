import { best, bestSplit, complexity } from "../shared/predict.js";
import { makeSolver, pick } from "../shared/solve.js";
import * as M from "../shared/marks.js";
import { loadAudio, drawAudio } from "../shared/audio.js";

const P = await (await fetch("../data/probe.json")).json();
const AUDIO = await loadAudio("../data/audio.json");
const D = P.duration, INPUT = "/mnt/pool/videos/2026-11-25 22-05-57.mp4", VIDNAME = "2026-11-25 22-05-57.mp4";
const COLORS = ["#4cc9f0", "#f4a261", "#b5e48c", "#c77dff", "#ff6b9d", "#ffd166"];
const LADDER = [{ db: 26.0, x: 2, y: 0 }, { db: 28.0, x: 0, y: 1 }, { db: 29.8, x: 1, y: 1 }, { db: 31.2, x: 2, y: 1 }, { db: 99, x: 0, y: 0, name: "source" }];
const LAMBDA = 0.02; // tiny per-clip cost: an extra clip must buy at least this much utility
const $ = (id) => document.getElementById(id);
const vid = $("vid"), track = $("track");
const r1 = (x) => Math.round(x * 10) / 10;
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const fmt = (x) => x.toFixed(1);
const X = (t) => (t / D) * 100 + "%";

let nextId = 1, pinN = 1;
const st = {
  parts: [], limitMiB: 4, audio: false, from: "head", pin: null, curve: null, stopAt: null,
  sel: null, selKind: null, selMark: null, selProp: null, // selKind: "part" | "mark" | "prop" | null
  marks: [], maxK: 3, pins: [], layout: null, warn: "", warnHold: "", pend: null, def: { lead: 4, lag: 2 }, demo: false,
};
try {
  const s = JSON.parse(localStorage.getItem("fit4-a3") || "null");
  if (s) {
    st.parts = s.parts || []; st.limitMiB = s.limitMiB ?? 4; st.audio = !!s.audio; st.maxK = clamp(s.maxK ?? 3, 1, 5);
    st.pins = (s.pins || []).slice(0, 8); st.def = { lead: s.def?.lead ?? 4, lag: s.def?.lag ?? 2 };
    if (st.parts.length) nextId = Math.max(...st.parts.map((p) => p.id)) + 1;
    pinN = 1 + st.pins.length;
  }
} catch {}
st.marks = M.loadMarks(VIDNAME);
if (new URLSearchParams(location.search).get("demo") === "1") { st.demo = true; st.marks = M.demoMarks(D); } // ?demo=1 never writes marks to storage
{ const k = parseInt(new URLSearchParams(location.search).get("k")); if (k >= 1 && k <= 5) st.maxK = k; } // ?k=N sets Max clips (for screenshots)
const save = () => {
  try { localStorage.setItem("fit4-a3", JSON.stringify({ parts: st.parts, limitMiB: st.limitMiB, audio: st.audio, maxK: st.maxK, pins: st.pins, def: st.def })); } catch {}
  if (!st.demo) M.saveMarks(VIDNAME, st.marks);
};
const limit = () => Math.round(st.limitMiB * 1048576);
const colorOf = (p) => COLORS[st.parts.indexOf(p) % COLORS.length];
const selPart = () => (st.selKind === "part" ? st.parts.find((p) => p.id === st.sel) : null);
const pred = (p) => (p.e - p.s >= 0.4 ? best(P, p.s, p.e, limit(), st.audio) : null);
const props = () => (st.layout ? st.layout.clips.filter((c) => !c.fixed) : []);

function selectPart(id) { st.sel = id; st.selKind = id == null ? null : "part"; st.selMark = null; st.selProp = null; st.pin = null; }
function selectMark(id) { st.selKind = "mark"; st.selMark = id; st.selProp = null; st.pin = null; }
function selectProp(i) { st.selKind = "prop"; st.selProp = i; st.selMark = null; st.pin = null; }
function selectNone() { st.selKind = null; st.selMark = null; st.selProp = null; }
/** The window the ladder / "from clip start" curve / Play clip refer to: a part or a proposed clip. */
function selWin() {
  const p = selPart();
  if (p) { const pr = pred(p); return { s: p.s, e: p.e, db: pr?.db ?? null, name: "P" + (st.parts.indexOf(p) + 1) }; }
  if (st.selKind === "prop") { const c = props()[st.selProp]; if (c) return { s: c.a, e: c.b, db: c.db, name: "C" + (st.selProp + 1) }; }
  return null;
}

function tier(db) {
  if (db == null) return { cls: "t-na", txt: "cannot fit" };
  if (db < 27) return { cls: "t-bad", txt: "rough: textures smeared" };
  if (db < 29.5) return { cls: "t-warn", txt: "rough: texture mostly gone" };
  if (db < 31) return { cls: "t-ok", txt: "texture readable" };
  return { cls: "t-ok", txt: "close to source" };
}

/* ---------- solver ---------- */
let solver = null, solverKey = "";
function ensureSolver() {
  const key = st.limitMiB + "|" + st.audio;
  if (key !== solverKey) { solver = makeSolver({ probe: P, limitMiB: st.limitMiB, audioKbps: st.audio }); solverKey = key; }
}
const inside = (m, p) => { const r = M.contextRange(m, D); return r && r[0] >= p.s - 1e-6 && r[1] <= p.e + 1e-6; };
// marks already inside an accepted part are not offered to the solver again; parts are fixed clips that count toward Max clips
const freeMarks = () => st.marks.filter((m) => m.type === "boring" || !st.parts.some((p) => inside(m, p)));
function solveWith(pins, prev) {
  ensureSolver();
  const marks = freeMarks();
  const fixed = st.parts.filter((p) => p.e - p.s >= 0.5).map((p) => ({ id: "P" + p.id, a: p.s, b: p.e }));
  const ls = solver.solveK(marks, { kMax: st.maxK, pins: pins.filter((p) => p.a != null || p.b != null), fixed, prev });
  return { lay: pick(ls, LAMBDA), marks };
}
function resolve(fresh = false) {
  const prev = fresh ? null : st.layout;
  let r = solveWith(st.pins, prev);
  st.warn = st.warnHold || "";
  if (!r.lay && st.pins.some((p) => p.a != null || p.b != null)) {
    const r2 = solveWith([], prev);
    if (r2.lay) { r = r2; st.warn = "The pinned edges cannot all be kept with these marks and this max; pins ignored for now."; }
  }
  st.layout = r.lay;
}

/* ---------- marks helpers ---------- */
function markNames() {
  const c = { moment: 0, keep: 0, boring: 0 }, pre = { moment: "M", keep: "K", boring: "B" }, out = {};
  for (const m of st.marks) out[m.id] = pre[m.type] + ++c[m.type];
  return out;
}
/** "prop" (covered by a proposed clip), "part" (inside an accepted part), "out" (left out), "boring". */
function markStatus(m) {
  if (m.type === "boring") return "boring";
  if (st.parts.some((p) => inside(m, p))) return "part";
  if (st.layout && props().some((c) => c.covered.includes(m.id))) return "prop";
  return "out";
}
const markLabelTime = (m) => (m.type === "moment" ? fmt(m.t) + " s" : `${fmt(m.a)}–${fmt(m.b)} s`);

function addMark(mark) { st.marks = M.addMark(st.marks, mark); selectMark(mark.id); refresh(true); }
function addMoment(t) { addMark(M.createMark("moment", { t: clamp(t, 0, D), lead: st.def.lead, lag: st.def.lag })); }
function toggleRange(type) {
  const t = r1(vid.currentTime || 0);
  if (!st.pend || st.pend.type !== type) { st.pend = { type, a: t }; renderToolbar(); renderPend(); return; }
  let a = st.pend.a, b = t; if (b < a) [a, b] = [b, a];
  st.pend = null;
  if (b - a < 0.5) b = Math.min(D, a + 0.5);
  addMark(M.createMark(type, { a, b }));
}
function deleteSelected() {
  if (st.selKind === "mark" && st.selMark) { st.marks = M.removeMark(st.marks, st.selMark); selectNone(); refresh(true); }
  else if (st.selKind === "prop") {
    const c = props()[st.selProp]; if (!c) return;
    const ids = new Set(c.pinned || []); st.pins = st.pins.filter((p) => !ids.has(p.id)); refresh(true);
  } else if (st.selKind === "part") { const p = selPart(); if (p) removePart(p.id); }
}

/* ---------- timeline drawing ---------- */
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
  const c = $("cost"), dpr = devicePixelRatio || 1, H = 30;
  c.width = W * dpr; c.height = H * dpr;
  const g = c.getContext("2d"); g.scale(dpr, dpr);
  const mx = Math.max(...P.timeline), sw = W / D;
  P.timeline.forEach((v, i) => {
    const f = v / mx, h = Math.max(2, f * (H - 10));
    g.fillStyle = `hsl(${190 - 160 * f} ${55 + 25 * f}% ${28 + 22 * f}%)`;
    g.fillRect(i * sw, H - h, sw + 0.5, h);
  });
  const ay = H - (P.reel_complexity / mx) * (H - 10);
  g.strokeStyle = "#ffffff77"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(0, ay); g.lineTo(W, ay); g.stroke();
  g.setLineDash([]); g.fillStyle = "#ffffffaa"; g.font = "10px system-ui"; g.fillText("bytes needed per second: taller / hotter = costlier", 6, 10);
  drawAudio($("aud"), AUDIO, D, { height: 34 });
}

function bracketRange(m) { return m.type === "moment" ? M.contextRange(m, D) : [m.a, m.b]; }
function renderMarks() {
  const ml = $("mlane"), names = markNames(), sel = st.selKind === "mark" ? st.selMark : null;
  const hl = new Set(st.selKind === "prop" ? props()[st.selProp]?.covered || [] : []);
  const items = st.marks.map((m) => ({ m, r: bracketRange(m) })).sort((a, b) => a.r[0] - b.r[0]);
  const ends = []; let html = "";
  for (const { m, r } of items) {
    let row = ends.findIndex((e) => e <= r[0] + 0.15);
    if (row < 0) row = ends.length < 3 ? ends.length : 2;
    ends[row] = Math.max(ends[row] ?? 0, r[1]);
    const stt = markStatus(m);
    const w = Math.max(r[1] - r[0], 0.4);
    const dot = m.type === "moment" ? `<i class="dot" style="left:${((m.t - r[0]) / Math.max(r[1] - r[0], 0.01)) * 100}%"></i>` : "";
    const txt = m.type === "keep" ? `${names[m.id]} · ${fmt(m.b - m.a)} s` : m.type === "boring" ? `${names[m.id]} skip` : names[m.id];
    const tip = `${names[m.id]} ${m.type} ${markLabelTime(m)}${m.type === "moment" ? ` (lead ${m.lead}, lag ${m.lag})` : ""}`;
    html += `<div class="mk ${m.type} st-${stt}${m.id === sel ? " sel" : ""}${hl.has(m.id) ? " cov" : ""}" data-mid="${m.id}" title="${tip}" style="left:${X(r[0])};width:${(w / D) * 100}%;top:${1 + row * 15}px"><i class="h l"></i><span class="ml">${txt}</span>${dot}<i class="h r"></i></div>`;
  }
  const pend = $("pend");
  ml.innerHTML = html; ml.append(pend);
  renderPend();
}
function renderPend() {
  const el = $("pend");
  if (!st.pend) { el.style.display = "none"; return; }
  const a = st.pend.a, b = vid.currentTime || 0, lo = Math.min(a, b), hi = Math.max(a, b);
  el.style.display = "block"; el.className = "mk pend " + st.pend.type;
  el.style.left = X(lo); el.style.width = Math.max(0.3, ((hi - lo) / D) * 100) + "%"; el.style.top = "31px";
  el.textContent = `${st.pend.type} from ${fmt(a)} s: press ${st.pend.type === "keep" ? "K" : "B"} to end`;
}
function renderToolbar() {
  $("bkeep").textContent = st.pend?.type === "keep" ? "End keep (K)" : "Keep (K)";
  $("bboring").textContent = st.pend?.type === "boring" ? "End boring (B)" : "Boring (B)";
  $("bkeep").classList.toggle("on", st.pend?.type === "keep");
  $("bboring").classList.toggle("on", st.pend?.type === "boring");
}

function renderTrack() {
  // marks columns + part/proposal columns behind the lanes
  let b = "";
  const sel = st.selKind === "mark" ? st.selMark : null;
  for (const m of st.marks) { const r = bracketRange(m); b += `<div class="mband ${m.type}${m.id === sel ? " sel" : ""}" style="left:${X(r[0])};width:${(Math.max(r[1] - r[0], 0.2) / D) * 100}%"></div>`; }
  props().forEach((c, i) => { b += `<div class="pband${st.selKind === "prop" && st.selProp === i ? " sel" : ""}" style="left:${X(c.a)};width:${((c.b - c.a) / D) * 100}%"></div>`; });
  for (const p of st.parts) b += `<div class="band${p.id === st.sel && st.selKind === "part" ? " sel" : ""}" style="--c:${colorOf(p)};left:${X(p.s)};width:${((p.e - p.s) / D) * 100}%"></div>`;
  $("bands").innerHTML = b;
  // proposed lane
  $("plane").innerHTML = props().map((c, i) => {
    const t = tier(c.db), pa = pinHas(c, "a"), pb = pinHas(c, "b");
    return `<div class="prop${st.selKind === "prop" && st.selProp === i ? " sel" : ""}${pa ? " pa" : ""}${pb ? " pb" : ""}" data-i="${i}" title="Proposed clip: drag an edge to pin it, drag the body to move it, double-click to accept" style="left:${X(c.a)};width:${((c.b - c.a) / D) * 100}%"><div class="h l"></div><div class="lab"><span>C${i + 1} · <span class="${t.cls}">${c.db != null ? fmt(c.db) + " dB" : "n/a"}</span>${pa || pb ? ' <span class="pn">pinned</span>' : ""}</span></div><div class="h r"></div></div>`;
  }).join("");
  // parts lane
  $("lane").innerHTML = st.parts.map((p) => {
    const pr = pred(p);
    return `<div class="part${p.id === st.sel && st.selKind === "part" ? " sel" : ""}" data-id="${p.id}" style="--c:${colorOf(p)};left:${X(p.s)};width:${((p.e - p.s) / D) * 100}%"><div class="h l"></div><div class="lab">P${st.parts.indexOf(p) + 1} · ${fmt(p.e - p.s)}s · ${pr ? fmt(pr.db) + " dB" : "n/a"}</div><div class="h r"></div></div>`;
  }).join("");
  renderMarks();
  placeHead();
}
function pinHas(c, key) { const ids = new Set(c.pinned || []); return st.pins.some((p) => ids.has(p.id) && p[key] != null); }
function placeHead() { $("head").style.left = X(vid.currentTime || 0); $("clock").textContent = `${fmt(vid.currentTime || 0)} / ${fmt(D)} s`; if (st.pend) renderPend(); }

/* ---------- side panel: plan ---------- */
function costInfo(m, ps) {
  // what would it take to include a left-out mark: its own clip, or growing a proposed clip to reach it
  const step = solver.step, [lo, hi] = M.contextRange(m, D);
  let a = Math.floor(lo / step + 1e-9) * step, b = Math.min(D, Math.ceil(hi / step - 1e-9) * step);
  if (b - a < 3) { b = Math.min(D, a + 3); a = Math.max(0, b - 3); }
  const own = solver.quality(a, b);
  let join = null;
  for (const [i, c] of ps.entries()) {
    const q = solver.quality(Math.min(c.a, lo), Math.max(c.b, hi));
    if (!join || (q && (join.db == null || q.db > join.db))) join = { i, db: q ? q.db : null, from: c.db };
  }
  return { own: own ? own.db : null, join };
}
const dbTxt = (d) => (d == null ? "nothing fits" : fmt(d) + " dB");
function renderPlan() {
  const ps = props(), names = markNames(), lay = st.layout;
  $("kval").textContent = st.maxK; $("kminus").disabled = st.maxK <= 1; $("kplus").disabled = st.maxK >= 5;
  const moments = st.marks.filter((m) => m.type !== "boring");
  const cnt = { prop: 0, part: 0, out: 0 };
  for (const m of moments) cnt[markStatus(m)]++;
  let sum;
  if (!moments.length) sum = "No marks yet. Scrub or play, then press <kbd>M</kbd> on a moment, <kbd>K</kbd> to start and end a keep range, <kbd>B</kbd> for a boring stretch. Or try Demo marks.";
  else {
    const parts = [`${ps.length} proposed${st.parts.length ? " + " + st.parts.length + " accepted" : ""} of max ${st.maxK}`,
      `covers ${cnt.prop + cnt.part} of ${moments.length} marks`];
    if (lay && lay.worstDb != null) parts.push(`worst ${fmt(lay.worstDb)} dB`);
    sum = parts.join(" · ");
    if (lay && lay.boringSec > 0.05) sum += ` · ${fmt(lay.boringSec)} s of boring included`;
  }
  if (st.warn) sum += `<br><span class="warn">${st.warn}</span>`;
  $("plansum").innerHTML = sum;
  $("props").innerHTML = ps.map((c, i) => {
    const t = tier(c.db), sel = st.selKind === "prop" && st.selProp === i;
    const cov = c.covered.length ? c.covered.map((id) => names[id]).join(" ") : "no marks (kept by a pin)";
    const pins = (pinHas(c, "a") ? "start pinned" : "") + (pinHas(c, "a") && pinHas(c, "b") ? ", " : "") + (pinHas(c, "b") ? "end pinned" : "");
    return `<div class="prow${sel ? " sel" : ""}" data-i="${i}"><div class="r1"><b>C${i + 1}</b><span>${fmt(c.a)}–${fmt(c.b)} s</span><span class="dim">${fmt(c.b - c.a)} s</span><span class="sp"></span><span class="pdb ${t.cls}">${c.db != null ? fmt(c.db) + " dB" : "n/a"}</span></div>
      <div class="r2"><span class="tier ${t.cls}">${t.txt}</span><span class="dim">${c.label || ""}</span><span class="sp"></span><button data-acc="${i}">Accept</button></div>
      <div class="r3">covers ${cov}${pins ? " · " + pins : ""}</div></div>`;
  }).join("");
  const out = moments.filter((m) => markStatus(m) === "out");
  let lo = "";
  if (out.length && solver) {
    const full = st.parts.length + ps.length >= st.maxK;
    for (const m of out) {
      const ci = costInfo(m, ps);
      let why = `Own clip: ${dbTxt(ci.own)}${full ? " (no free slot: raise Max clips)" : ""}.`;
      if (ci.join) why += ` Joined into C${ci.join.i + 1}: ${dbTxt(ci.join.db)}${ci.join.from != null ? ` (C${ci.join.i + 1} is ${fmt(ci.join.from)} now)` : ""}.`;
      lo += `<div class="lo"><s>${names[m.id]} · ${markLabelTime(m)}</s> left out<span class="why">${why}</span></div>`;
    }
  }
  $("leftout").innerHTML = lo;
  $("acceptall").disabled = !ps.length;
  const np = st.pins.filter((p) => p.a != null || p.b != null).length;
  $("clearpins").disabled = !np; $("pincount").textContent = np ? `${np} pinned edge${np > 1 ? "s" : ""}` : "";
  renderInspector();
}
const r2x = (v) => String(Math.round(v * 100) / 100);
function renderInspector() {
  const box = $("inspector"), m = st.selKind === "mark" ? M.getMark(st.marks, st.selMark) : null;
  if (!m) { box.innerHTML = '<span class="dim">Select a mark on the timeline to edit its numbers; drag its bracket ends to change its context.</span>'; box.dataset.k = ""; return; }
  const names = markNames(), key = m.id + m.type;
  const F = m.type === "moment" ? [["t", "at"], ["lead", "lead"], ["lag", "lag"], ["weight", "weight"]]
    : m.type === "keep" ? [["a", "from"], ["b", "to"], ["weight", "weight"], ["perSec", "per s"]] : [["a", "from"], ["b", "to"], ["rate", "rate/s"]];
  if (box.dataset.k !== key) {
    box.dataset.k = key;
    box.innerHTML = `<div class="ih"></div><div class="fl">${F.map(([f, l]) => `<label>${l} <input type="number" step="${f === "rate" || f === "perSec" ? 0.01 : 0.1}" data-f="${f}"></label>`).join("")}<button id="delmark">Delete</button></div>`;
    for (const inp of box.querySelectorAll("input")) inp.oninput = () => {
      const v = parseFloat(inp.value); if (!Number.isFinite(v)) return;
      st.marks = M.updateMark(st.marks, m.id, { [inp.dataset.f]: v }); refresh(true);
    };
    $("delmark").onclick = deleteSelected;
  }
  box.querySelector(".ih").textContent = `${names[m.id]} · ${m.type}`;
  for (const inp of box.querySelectorAll("input")) if (document.activeElement !== inp) inp.value = r2x(m[inp.dataset.f]);
}

/* ---------- side panel: parts + ladder (as sketch A) ---------- */
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
    el.onclick = () => { selectPart(p.id); refresh(true); };
    el.querySelector(".x").onclick = (ev) => { ev.stopPropagation(); removePart(p.id); };
    for (const k of ["s", "e"]) {
      const inp = el.querySelector("." + k);
      inp.oninput = () => { const v = parseFloat(inp.value); if (isNaN(v)) return; setRange(p, k === "s" ? v : p.s, k === "e" ? v : p.e, true); };
      inp.onfocus = () => { selectPart(p.id); refresh(true); };
    }
    cards.set(p.id, el);
  });
}
function updateCards() {
  const names = markNames();
  st.parts.forEach((p, i) => {
    const el = cards.get(p.id); if (!el) return;
    const pr = pred(p), t = tier(pr?.db);
    el.style.setProperty("--c", colorOf(p)); el.classList.toggle("sel", p.id === st.sel && st.selKind === "part");
    el.querySelector(".nm").textContent = "P" + (i + 1);
    for (const k of ["s", "e"]) { const inp = el.querySelector("." + k); if (document.activeElement !== inp) inp.value = fmt(p[k]); }
    el.querySelector(".dur").textContent = fmt(p.e - p.s) + " s";
    const db = el.querySelector(".db"); db.textContent = pr ? fmt(pr.db) + " dB" : "n/a"; db.className = "db " + t.cls;
    const tr = el.querySelector(".tier"); tr.textContent = t.txt; tr.className = "tier " + t.cls;
    const cov = st.marks.filter((m) => m.type !== "boring" && inside(m, p)).map((m) => names[m.id]).join(" ");
    el.querySelector(".meta").textContent = pr
      ? `${pr.label} · ${Math.round(pr.kbps)} kbps video${st.audio ? " + audio" : ""} · content ${complexity(P, p.s, p.e).toFixed(2)}× avg${cov ? " · covers " + cov : ""}`
      : `no codec reaches ${Math.round(limit() / 1024)} KiB for this length`;
  });
}
function renderLadder() {
  const w = selWin(), db = w?.db;
  $("ladtitle").textContent = w ? `What ${db != null ? fmt(db) : "n/a"} dB looks like (${w.name})` : "What dB looks like";
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
  $("ladnote").textContent = db == null ? "" : show === near ? "nearest ladder tile to this clip" : "pinned; nearest is " + (LADDER[near].name || fmt(LADDER[near].db));
}
function renderCmds() {
  const lim = limit();
  const lines = st.parts.map((p, i) => {
    const pr = pred(p), ext = pr?.curve.codec === "x264" ? "mp4" : "webm";
    return `fit4 '${INPUT}' --start ${fmt(p.s)} --end ${fmt(p.e)}${st.audio ? "" : " --no-audio"}${lim !== 4194304 ? " --limit " + lim : ""} -o part${i + 1}.${ext}`;
  });
  $("cmds").textContent = lines.join("\n") || "(no parts yet: accept proposed clips, or drag a part on the parts lane)";
}

/* ---------- model ops ---------- */
function setRange(p, s, e, fromInput = false) {
  s = clamp(r1(s), 0, D); e = clamp(r1(e), 0, D);
  if (e - s < 0.5) { if (fromInput) { p.s = s; p.e = Math.max(e, s); refresh(true); return; } if (s !== p.s) s = e - 0.5; else e = s + 0.5; }
  p.s = Math.max(0, s); p.e = Math.min(D, e); refresh(true);
}
function addPart(s, e) {
  const p = { id: nextId++, s: r1(s), e: r1(e) }; st.parts.push(p); st.parts.sort((a, b) => a.s - b.s); selectPart(p.id); refresh(); return p;
}
function removePart(id) {
  st.parts = st.parts.filter((p) => p.id !== id);
  if (st.sel === id) selectPart(st.parts[0]?.id ?? null);
  refresh();
}
function acceptClips(list) {
  if (!list.length) return;
  const ids = new Set(list.flatMap((c) => c.pinned || []));
  st.pins = st.pins.filter((p) => !ids.has(p.id));
  let last = null;
  for (const c of list) { last = { id: nextId++, s: c.a, e: c.b }; st.parts.push(last); }
  st.parts.sort((a, b) => a.s - b.s); selectPart(last.id); refresh();
}

/* ---------- quality curve (as sketch A) ---------- */
const QMIN = 22, QMAX = 34, BANDS = [[30, "#7bd88f", "30 dB"], [28, "#e9d46b", "28 dB"], [26, "#f4a261", "26 dB"], [-1, "#ff6b6b", "below 26"]];
const qcol = (db) => BANDS.find((b) => db >= b[0])[1];
let curveTimer, lastCurveAt = 0;
function anchor() { const w = selWin(); return st.from === "part" && w ? w.s : vid.currentTime || 0; }
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
  const c = $("qc"), W = track.clientWidth, H = 60, dpr = devicePixelRatio || 1;
  if (c.width !== Math.round(W * dpr)) { c.width = Math.round(W * dpr); c.height = H * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const Y = (db) => H - 4 - ((Math.min(QMAX, Math.max(QMIN, db)) - QMIN) / (QMAX - QMIN)) * (H - 8);
  g.font = "10px system-ui";
  for (const [lv, col] of BANDS.slice(0, 3)) {
    g.strokeStyle = col + "55"; g.setLineDash([3, 4]); g.beginPath(); g.moveTo(0, Y(lv)); g.lineTo(W, Y(lv)); g.stroke();
  }
  g.setLineDash([]);
  const cv = st.curve; if (!cv) return;
  const sx = (t) => (t / D) * W;
  for (let i = 0; i < cv.pts.length - 1; i++) {
    const [x0, d0] = cv.pts[i], [x1, d1] = cv.pts[i + 1];
    if (d0 === undefined || d1 === undefined) continue;
    if (d0 === null || d1 === null) { g.fillStyle = "#ff6b6b22"; g.fillRect(sx(x0), 0, sx(x1) - sx(x0) + 0.5, H); continue; }
    const col = qcol((d0 + d1) / 2);
    g.fillStyle = col + "30"; g.beginPath(); g.moveTo(sx(x0), H); g.lineTo(sx(x0), Y(d0)); g.lineTo(sx(x1), Y(d1)); g.lineTo(sx(x1), H); g.fill();
    g.strokeStyle = col; g.lineWidth = 2; g.beginPath(); g.moveTo(sx(x0), Y(d0)); g.lineTo(sx(x1), Y(d1)); g.stroke();
  }
  g.lineWidth = 1;
  g.strokeStyle = "#ffffffaa"; g.beginPath(); g.moveTo(sx(cv.a), 0); g.lineTo(sx(cv.a), H); g.stroke();
  g.fillStyle = "#ffffffaa";
  g.fillText(st.from === "part" ? "predicted dB if the selected clip ended here" : "predicted dB for a clip from the playhead to here (either side)", 6, 11);
  g.textAlign = "right"; for (const [lv, col, name] of BANDS.slice(0, 3)) { g.fillStyle = col; g.fillText(name, W - 4, Y(lv) + 10); } g.textAlign = "left";
  const w = selWin();
  if (w && st.from === "part" && w.db != null) { g.fillStyle = "#fff"; g.beginPath(); g.arc(sx(w.e), Y(w.db), 3.5, 0, 7); g.fill(); }
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
  resolve();
  if (st.selKind === "prop" && !props()[st.selProp]) selectNone();
  updateCards(); renderTrack(); renderPlan(); renderToolbar(); renderLadder(); renderCmds(); save();
  scheduleCurve();
}

/* ---------- interaction: track ---------- */
const tAt = (ev) => clamp(((ev.clientX - track.getBoundingClientRect().left) / track.clientWidth) * D, 0, D);
function seek(t) { vid.currentTime = clamp(t, 0, D - 0.05); placeHead(); }
let scrubbing = false;
for (const id of ["cost", "film", "ruler", "qc", "aud"]) {
  $(id).addEventListener("pointerdown", (ev) => { scrubbing = true; $(id).setPointerCapture(ev.pointerId); seek(tAt(ev)); });
  $(id).addEventListener("pointermove", (ev) => { if (scrubbing) seek(tAt(ev)); });
  $(id).addEventListener("pointerup", () => { scrubbing = false; scheduleCurve(); });
}
vid.addEventListener("seeked", () => { if (st.from === "head") scheduleCurve(); });
$("qc").addEventListener("mousemove", (ev) => curveReadout(tAt(ev)));
$("qc").addEventListener("mouseleave", () => ($("fitsout").textContent = ""));

/* marks lane: drag bracket ends / bodies */
$("mlane").addEventListener("pointerdown", (ev) => {
  const el = ev.target.closest(".mk[data-mid]"), ml = $("mlane");
  ml.setPointerCapture(ev.pointerId);
  const t = tAt(ev);
  if (!el) { selectNone(); seek(t); ml._drag = { mode: "seek" }; renderMarks(); renderPlan(); renderLadder(); return; }
  const m = M.getMark(st.marks, el.dataset.mid); if (!m) return;
  selectMark(m.id);
  const mode = ev.target.classList.contains("l") ? "l" : ev.target.classList.contains("r") ? "r" : "m";
  ml._drag = { mode, id: m.id, t0: t, m0: { ...m } };
  renderMarks(); renderPlan(); renderLadder();
});
$("mlane").addEventListener("pointermove", (ev) => {
  const d = $("mlane")._drag; if (!d) return;
  const t = tAt(ev);
  if (d.mode === "seek") { seek(t); return; }
  const m0 = d.m0; let patch;
  if (m0.type === "moment") {
    if (d.mode === "l") patch = { lead: Math.max(0, r1(m0.t - t)) };
    else if (d.mode === "r") patch = { lag: Math.max(0, r1(t - m0.t)) };
    else patch = { t: clamp(r1(m0.t + t - d.t0), 0, D) };
  } else {
    const len = m0.b - m0.a;
    if (d.mode === "l") patch = { a: Math.min(r1(t), m0.b - 0.5) };
    else if (d.mode === "r") patch = { b: Math.max(r1(t), m0.a + 0.5) };
    else { const a = clamp(r1(m0.a + t - d.t0), 0, D - len); patch = { a, b: a + len }; }
  }
  st.marks = M.updateMark(st.marks, d.id, patch); refresh(true);
});
$("mlane").addEventListener("pointerup", () => { $("mlane")._drag = null; });
$("mlane").addEventListener("pointercancel", () => { $("mlane")._drag = null; });

/* proposed lane: dragging an edge pins it (grid 0.5 s), dragging the body pins both edges, a click on a pinned edge unpins it */
function claimPin(c, keys) {
  const ids = new Set(c.pinned || []);
  for (const p of st.pins) if (ids.has(p.id)) for (const k of keys) delete p[k];
  st.pins = st.pins.filter((p) => p.a != null || p.b != null);
  let keep = st.pins.find((p) => ids.has(p.id));
  if (!keep) { keep = { id: "pin" + pinN++ }; st.pins.push(keep); }
  while (st.pins.length > 8) { const i = st.pins.findIndex((p) => p !== keep); st.pins.splice(i, 1); }
  return keep.id;
}
$("plane").addEventListener("pointerdown", (ev) => {
  const el = ev.target.closest(".prop"), pl = $("plane");
  pl.setPointerCapture(ev.pointerId);
  const t = tAt(ev);
  if (!el) { selectNone(); seek(t); pl._drag = { mode: "seek" }; refresh(true); return; }
  const i = +el.dataset.i, c = props()[i]; if (!c) return;
  selectProp(i);
  const mode = ev.target.classList.contains("l") ? "l" : ev.target.classList.contains("r") ? "r" : "m";
  pl._drag = { mode, i, c, t0: t, x0: ev.clientX, moved: false, pinId: null };
  st.warnHold = ""; refresh(true);
});
$("plane").addEventListener("pointermove", (ev) => {
  const pl = $("plane"), d = pl._drag; if (!d) return;
  const t = tAt(ev);
  if (d.mode === "seek") { seek(t); return; }
  if (!d.moved && Math.abs(ev.clientX - d.x0) < 4) return;
  d.moved = true;
  const snap = (x) => Math.round(x / 0.5) * 0.5, c = d.c, len = c.b - c.a;
  let vals;
  if (d.mode === "l") vals = { a: clamp(snap(t), 0, c.b - 3) };
  else if (d.mode === "r") vals = { b: clamp(snap(t), c.a + 3, D) };
  else { const a = clamp(snap(c.a + t - d.t0), 0, D - len); vals = { a, b: a + len }; }
  if (d.pinId == null) d.pinId = claimPin(c, Object.keys(vals));
  const trial = st.pins.map((p) => (p.id === d.pinId ? { ...p, ...vals } : p));
  const r = solveWith(trial, st.layout);
  if (r.lay) { st.pins = trial; st.warnHold = ""; refresh(true); }
  else { st.warnHold = "That edge cannot go there (it would overlap another clip or leave a mark with no room)."; renderPlan(); }
});
function endPlaneDrag() {
  const pl = $("plane"), d = pl._drag; pl._drag = null;
  if (!d || d.mode === "seek") return;
  if (!d.moved && (d.mode === "l" || d.mode === "r")) {
    const key = d.mode === "l" ? "a" : "b", ids = new Set(d.c.pinned || []);
    for (const p of st.pins) if (ids.has(p.id)) delete p[key];
  }
  st.pins = st.pins.filter((p) => p.a != null || p.b != null);
  st.warnHold = ""; refresh(true);
}
$("plane").addEventListener("pointerup", endPlaneDrag);
$("plane").addEventListener("pointercancel", endPlaneDrag);
$("plane").addEventListener("dblclick", (ev) => {
  const el = ev.target.closest(".prop"); if (!el) return;
  const c = props()[+el.dataset.i]; if (c) acceptClips([c]);
});

/* parts lane (as sketch A) */
$("lane").addEventListener("pointerdown", (ev) => {
  const partEl = ev.target.closest(".part");
  const lane = $("lane"); lane.setPointerCapture(ev.pointerId);
  const t0 = tAt(ev);
  if (partEl) {
    const p = st.parts.find((q) => q.id === +partEl.dataset.id); selectPart(p.id);
    const mode = ev.target.classList.contains("l") ? "l" : ev.target.classList.contains("r") ? "r" : "m";
    lane._drag = { mode, p, t0, s0: p.s, e0: p.e }; refresh(true);
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
  if (d && d.mode === "new" && !d.p) { seek(tAt(ev)); selectNone(); refresh(true); }
});

/* ---------- buttons ---------- */
$("play").onclick = () => { st.stopAt = null; vid.paused ? vid.play() : vid.pause(); };
$("playpart").onclick = () => { const w = selWin(); if (!w) return; seek(w.s); st.stopAt = w.e; vid.play(); };
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
$("kminus").onclick = () => { st.maxK = Math.max(1, st.maxK - 1); refresh(true); };
$("kplus").onclick = () => { st.maxK = Math.min(5, st.maxK + 1); refresh(true); };
function repack() { resolve(true); renderTrack(); renderPlan(); renderLadder(); scheduleCurve(); }
$("repack").onclick = repack;
$("acceptall").onclick = () => acceptClips(props());
$("clearpins").onclick = () => { st.pins = []; refresh(true); };
$("props").addEventListener("click", (ev) => {
  const acc = ev.target.closest("[data-acc]");
  if (acc) { const c = props()[+acc.dataset.acc]; if (c) acceptClips([c]); return; }
  const row = ev.target.closest(".prow"); if (row) { selectProp(+row.dataset.i); refresh(true); }
});
$("bmoment").onclick = () => addMoment(r1(vid.currentTime || 0));
$("bkeep").onclick = () => toggleRange("keep");
$("bboring").onclick = () => toggleRange("boring");
$("deflead").value = st.def.lead; $("deflag").value = st.def.lag;
for (const [id, k] of [["deflead", "lead"], ["deflag", "lag"]]) $(id).oninput = () => { const v = parseFloat($(id).value); if (v >= 0) { st.def[k] = v; save(); } };
$("demo").onclick = () => { st.marks = M.sortMarks([...st.marks.filter((m) => !String(m.id).startsWith("demo-")), ...M.demoMarks(D)]); selectNone(); refresh(true); };
$("clearmarks").onclick = () => { if (!st.marks.length || confirm("Remove all " + st.marks.length + " marks?")) { st.marks = []; st.pend = null; selectNone(); refresh(true); } };

function split(n) {
  const p = selPart(); if (!p) { $("fitsout").textContent = "Select a part first (splitting works on accepted parts)."; return; }
  const btn = $("split" + n), old = btn.textContent; btn.textContent = "Working…"; btn.disabled = true;
  setTimeout(() => {
    const r = bestSplit(P, p.s, p.e, limit(), n, st.audio, n === 3 ? 1 : 0.5);
    btn.textContent = old; btn.disabled = false;
    if (!r) { btn.title = "No split fits"; $("fitsout").textContent = `no ${n}-way split fits in this range`; return; }
    const edges = [p.s, ...r.cuts, p.e];
    st.parts = st.parts.filter((q) => q !== p);
    for (let i = 0; i < n; i++) st.parts.push({ id: nextId++, s: edges[i], e: edges[i + 1] });
    st.parts.sort((a, b) => a.s - b.s); selectPart(st.parts.find((q) => q.s === edges[0]).id);
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
  if (ev.target.matches("input,select,textarea") || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const p = selPart(), key = ev.key.toLowerCase();
  if (ev.code === "Space") { ev.preventDefault(); $("play").click(); }
  else if (key === "m") addMoment(r1(vid.currentTime || 0));
  else if (key === "k") toggleRange("keep");
  else if (key === "b") toggleRange("boring");
  else if (key === "p") repack();
  else if (key === "escape") { st.pend = null; selectNone(); refresh(true); }
  else if (key === "i" && p) setRange(p, vid.currentTime, Math.max(p.e, vid.currentTime + 0.5));
  else if (key === "o" && p) setRange(p, Math.min(p.s, vid.currentTime - 0.5), vid.currentTime);
  else if (key === "delete" || key === "backspace") { ev.preventDefault(); deleteSelected(); }
  else if (key === "arrowleft") { ev.preventDefault(); seek(vid.currentTime - (ev.shiftKey ? 1 : 0.1)); }
  else if (key === "arrowright") { ev.preventDefault(); seek(vid.currentTime + (ev.shiftKey ? 1 : 0.1)); }
});

new ResizeObserver(() => { buildTrack(); renderTrack(); drawCurve(); }).observe(track);
buildTrack(); refresh();
