// Checks for shared/solve.js + marks.js. Run: nix shell nixpkgs#nodejs -c node sketches/tools/check_solve.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { makeSolver, g, pick, stages, G_LO, G_HI } from "../shared/solve.js";
import { demoMarks, createMark, contextRange, markWeight, serialise, deserialise, interestTimeline, updateMark, removeMark, addMark } from "../shared/marks.js";
import { best } from "../shared/predict.js";

const here = dirname(fileURLToPath(import.meta.url));
const probe = JSON.parse(readFileSync(join(here, "../data/probe.json"), "utf8"));
let failed = 0;
const test = (name, fn) => { try { fn(); console.log("ok   ", name); } catch (e) { failed++; console.log("FAIL ", name, "\n", e.message); } };

// deterministic rng
let seed = 12345;
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);

// ---------- (a) brute force on a tiny synthetic probe ----------
function stubProbe(D) {
  const timeline = Array.from({ length: Math.ceil(D) }, () => 20 + rnd() * 60);
  const mk = (codec, shift) => ({ codec, height: 540, fps: 30, points: [0, 1, 2, 3].map((i) => ({ crf: 20 + i * 4, key_bytes: 90000 / (1 + i), inter_bytes: 9000 / (1 + i), xpsnr: shift + 10 - i * 2.2, ssim: 0.9 })) });
  return { start: 0, duration: D, src_fps: 30, timeline, reel_complexity: timeline.reduce((s, v) => s + v, 0) / timeline.length, slices: [], curves: [mk("x264", 21), mk("vp9", 22)] };
}
function bruteU(marks, sv, a, b) {
  let cov = 0, cnt = 0, pen = 0;
  for (const m of marks) {
    if (m.type === "boring") { pen += m.rate * Math.max(0, Math.min(b, m.b) - Math.max(a, m.a)); continue; }
    const [lo, hi] = contextRange(m, sv.D);
    if (lo >= a - 1e-9 && hi <= b + 1e-9) { cov += markWeight(m); cnt++; }
  }
  const q = sv.quality(a, b);
  return { u: cov * g(q?.db) + pen, cnt, feasible: !!q };
}
function brute(marks, sv, K, minLen = 3) {
  const t = sv.grid.times, N = t.length;
  let bestS = -Infinity;
  const rec = (k, from, sum) => {
    if (k === 0) { if (sum > bestS) bestS = sum; return; }
    for (let i = from; i < N; i++) for (let j = i + 1; j < N; j++) {
      if (t[j] - t[i] < minLen - 1e-9) continue;
      const r = bruteU(marks, sv, t[i], t[j]);
      if (!r.feasible || r.cnt === 0) continue;
      rec(k - 1, j, sum + r.u);
    }
  };
  rec(K, 0, 0);
  return bestS;
}
test("(a) solveK equals brute force, K<=2, random stubs", () => {
  for (let trial = 0; trial < 12; trial++) {
    const D = 11 + Math.floor(rnd() * 5);
    const pr = stubProbe(D);
    const sv = makeSolver({ probe: pr, limitMiB: 0.12 + rnd() * 0.12, audioKbps: rnd() < 0.5, step: 0.5, minLen: 3 });
    const marks = [];
    for (let i = 0, n = 2 + Math.floor(rnd() * 3); i < n; i++) marks.push(createMark("moment", { id: "m" + i, t: rnd() * D, lead: 1 + rnd() * 2, lag: rnd() * 2, weight: 0.5 + rnd() }));
    if (rnd() < 0.7) { const a = rnd() * (D - 4); marks.push(createMark("keep", { id: "k", a, b: a + 1 + rnd() * 2 })); }
    if (rnd() < 0.7) { const a = rnd() * (D - 4); marks.push(createMark("boring", { id: "b", a, b: a + 1 + rnd() * 4 })); }
    const L = sv.solveK(marks, { kMax: 2 });
    for (let K = 1; K <= 2; K++) {
      const want = brute(marks, sv, K);
      if (want === -Infinity) { assert.equal(L[K - 1], null, `trial ${trial} K=${K} should be infeasible`); continue; }
      assert.ok(L[K - 1], `trial ${trial} K=${K} null but brute found ${want}`);
      assert.ok(Math.abs(L[K - 1].score - want) < 1e-9, `trial ${trial} K=${K}: dp ${L[K - 1].score} vs brute ${want}`);
    }
  }
});

// ---------- (b) real probe ----------
const sv = makeSolver({ probe, limitMiB: 4, audioKbps: false });
const D = probe.duration;
const marks = demoMarks(D);
const layouts = sv.solveK(marks, { kMax: 5 });

test("quality() matches predict.best()", () => {
  for (const [a, b] of [[0, 10], [20, 33.5], [60, 119.5], [5, 5.7]]) {
    const r = best(probe, a, b, sv.limitBytes, false), q = sv.quality(a, b);
    if (!r) { assert.equal(q, null); continue; }
    assert.ok(Math.abs(r.db - q.db) < 1e-9 && r.label === q.label, `${a}-${b}`);
  }
});

console.log("\nDemo marks:", marks.map((m) => `${m.id}(${m.type} ${m.type === "moment" ? m.t : m.a + "-" + m.b})`).join(" "));
console.log("limit 4 MiB, no audio. K | clips | covered | worst dB | mean dB | score");
for (const l of layouts) {
  if (!l) { console.log("  null"); continue; }
  console.log(`${l.k} | ${l.clips.map((c) => `[${c.a}-${c.b} ${c.db?.toFixed(1)}dB ${c.label}]`).join(" ")} | ${l.covered}/${l.totalMarks} | ${l.worstDb?.toFixed(2)} | ${l.meanDb?.toFixed(2)} | ${l.score.toFixed(3)}`);
}
console.log("stages:", stages(layouts).map((s) => `k=${s.k} lambda ${s.from.toFixed(3)}..${s.to === Infinity ? "inf" : s.to.toFixed(3)}`).join("; "));

function validate(lay, marks, fixedOk = true) {
  const byId = Object.fromEntries(marks.map((m) => [m.id, m]));
  let prevEnd = -Infinity;
  for (const c of [...lay.clips].sort((x, y) => x.a - y.a)) {
    assert.ok(c.a >= -1e-9 && c.b <= D + 1e-9 && c.b - c.a >= 3 - 1e-9 && c.b - c.a <= 120 + 1e-9, `clip bounds ${c.a}-${c.b}`);
    assert.ok(c.a >= prevEnd - 1e-9, `overlap at ${c.a} < ${prevEnd}`);
    prevEnd = c.b;
    for (const id of c.covered) {
      const [lo, hi] = contextRange(byId[id], D);
      assert.ok(lo >= c.a - 1e-9 && hi <= c.b + 1e-9, `${id} context [${lo},${hi}] not inside [${c.a},${c.b}]`);
    }
  }
  // every mark inside some clip is reported covered, and no mark is double counted
  const seen = new Set();
  for (const c of lay.clips) for (const id of c.covered) { assert.ok(!seen.has(id)); seen.add(id); }
  for (const m of marks) if (m.type !== "boring") {
    const [lo, hi] = contextRange(m, D);
    const inside = lay.clips.some((c) => lo >= c.a - 1e-9 && hi <= c.b + 1e-9);
    assert.equal(seen.has(m.id), inside, `coverage flag of ${m.id}`);
  }
}
test("(b) demo layouts: disjoint, in range, coverage honest", () => {
  assert.ok(layouts[0], "K=1 exists");
  for (const l of layouts) if (l) { assert.equal(l.clips.length, l.k); validate(l, marks); }
  assert.ok(layouts.filter(Boolean).length >= 3);
});
test("g() constants and shape", () => {
  assert.equal(g(25), 0); assert.equal(g(31), 1); assert.equal(g(40), 1); assert.equal(g(null), 0); assert.ok(Math.abs(g(28) - 0.5) < 1e-12);
  assert.equal(G_LO, 25); assert.equal(G_HI, 31);
});
test("pick() and stages() consistent", () => {
  const st = stages(layouts);
  assert.ok(st.length >= 1);
  for (const s of st) { const mid = s.to === Infinity ? s.from + 1 : (s.from + s.to) / 2; assert.equal(pick(layouts, mid).k, s.k, `lambda ${mid}`); }
  assert.equal(pick(layouts, 1e9).k, layouts.findIndex(Boolean) + 1);
});

{
  const s1 = makeSolver({ probe, limitMiB: 1, audioKbps: false });
  console.log("\nSame marks at 1 MiB, no audio:");
  for (const l of s1.solveK(marks, { kMax: 5 })) console.log(!l ? "  null" : `${l.k} | ${l.clips.map((c) => `[${c.a}-${c.b} ${c.db?.toFixed(1)}dB]`).join(" ")} | ${l.covered}/${l.totalMarks} | score ${l.score.toFixed(3)}`);
}

// ---------- (c) pins, fixed, prev ----------
test("(c) pinned start and end honoured", () => {
  const free = layouts[1];
  assert.ok(free);
  const c0 = free.clips[0];
  const pa = c0.a + 1.5; // not where the optimum put it
  const L = sv.solveK(marks, { kMax: 3, pins: [{ id: "p1", a: pa }] });
  for (const l of L) if (l) { assert.ok(l.clips.some((c) => Math.abs(c.a - pa) < 1e-9), `no clip starts at ${pa} for k=${l.k}`); validate(l, marks); }
  assert.ok(L.some(Boolean));
  const L2 = sv.solveK(marks, { kMax: 3, pins: [{ id: "p2", a: 20, b: 33 }, { id: "p3", b: 90 }] });
  for (const l of L2) if (l) { assert.ok(l.clips.some((c) => c.a === 20 && c.b === 33)); assert.ok(l.clips.some((c) => c.b === 90)); validate(l, marks); }
  assert.ok(L2.some(Boolean));
});
test("(c) fixed clips kept, others packed around", () => {
  const fixed = [{ id: "f1", a: 40.25, b: 52.1 }];
  const L = sv.solveK(marks, { kMax: 4, fixed });
  assert.equal(L[0].clips.length, 1);
  assert.deepEqual([L[0].clips[0].a, L[0].clips[0].b, L[0].clips[0].fixed], [40.25, 52.1, true]);
  for (const l of L) if (l) {
    assert.ok(l.clips.some((c) => c.fixed && c.a === 40.25 && c.b === 52.1));
    validate(l, marks);
  }
});
test("(c) prev layout preferred on ties; unchanged marks give identical layout", () => {
  const again = sv.solveK(marks, { kMax: 5, prev: layouts[2] });
  assert.deepEqual(again[2].clips.map((c) => [c.a, c.b]), layouts[2].clips.map((c) => [c.a, c.b]));
  // adding a boring mark far from any clip changes nothing
  const extra = [...marks, createMark("boring", { id: "x", a: 100, b: 100.5 })];
  const b = sv.solveK(extra, { kMax: 5, prev: layouts[2] });
  assert.deepEqual(b[2].clips.map((c) => [c.a, c.b]), layouts[2].clips.map((c) => [c.a, c.b]));
});
test("marks model round trip", () => {
  const s = deserialise(serialise(marks));
  assert.deepEqual(s.map((m) => m.id), marks.map((m) => m.id));
  assert.equal(removeMark(marks, "demo-m1").length, marks.length - 1);
  assert.equal(updateMark(marks, "demo-m1", { lead: 7 }).find((m) => m.id === "demo-m1").lead, 7);
  const it = interestTimeline(marks, D, 0.5); assert.equal(it.n, 240);
  assert.deepEqual(addMark([], createMark("moment", { t: 3 })).length, 1);
});

// ---------- (d) timing ----------
{
  const t0 = performance.now(); const s2 = makeSolver({ probe, limitMiB: 4, audioKbps: false }); const build = performance.now() - t0;
  const time = (fn, n = 20) => { fn(); const t = performance.now(); for (let i = 0; i < n; i++) fn(); return (performance.now() - t) / n; };
  const plain = time(() => s2.solveK(marks, { kMax: 5 }));
  const pinned = time(() => s2.solveK(marks, { kMax: 5, pins: [{ id: "p", a: 20 }, { id: "q", b: 90 }] }));
  const many = [...marks]; for (let i = 0; i < 24; i++) many.push(createMark("moment", { t: 5 + i * 4.5 }));
  const heavy = time(() => s2.solveK(many, { kMax: 5 }));
  console.log(`\nTIMING: Q table build ${build.toFixed(0)} ms (once, ${s2.grid.N1} grid points); solveK kMax=5: ${plain.toFixed(1)} ms (demo marks), ${pinned.toFixed(1)} ms (2 pins), ${heavy.toFixed(1)} ms (30 marks)`);
  test("(d) timing targets: build < 1500 ms, solveK < 100 ms", () => { assert.ok(build < 1500, "build " + build); assert.ok(plain < 100 && pinned < 100 && heavy < 100); });
}

console.log(failed ? `\n${failed} FAILED` : "\nall passed");
process.exit(failed ? 1 : 0);
