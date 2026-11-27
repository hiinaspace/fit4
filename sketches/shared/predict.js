// Port of fit4.preflight.Probe predictions (keep in step with preflight.py).
export const GOP_SECONDS = 20, VP9_FLOOR = 2.0, EXTRAP_MAX_DB = 0.5, OVERHEAD = 0.006;
export const AUDIO_KBPS = { x264: 96, vp9: 64 };

export const label = (c) => `${c.codec}-${c.height}p${+c.fps.toFixed(3)}`;

export function complexity(p, a, b) {
  const i0 = Math.max(0, Math.floor(a - p.start));
  const i1 = Math.min(p.timeline.length, Math.max(i0 + 1, Math.ceil(b - p.start)));
  const seg = p.timeline.slice(i0, i1);
  return seg.reduce((s, v) => s + v, 0) / seg.length / p.reel_complexity;
}

export function kbpsAt(p, curve, pt, a, b) {
  const d = b - a, frames = d * curve.fps, keys = Math.max(1, Math.ceil(d / GOP_SECONDS));
  const c = complexity(p, a, b);
  return ((keys * pt.key_bytes * c + (frames - keys) * pt.inter_bytes * c) * 8) / d / 1000;
}

export function videoBudget(limit, duration, audioKbps = 0, margin = 0.985) {
  return ((limit * 8 * margin) / (1 + OVERHEAD) - audioKbps * 1000 * duration) / duration / 1000;
}

/** Predicted XPSNR (dB) for curve over [a,b) at videoKbps, or null if the codec can't get that small. */
export function predict(p, curve, a, b, videoKbps) {
  const pts = curve.points.map((pt) => [kbpsAt(p, curve, pt, a, b), pt.xpsnr]).sort((x, y) => x[0] - y[0]);
  if (curve.codec === "vp9" && videoKbps < pts[0][0] * VP9_FLOOR) return null;
  const xs = pts.map((t) => Math.log(t[0])), ys = pts.map((t) => t[1]), x = Math.log(videoKbps);
  let i;
  if (x <= xs[0]) i = 0;
  else if (x >= xs[xs.length - 1]) i = xs.length - 2;
  else { i = 0; for (let j = 0; j < xs.length - 1; j++) if (xs[j] <= x) i = j; }
  const slope = xs[i + 1] !== xs[i] ? (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) : 0;
  const q = ys[i] + slope * (x - xs[i]);
  // Past the best measured point quality saturates; cap the extrapolation (same as preflight.py).
  return x > xs[xs.length - 1] ? Math.min(q, ys[ys.length - 1] + EXTRAP_MAX_DB) : q;
}

/** True when videoKbps is above the best measured point: the prediction is a capped guess. */
export function atCeiling(p, curve, a, b, videoKbps) {
  return videoKbps > Math.max(...curve.points.map((pt) => kbpsAt(p, curve, pt, a, b)));
}

/** Every candidate's prediction for [a,b), best first. audio: include the codec's audio bitrate. */
export function rank(p, a, b, limit, audio = false) {
  const out = [];
  for (const c of p.curves) {
    const kb = videoBudget(limit, b - a, audio ? AUDIO_KBPS[c.codec] : 0);
    const q = kb > 0 ? predict(p, c, a, b, kb) : null;
    out.push({ curve: c, label: label(c), kbps: kb, db: q });
  }
  return out.sort((x, y) => (y.db ?? -1e9) - (x.db ?? -1e9));
}
export const best = (p, a, b, limit, audio) => { const r = rank(p, a, b, limit, audio)[0]; return r && r.db != null ? r : null; };

/** Longest duration from a whose best prediction is >= bar. */
export function fitsFor(p, a, limit, bar, audio = false, step = 0.5) {
  const end = p.start + p.duration; let bestD = 0;
  for (let d = step * 2; a + d <= end + 1e-6; d += step) { const r = best(p, a, a + d, limit, audio); if (r && r.db >= bar) bestD = d; }
  return bestD;
}

/** Cut points inside [s,e) maximizing the worst part's predicted dB (parts = 2 or 3). */
export function bestSplit(p, s, e, limit, parts = 2, audio = false, step = 0.5) {
  const grid = []; for (let t = s + step; t < e - step / 2; t += step) grid.push(t);
  let bestR = null;
  const score = (edges) => { const rs = []; for (let i = 0; i < edges.length - 1; i++) { const r = best(p, edges[i], edges[i + 1], limit, audio); if (!r) return null; rs.push(r); } return rs; };
  if (parts === 2) for (const t of grid) { const rs = score([s, t, e]); if (rs) { const w = Math.min(...rs.map((r) => r.db)); if (!bestR || w > bestR.worst) bestR = { worst: w, cuts: [t], parts: rs }; } }
  else for (let i = 0; i < grid.length; i++) for (let j = i + 2; j < grid.length; j++) { const rs = score([s, grid[i], grid[j], e]); if (rs) { const w = Math.min(...rs.map((r) => r.db)); if (!bestR || w > bestR.worst) bestR = { worst: w, cuts: [grid[i], grid[j]], parts: rs }; } }
  return bestR;
}
