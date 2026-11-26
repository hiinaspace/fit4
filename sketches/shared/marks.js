// Marks data model shared by the sketch variants. Plain data, no DOM except localStorage.
//
// A mark is a plain JSON object:
//   moment: { id, type:"moment", t, lead, lag, weight }   a point; [t-lead, t+lag] must be inside one clip
//   keep:   { id, type:"keep",   a, b, weight, perSec }    a range; only counts if [a,b] is inside one clip
//   boring: { id, type:"boring", a, b, rate }              a range; `rate` (negative) per second included in a clip
// All functions are pure (they return new arrays/objects) except save/load.

export const DEFAULTS = {
  moment: { lead: 4, lag: 2, weight: 1 },
  keep: { weight: 1, perSec: 0.02 },
  boring: { rate: -0.15 },
};
export const TYPES = ["moment", "keep", "boring"];

let counter = 0;
export const newId = () => "m" + Date.now().toString(36).slice(-4) + (counter++).toString(36) + Math.floor(Math.random() * 1296).toString(36);

const num = (v, d) => (Number.isFinite(+v) ? +v : d);
const r3 = (x) => Math.round(x * 1000) / 1000;

/** Build a mark. spec: moment {t, lead?, lag?, weight?}; keep|boring {a, b, ...}. id optional. */
export function createMark(type, spec = {}) {
  if (!TYPES.includes(type)) throw new Error("unknown mark type " + type);
  const id = spec.id ?? newId();
  if (type === "moment") {
    const d = DEFAULTS.moment;
    return { id, type, t: r3(num(spec.t, 0)), lead: Math.max(0, num(spec.lead, d.lead)), lag: Math.max(0, num(spec.lag, d.lag)), weight: num(spec.weight, d.weight) };
  }
  let a = num(spec.a, 0), b = num(spec.b, a + 1);
  if (b < a) [a, b] = [b, a];
  if (type === "keep") return { id, type, a: r3(a), b: r3(b), weight: num(spec.weight, DEFAULTS.keep.weight), perSec: num(spec.perSec, DEFAULTS.keep.perSec) };
  return { id, type, a: r3(a), b: r3(b), rate: Math.min(0, num(spec.rate, DEFAULTS.boring.rate)) };
}

export const markStart = (m) => (m.type === "moment" ? m.t : m.a);
export const markEnd = (m) => (m.type === "moment" ? m.t : m.b);

export function sortMarks(marks) {
  return [...marks].sort((x, y) => markStart(x) - markStart(y) || markEnd(x) - markEnd(y) || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
}

export const addMark = (marks, mark) => sortMarks([...marks, mark]);
export const removeMark = (marks, id) => marks.filter((m) => m.id !== id);
/** Merge patch into the mark with this id (type cannot change). Re-validates through createMark. */
export function updateMark(marks, id, patch) {
  return sortMarks(marks.map((m) => (m.id === id ? createMark(m.type, { ...m, ...patch, id }) : m)));
}
export const getMark = (marks, id) => marks.find((m) => m.id === id) ?? null;

/** Time range a mark needs inside one clip, clamped to [0, D]. null for boring marks. */
export function contextRange(m, D = Infinity) {
  if (m.type === "moment") return [Math.max(0, m.t - m.lead), Math.min(D, m.t + m.lag)];
  if (m.type === "keep") return [Math.max(0, m.a), Math.min(D, m.b)];
  return null;
}
/** Value of a covered moment/keep mark: moment weight, keep weight + perSec x length. */
export function markWeight(m) {
  if (m.type === "moment") return m.weight;
  if (m.type === "keep") return m.weight + m.perSec * Math.max(0, m.b - m.a);
  return 0;
}

export const serialise = (marks) => JSON.stringify({ v: 1, marks: sortMarks(marks) });
export function deserialise(str) {
  try {
    const o = JSON.parse(str);
    const arr = Array.isArray(o) ? o : o?.marks;
    if (!Array.isArray(arr)) return [];
    return sortMarks(arr.filter((m) => m && TYPES.includes(m.type)).map((m) => createMark(m.type, m)));
  } catch { return []; }
}

export const storageKey = (videoName) => "fit4-marks:" + String(videoName).replace(/^.*[\\/]/, "");
export function loadMarks(videoName) {
  try { const s = localStorage.getItem(storageKey(videoName)); return s ? deserialise(s) : []; } catch { return []; }
}
export function saveMarks(videoName, marks) {
  try { localStorage.setItem(storageKey(videoName), serialise(marks)); return true; } catch { return false; }
}

/**
 * Per-bin interest for drawing. Returns {step, n, pos, neg, net} (Float64Array of n = ceil(D/step)).
 * pos: summed weight density of moment/keep contexts covering the bin (each mark contributes its
 * weight, keep marks their weight, so overlaps stack); neg: boring marks as |rate|/0.15 (1 = default).
 * net = pos - neg.
 */
export function interestTimeline(marks, D, step = 0.5) {
  const n = Math.max(1, Math.ceil(D / step - 1e-9));
  const pos = new Float64Array(n), neg = new Float64Array(n);
  for (const m of marks) {
    if (m.type === "boring") {
      const w = Math.abs(m.rate) / 0.15;
      for (let i = Math.max(0, Math.floor(m.a / step)); i < n && i * step < m.b; i++) neg[i] += w;
    } else {
      const [lo, hi] = contextRange(m, D);
      const w = m.weight;
      for (let i = Math.max(0, Math.floor(lo / step)); i < n && i * step < hi; i++) pos[i] += w;
    }
  }
  const net = pos.map((v, i) => v - neg[i]);
  return { step, n, pos, neg, net };
}

/** A small, plausible demo set. Clearly a demo: ids start with "demo-". */
export function demoMarks(D) {
  const at = (f) => r3(Math.min(D - 3, Math.max(3, D * f)));
  return sortMarks([
    createMark("keep", { id: "demo-keep1", a: 1, b: Math.min(9, D * 0.08) }),
    createMark("moment", { id: "demo-m1", t: at(0.30) }),
    createMark("moment", { id: "demo-m2", t: at(0.33), lead: 5, lag: 3 }),
    createMark("moment", { id: "demo-m3", t: at(0.72) }),
    createMark("boring", { id: "demo-b1", a: r3(D * 0.40), b: r3(D * 0.62) }),
    createMark("boring", { id: "demo-b2", a: r3(Math.max(0, D - 6)), b: r3(D) }),
  ]);
}
