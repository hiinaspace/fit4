// Exact clip-layout solver for the fit4 marks sketches.
//
// Chooses K disjoint windows [a,b] on a 0.5 s grid, each encoded to the same byte limit,
// maximising  sum U(a,b)  (then pick() subtracts lambda x K):
//   U(a,b) = covered(a,b) x g(Q(a,b)) + boringPenalty(a,b)
//   covered : summed weight of moment marks whose [t-lead, t+lag] lies in [a,b] and keep marks fully in [a,b]
//   Q       : best predicted XPSNR dB over probe curves (predict.js, same maths as best()) for that window
//   g       : smoothstep, 0 at <= G_LO (25 dB), 1 at >= G_HI (31 dB)
//   boringPenalty : sum over boring marks of rate x seconds of overlap with the window (rate < 0)
// Free (non-fixed) clips must cover at least one mark unless they satisfy a pin.
// Dynamic programme F[k][t][pinMask] = max(F[k][t-1], max_a F[k-1][a] + U(a,t)): exact, with
// back-pointers; pin bitmask is 1 state when there are no pins (<= 8 pins supported).
//
// Measured with node (probe.json, D = 119.68 s, 241 grid points, minLen 3 / maxLen 120):
//   Q table build ~170 ms once (target 1.5 s); solveK(kMax 5) ~1.2 ms with demo marks, ~3.5 ms with 2 pins,
//   ~1.6 ms with 30 marks (target 100 ms). Re-measure with: node sketches/tools/check_solve.mjs
//
// Q depends only on probe/limit/audio, so it is built once in makeSolver and reused for every
// solveK; marks only affect the U matrix, which is rebuilt per call (~28k windows x marks).

import { videoBudget, predict, label, AUDIO_KBPS } from "./predict.js";
import { contextRange, markWeight } from "./marks.js";

export const G_LO = 25, G_HI = 31;
/** Smooth saturating quality map: 0 at <= 25 dB, 1 at >= 31 dB (smoothstep between). null -> 0. */
export function g(db) {
  if (db == null || !(db > G_LO)) return 0;
  if (db >= G_HI) return 1;
  const x = (db - G_LO) / (G_HI - G_LO);
  return x * x * (3 - 2 * x);
}

const EPS = 1e-9;      // time tolerance
const TIE = 1e-12;     // DP strict-improvement threshold
const PREV_TIE = 1e-6; // prefer the previous layout when within this score

/** Best predicted window quality over probe curves (same selection as predict.js best()). */
function bestWindow(probe, a, b, limitBytes, audioKbps) {
  let bd = null, bi = -1;
  for (let ci = 0; ci < probe.curves.length; ci++) {
    const c = probe.curves[ci];
    const ak = audioKbps == null ? AUDIO_KBPS[c.codec] : audioKbps;
    const kb = videoBudget(limitBytes, b - a, ak);
    const q = kb > 0 ? predict(probe, c, a, b, kb) : null;
    if (q != null && (bd == null || q > bd)) { bd = q; bi = ci; }
  }
  return bd == null ? null : { db: bd, ci: bi };
}

/**
 * makeSolver({probe, limitMiB=4, audioKbps=false, step=0.5, minLen=3, maxLen=120})
 *   audioKbps: false/0 = no audio; true = per-codec defaults from predict.js (AUDIO_KBPS); number = that bitrate for all codecs.
 */
export function makeSolver({ probe, limitMiB = 4, audioKbps = false, step = 0.5, minLen = 3, maxLen = 120 }) {
  const t0 = performance.now();
  const D = probe.duration;
  const limitBytes = Math.round(limitMiB * 1048576);
  const ak = audioKbps === true ? null : audioKbps ? +audioKbps : 0;
  const M = Math.ceil(D / step - EPS), N1 = M + 1;
  const T = new Float64Array(N1);
  for (let i = 0; i < N1; i++) T[i] = Math.min(i * step, D);
  const curveLabels = probe.curves.map(label);

  // Q table: Q[i*N1+j] = dB for window [T[i], T[j]], NaN when out of length range or no codec fits.
  const Q = new Float64Array(N1 * N1).fill(NaN);
  const QC = new Int8Array(N1 * N1).fill(-1);
  for (let i = 0; i < N1; i++) {
    for (let j = i + 1; j < N1; j++) {
      const len = T[j] - T[i];
      if (len < minLen - EPS) continue;
      if (len > maxLen + EPS) break;
      const r = bestWindow(probe, T[i], T[j], limitBytes, ak);
      if (r) { Q[i * N1 + j] = r.db; QC[i * N1 + j] = r.ci; }
    }
  }
  const buildMs = performance.now() - t0;

  const idxOf = (t) => Math.min(M, Math.max(0, Math.round(t / step)));
  const offGrid = new Map();
  /** Predicted {db,label,ci} for any window [a,b] (grid windows come from the table), or null. */
  function quality(a, b) {
    const i = idxOf(a), j = idxOf(b);
    if (Math.abs(T[i] - a) < 1e-6 && Math.abs(T[j] - b) < 1e-6 && j > i) {
      const q = Q[i * N1 + j];
      if (!Number.isNaN(q)) return { db: q, label: curveLabels[QC[i * N1 + j]], ci: QC[i * N1 + j] };
      if (T[j] - T[i] >= minLen - EPS && T[j] - T[i] <= maxLen + EPS) return null;
    }
    if (b - a < 0.4) return null;
    const key = a + "," + b;
    if (!offGrid.has(key)) { const r = bestWindow(probe, a, b, limitBytes, ak); offGrid.set(key, r && { db: r.db, label: curveLabels[r.ci], ci: r.ci }); }
    return offGrid.get(key);
  }

  const markStats = (marks, a, b) => {
    let covW = 0, pen = 0, boringSec = 0; const ids = [];
    for (const m of marks) {
      if (m.type === "boring") {
        const ov = Math.max(0, Math.min(b, m.b) - Math.max(a, m.a));
        pen += m.rate * ov; boringSec += ov;
      } else {
        const [lo, hi] = contextRange(m, D);
        if (lo >= a - EPS && hi <= b + EPS) { covW += markWeight(m); ids.push(m.id); }
      }
    }
    return { covW, pen, boringSec, ids };
  };

  /** Build the layout object for an arbitrary list of clips [{a,b,id?,fixed?}] (need not be on the grid). */
  function evaluate(marks, clipsIn, pinsIn = []) {
    const clips = [...clipsIn].sort((x, y) => x.a - y.a).map((c) => {
      const q = quality(c.a, c.b), s = markStats(marks, c.a, c.b);
      return { id: c.id ?? `c${c.a}-${c.b}`, a: c.a, b: c.b, db: q ? q.db : null, label: q ? q.label : null,
        covered: s.ids, fixed: !!c.fixed, score: s.covW * g(q?.db) + s.pen, coveredWeight: s.covW, boringSec: s.boringSec };
    });
    const dbs = clips.map((c) => c.db);
    let total = 0, totalW = 0, covW = 0, covN = 0, totN = 0;
    for (const m of marks) if (m.type !== "boring") { totN++; totalW += markWeight(m); }
    const covered = new Set();
    for (const c of clips) { covW += c.coveredWeight; for (const id of c.covered) covered.add(id); total += c.score; }
    covN = covered.size;
    const pinned = (c) => pinsIn.filter((p) => (p.a == null || Math.abs(p.a - c.a) <= step / 2 + EPS) && (p.b == null || Math.abs(p.b - c.b) <= step / 2 + EPS) && (p.a != null || p.b != null)).map((p) => p.id);
    for (const c of clips) c.pinned = pinned(c);
    return {
      k: clips.length, clips, covered: covN, totalMarks: totN, coveredWeight: covW, totalWeight: totalW,
      uncovered: marks.filter((m) => m.type !== "boring" && !covered.has(m.id)).map((m) => m.id),
      worstDb: dbs.length && dbs.every((d) => d != null) ? Math.min(...dbs) : null,
      meanDb: dbs.length && dbs.every((d) => d != null) ? dbs.reduce((s, d) => s + d, 0) / dbs.length : null,
      boringSec: clips.reduce((s, c) => s + c.boringSec, 0),
      totalSec: clips.reduce((s, c) => s + (c.b - c.a), 0),
      score: total,
    };
  }

  /**
   * solveK(marks, {kMax=5, pins=[], fixed=[], prev=null}) -> array of length kMax; entry k-1 is the optimal layout with
   * exactly k clips (fixed clips count), or null if infeasible. Layout: see evaluate().
   *   pins:  [{id, a?, b?}]  some clip must start exactly at a (snapped to the grid) and/or end exactly at b
   *   fixed: [{id?, a, b}]   kept verbatim; free clips are packed around them (touching allowed)
   *   prev:  a previous layout; on ties (within 1e-6) its clips are kept
   */
  function solveK(marks, { kMax = 5, pins = [], fixed = [], prev = null } = {}) {
    const fx = [...fixed].sort((x, y) => x.a - y.a).map((c) => ({ ...c, fixed: true }));
    const pn = pins.filter((p) => p.a != null || p.b != null);
    if (pn.length > 8) throw new Error("at most 8 pins");
    const P = pn.length, S = 1 << P, full = S - 1;
    const pinI = pn.map((p) => ({ ia: p.a == null ? -1 : idxOf(p.a), ib: p.b == null ? -1 : idxOf(p.b) }));
    const pinSat = (i, j, a, b) => {
      let s = 0;
      for (let p = 0; p < P; p++) {
        const okA = pn[p].a == null || (a == null ? pinI[p].ia === i : Math.abs(pn[p].a - a) < 1e-6 || Math.abs(T[pinI[p].ia] - a) < 1e-6);
        const okB = pn[p].b == null || (b == null ? pinI[p].ib === j : Math.abs(pn[p].b - b) < 1e-6 || Math.abs(T[pinI[p].ib] - b) < 1e-6);
        if (okA && okB) s |= 1 << p;
      }
      return s;
    };
    let m0 = 0;
    for (const c of fx) m0 |= pinSat(-1, -1, c.a, c.b);
    const nf = fx.length, kfMax = kMax - nf;
    const out = new Array(kMax).fill(null);
    if (kfMax < 0) return out;

    const pairSat = P ? new Uint8Array(N1 * N1) : null;
    const prevFlag = new Uint8Array(N1 * N1);
    const prevFree = prev ? prev.clips.filter((c) => !c.fixed) : [];
    for (const c of prevFree) { const i = idxOf(c.a), j = idxOf(c.b); if (Math.abs(T[i] - c.a) < 1e-6 && Math.abs(T[j] - c.b) < 1e-6) prevFlag[i * N1 + j] = 1; }

    // marks -> flat arrays
    const mom = [], bor = [];
    for (const m of marks) {
      if (m.type === "boring") bor.push(m.a, m.b, m.rate);
      else { const [lo, hi] = contextRange(m, D); mom.push(lo, hi, markWeight(m)); }
    }
    const U = new Float64Array(N1 * N1).fill(-Infinity);
    for (let i = 0; i < M; i++) {
      const ti = T[i];
      let blocked = false;
      for (let j = i + 1; j < N1; j++) {
        const q = Q[i * N1 + j];
        if (Number.isNaN(q)) continue;
        const tj = T[j];
        blocked = false;
        for (const c of fx) if (ti < c.b - EPS && tj > c.a + EPS) { blocked = true; break; }
        if (blocked) continue;
        let cov = 0, cnt = 0, pen = 0;
        for (let m = 0; m < mom.length; m += 3) if (mom[m] >= ti - EPS && mom[m + 1] <= tj + EPS) { cov += mom[m + 2]; cnt++; }
        for (let m = 0; m < bor.length; m += 3) {
          const ov = Math.min(tj, bor[m + 1]) - Math.max(ti, bor[m]);
          if (ov > 0) pen += bor[m + 2] * ov;
        }
        const s = P ? pinSat(i, j, null, null) : 0;
        if (cnt === 0 && s === 0) continue;
        if (P) pairSat[i * N1 + j] = s;
        U[i * N1 + j] = cov * g(q) + pen;
      }
    }

    let fixedScore = 0;
    if (nf) fixedScore = evaluate(marks, fx).score;

    // DP
    const kf = Math.min(kfMax, Math.max(0, kMax));
    const idx = (k, t, m) => (k * N1 + t) * S + m;
    const F = new Float64Array((kf + 1) * N1 * S).fill(-Infinity);
    const bpA = new Int16Array(F.length).fill(-2), bpM = new Uint8Array(F.length);
    for (let t = 0; t < N1; t++) F[idx(0, t, m0)] = 0;
    for (let k = 1; k <= kf; k++) {
      for (let t = 1; t < N1; t++) {
        for (let m = 0; m < S; m++) {
          const o = idx(k, t, m), sk = F[idx(k, t - 1, m)];
          if (sk > -Infinity) { F[o] = sk; bpA[o] = -1; }
        }
        for (let a = 0; a < t; a++) {
          const u = U[a * N1 + t];
          if (u === -Infinity) continue;
          const bonus = prevFlag[a * N1 + t] ? 1e-9 : 0, s = P ? pairSat[a * N1 + t] : 0;
          for (let m = 0; m < S; m++) {
            const pv = F[idx(k - 1, a, m)];
            if (pv === -Infinity) continue;
            const nm = m | s, o = idx(k, t, nm), v = pv + u + bonus;
            if (v > F[o] + TIE) { F[o] = v; bpA[o] = a; bpM[o] = m; }
          }
        }
      }
    }

    for (let k = nf === 0 ? 1 : 0; k <= kf; k++) {
      const K = nf + k;
      if (K < 1 || K > kMax) continue;
      let clips = null;
      if (k === 0) { if (m0 === full) clips = []; }
      else if (F[idx(k, M, full)] > -Infinity) {
        clips = []; let kk = k, t = M, m = full;
        while (kk > 0) {
          const o = idx(kk, t, m), a = bpA[o];
          if (a === -1) { t--; continue; }
          clips.push({ a: T[a], b: T[t] }); m = bpM[o]; t = a; kk--;
        }
        clips.reverse();
      }
      if (!clips) continue;
      let lay = evaluate(marks, [...clips, ...fx], pins);
      // keep the previous layout when it is as good within 1e-6
      if (prev && prevFree.length === k && k > 0) {
        let ps = fixedScore, pm = m0, ok = true;
        const pc = [];
        for (const c of prevFree) {
          const i = idxOf(c.a), j = idxOf(c.b), u = U[i * N1 + j];
          if (Math.abs(T[i] - c.a) > 1e-6 || Math.abs(T[j] - c.b) > 1e-6 || u === -Infinity) { ok = false; break; }
          ps += u; pm |= P ? pairSat[i * N1 + j] : 0; pc.push({ a: T[i], b: T[j] });
        }
        if (ok && pm === full && ps >= lay.score - PREV_TIE && !sameClips(pc, clips)) lay = evaluate(marks, [...pc, ...fx], pins);
      }
      out[K - 1] = lay;
    }
    return out;
  }

  return { probe, D, step, limitBytes, limitMiB, audioKbps: ak, minLen, maxLen, buildMs, grid: { step, M, N1, times: T }, quality, evaluate, solveK, pick, stages, g };
}

function sameClips(x, y) {
  return x.length === y.length && x.every((c, i) => Math.abs(c.a - y[i].a) < 1e-9 && Math.abs(c.b - y[i].b) < 1e-9);
}

/** Layout maximising score - lambda x k among non-null layouts (ties within 1e-6: fewer clips). null if none. */
export function pick(layouts, lambda = 0) {
  let best = null, bv = -Infinity;
  for (const l of layouts) {
    if (!l) continue;
    const v = l.score - lambda * l.k;
    if (best == null || v > bv + PREV_TIE) { best = l; bv = v; }
  }
  return best;
}

/**
 * Upper envelope of score - lambda x k over lambda >= 0, so a slider can snap to distinct layouts.
 * Returns [{k, layout, from, to}] with lambda ascending (first = most clips, last: to = Infinity).
 */
export function stages(layouts) {
  const ls = layouts.filter(Boolean);
  if (!ls.length) return [];
  let cur = pick(ls, 0), from = 0;
  const out = [];
  for (;;) {
    let nxt = null, nl = Infinity;
    for (const l of ls) {
      if (l.k >= cur.k) continue;
      const lam = (cur.score - l.score) / (cur.k - l.k);
      if (lam < nl - PREV_TIE || (Math.abs(lam - nl) <= PREV_TIE && nxt && l.k < nxt.k)) { nl = lam; nxt = l; }
    }
    if (!nxt) { out.push({ k: cur.k, layout: cur, from, to: Infinity }); break; }
    nl = Math.max(nl, from);
    out.push({ k: cur.k, layout: cur, from, to: nl });
    cur = nxt; from = nl;
  }
  return out;
}
