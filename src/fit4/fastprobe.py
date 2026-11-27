"""A pre-flight probe cheap enough to always run.

Same output as preflight.run_probe (a `Probe`, same JSON), made with:

- a shorter sample reel (default 3 x 2 s instead of 4 x 3 s);
- fast encoder settings for the probe points (x264 veryfast, libvpx cpu-used 5
  without alt-ref lag). Their curves sit a roughly constant number of dB below
  the screening settings' (OFFSET_DB), which the pruning step adds back so
  x264 and VP9 compare on one scale;
- x264 first, then VP9 in two steps: one CRF-63 point per geometry (its bitrate
  is libvpx's floor), then the other points only for the few feasible
  geometries that x264 ranks best. Geometries that are not probed are reported
  to the caller with the reason.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from . import ff
from .encode import Candidate, norm_filter
from .layout import Layout
from .metrics import score
from .preflight import (
    VP9_FLOOR,
    CandCurve,
    Point,
    Probe,
    build_reel,
    crf_args,
    key_inter,
    pick_slices,
    timeline,
    video_budget,
)
from .probe import Source
from .prune import curve_for

# x264 medium sits 0.20 dB (sd 0.12) under the screening preset at equal bitrate for 1.2x less time;
# veryfast/faster lose 1.2 dB with sd 0.8. libvpx's cheaper settings buy no time (cpu-used 5 =
# cpu-used 3 in wall time, realtime mode is 3x faster but 1.5 dB off with sd 0.8), so VP9 points
# use the screening settings and need no offset. Numbers: NOTES-sweep.md, scripts/measure_probe_fast.py.
X264_PRESET = "medium"
VP9_FAST: dict[str, str] = {}
# Each codec gets one reference point first (x264 at a middle CRF, VP9 at CRF 63, whose bitrate is
# also libvpx's floor); the others are placed near where the window's budget should fall.
REF_CRF = {"x264": 32, "vp9": 63}
CRF_RANGE = {"x264": (10, 51), "vp9": (10, 62)}
CRF_SPREAD = {"x264": (-4, 4), "vp9": (-5, 3)}  # offsets around the predicted budget CRF
CRF_PER_OCTAVE = {"x264": 6.5, "vp9": 10.0}  # CRF steps for the bitrate to halve (reel data, 720p60)
# Sample reel: N slices of S seconds (more is slower and a little more accurate).
DEFAULT_REEL = "2x2"
# Probe x264 beyond its pilot geometry only when the pilot is this close to the best VP9 (dB).
X264_NEAR_DB = 0.5
# A lower frame rate is probed only if the estimate from its judder ceiling reaches the best full-rate VP9 minus this (dB).
JUDDER_SLACK_DB = 0.0
VP9_FLOOR_CRF = REF_CRF["vp9"]
# dB the screening settings gain over the fast probe settings, per codec (scripts/measure_probe_fast.py).
# vp9 +0.4: on A30 the reel predicted 0.37-0.46 dB under the full encode for all four VP9 geometries.
OFFSET_DB = {"x264": 0.20, "vp9": 0.4}
# None: probe every floor-feasible VP9 geometry. Ranking them by the x264 prediction is wrong:
# VP9 gains far more from resolution (A30: x264 ranked 1080p60 last of 8, it was the true best).
VP9_PROBE_TOP: int | None = None


@dataclass
class FastProbe:
    probe: Probe
    unprobed: dict[Candidate, str] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)  # stage -> wall seconds


def fast_args(cand: Candidate, reel: Source, crf: int, threads: int) -> list[str]:
    args = crf_args(cand, reel, crf, threads)
    if cand.codec == "x264":
        args[args.index("-preset") + 1] = X264_PRESET
        i = args.index("-x264-params")
        args[i + 1] = "aq-mode=3"  # the screening lookahead is part of what the preset cost buys
        return args
    for k, v in VP9_FAST.items():
        args[args.index(k) + 1] = v
    return args


def pick_crfs(codec: str, ref_kbps: float, budget_kbps: float) -> list[int]:
    """CRFs to probe next, around where the budget should fall given the reference point's bitrate.

    Both are in the same units (kbps over the probed window). The estimate is
    crude (bitrate halves every CRF_PER_OCTAVE steps); the spread on either
    side of it is what makes the budget land between two measured points.
    """
    star = REF_CRF[codec] + CRF_PER_OCTAVE[codec] * math.log2(max(ref_kbps, 1e-6) / max(budget_kbps, 1e-6))
    lo, hi = CRF_RANGE[codec]
    picks = {min(hi, max(lo, round(star + d))) for d in CRF_SPREAD[codec]}
    picks.discard(REF_CRF[codec])
    return sorted(picks)


def encode_point(reel: Source, cand: Candidate, crf: int, work: Path, threads: int) -> Point:
    ext = "mp4" if cand.codec == "x264" else "webm"
    out = work / f"{cand.label()}.fast{crf}.{ext}"
    ff.run(
        [*ff.FFMPEG, "-v", "error", "-y", "-i", str(reel.path), "-vf", norm_filter(reel, cand.height, cand.fps),
         "-an", *fast_args(cand, reel, crf, threads), str(out)],
    )  # fmt: skip
    key, inter = key_inter(out)
    sc = score(reel, out, out.with_suffix(".xpsnr.txt"), ssim=False)
    out.unlink()
    out.with_suffix(".xpsnr.txt").unlink(missing_ok=True)
    return Point(crf, key, inter, sc.xpsnr_yuv, sc.ssim_y)


def vp9_order(
    probe: Probe,
    vp9: Sequence[Candidate],
    x264_quality: Mapping[tuple[int, float], float],
    limit: int,
    audio_kbps: float,
) -> tuple[list[Candidate], dict[Candidate, str]]:
    """Feasible VP9 geometries (CRF-63 point only so far) ranked by the x264 prediction, and why the rest are out."""
    a, b = probe.start, probe.start + probe.duration
    kb = video_budget(limit, b - a, audio_kbps)
    feasible, out = [], {}
    for c in vp9:
        curve = curve_for(probe, c)
        assert curve is not None
        floor_kbps = probe.kbps_at(curve, curve.points[0], a, b)
        if kb < floor_kbps * VP9_FLOOR:
            out[c] = (
                f"budget {kb:.0f} kbps is under {VP9_FLOOR:g}x the VP9 CRF-63 bitrate ({floor_kbps:.0f}): would not fit"
            )
        else:
            feasible.append(c)
    feasible.sort(key=lambda c: x264_quality.get((c.height, float(c.fps)), -math.inf), reverse=True)
    return feasible, out


def judder_db(reel: Source, fps: Fraction, work: Path) -> float:
    """XPSNR of the reel against itself dropped to `fps` and frame-doubled back: a ceiling for any encode at that rate."""
    out = work / f"judder{float(fps):g}.mp4"
    ff.run(
        [*ff.FFMPEG, "-v", "error", "-y", "-i", str(reel.path), "-vf", f"fps=fps={fps}", "-an",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(out)],
    )  # fmt: skip
    sc = score(reel, out, out.with_suffix(".xpsnr.txt"), ssim=False)
    out.unlink(missing_ok=True)
    out.with_suffix(".xpsnr.txt").unlink(missing_ok=True)
    return sc.xpsnr_yuv


def lower_rate_estimate(
    probe: Probe, full: CandCurve | None, judder: float, ratio: float, limit: int, audio_kbps: float
) -> float | None:
    """Quality ceiling of a VP9 geometry at 1/ratio of the source frame rate, from its full-rate curve.

    Each frame gets `ratio` times the bits, so the coding quality is the full-rate curve read at
    ratio x the budget; the frame-doubling error (the reel's judder score) adds as noise power.
    None when the curve cannot say (missing, or under the floor).
    """
    if full is None:
        return None
    a, b = probe.start, probe.start + probe.duration
    coding = probe.predict(full, a, b, video_budget(limit, b - a, audio_kbps) * ratio)
    if coding is None:
        return None
    noise = 10 ** (-judder / 10) + 10 ** (-(coding + OFFSET_DB["vp9"]) / 10)
    return -10 * math.log10(noise)


def run_fast_probe(
    src: Source,
    cands: Sequence[Candidate],
    limit: int,
    work: Path,
    layout: Layout,
    *,
    audio_kbps: Mapping[str, float] | None = None,
    n_slices: int = 3,
    slice_len: float = 2.0,
    log: Callable[[str], None] = lambda s: None,
    on_stage: Callable[[str], None] = lambda s: None,
) -> FastProbe:
    """Probe in stages, spending encodes only where they can change the pruning.

    A: VP9 at every height at the source rate, one CRF-63 reference point each
       (the floor), and one x264 pilot geometry.
    B: the other two points of every floor-feasible VP9 geometry and of the pilot.
    C: only if needed: the other x264 geometries (when the pilot is within
       X264_NEAR_DB of the best VP9), and VP9 at lower frame rates (when the
       reel's judder ceiling at that rate could still beat the best full-rate VP9).
    Whatever is not probed is reported in `unprobed` with the reason.
    """
    work.mkdir(parents=True, exist_ok=True)
    secs: dict[str, float] = {}
    t = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t
        now = time.monotonic()
        secs[name] = secs.get(name, 0.0) + now - t
        t = now
        on_stage(name)

    budget = layout.budget
    tl = timeline(src, work, threads=budget)
    lap("timeline")
    slices = pick_slices(src, n_slices, slice_len)
    reel_c = []
    for a, d in slices:
        i0 = int(a - src.start)
        seg = tl[i0 : max(i0 + 1, math.ceil(a + d - src.start))]
        reel_c.append(sum(seg) / len(seg))
    reel = build_reel(src, slices, work)
    lap("reel")

    def points(jobs: list[tuple[Candidate, int]]) -> list[Point]:
        # All jobs at once, each with a share of the thread budget in proportion to its cost, so the
        # big encodes (VP9 1080p) are not the tail of the stage.
        w = [j[0].height ** 2 * float(j[0].fps) * (3.0 if j[0].codec == "vp9" else 1.0) for j in jobs]
        ths = [max(1, min(4, round(budget * x / sum(w)))) for x in w]
        while sum(ths) > budget and max(ths) > 1:
            ths[ths.index(max(ths))] -= 1
        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futs = [ex.submit(encode_point, reel, c, crf, work, th) for (c, crf), th in zip(jobs, ths)]
            return [f.result() for f in futs]

    src_fps = float(src.fps)
    at_rate = [c for c in cands if abs(float(c.fps) - src_fps) < 1e-3]
    x264 = [c for c in at_rate if c.codec == "x264"]
    vp9 = [c for c in at_rate if c.codec == "vp9"]
    curves: dict[Candidate, CandCurve] = {}
    a, b = src.start, src.start + src.duration
    akb = audio_kbps or {}

    def add(jobs: list[tuple[Candidate, int]]) -> None:
        for (c, _), p in zip(jobs, points(jobs)):
            curves.setdefault(c, CandCurve(c.codec, c.height, float(c.fps))).points.append(p)

    def build() -> Probe:
        return Probe(
            start=src.start, duration=src.duration, src_fps=src_fps, timeline=tl,
            reel_complexity=sum(reel_c) / len(reel_c), slices=slices, curves=list(curves.values()),
        )  # fmt: skip

    def next_crfs(cs: Sequence[Candidate]) -> list[tuple[Candidate, int]]:
        interim = build()
        jobs = []
        for c in cs:
            curve = curves[c]
            ref = curve.points[0]
            kb = video_budget(limit, b - a, akb.get(c.codec, 0.0))
            jobs += [(c, crf) for crf in pick_crfs(c.codec, interim.kbps_at(curve, ref, a, b), kb)]
        return jobs

    def quality(c: Candidate, probe_: Probe) -> float | None:
        q = probe_.predict(curves[c], a, b, video_budget(limit, b - a, akb.get(c.codec, 0.0)))
        return None if q is None else q + OFFSET_DB.get(c.codec, 0.0)

    unprobed: dict[Candidate, str] = {}
    video_kbps_vp9 = video_budget(limit, b - a, akb.get("vp9", 0.0))
    # Stage A
    pilot = sorted(x264, key=lambda c: c.height)[len(x264) // 2 :][:1]  # the middle height
    add([(c, VP9_FLOOR_CRF) for c in vp9] + [(c, REF_CRF["x264"]) for c in pilot])
    lap("A: reference points")
    # Stage B: only the feasible VP9 geometries get more points
    feasible, _ = vp9_order(build(), vp9, {}, limit, akb.get("vp9", 0.0))  # floor-infeasible ones keep only CRF 63
    add(next_crfs(feasible) + next_crfs(pilot))
    lap("B: bracketing points")
    interim = build()
    qv = [(q, c) for c in feasible if (q := quality(c, interim)) is not None]
    best_vp9 = max(qv, key=lambda t: t[0])[0] if qv else None
    # When the best full-rate figure is capped (budget above the best measured point) it is a ceiling,
    # not a forecast, and a lower frame rate cannot be ruled out against it.
    best_capped = bool(qv) and interim.at_ceiling(curves[max(qv, key=lambda t: t[0])[1]], a, b, video_kbps_vp9)
    # Stage C
    more_x264 = [c for c in x264 if c not in pilot]
    qx = quality(pilot[0], interim) if pilot else None
    if more_x264 and qx is not None and (best_vp9 is None or qx >= best_vp9 - X264_NEAR_DB):
        add([(c, REF_CRF["x264"]) for c in more_x264])
        add(next_crfs(more_x264))
    else:
        for c in more_x264:
            unprobed[c] = (
                f"x264 pilot ({pilot[0].height}p) is {best_vp9 - qx:.1f} dB behind the best VP9"
                if qx is not None and best_vp9 is not None
                else "x264 not probed"
            )
    lower = [c for c in cands if c not in at_rate]
    if lower:
        jud = {
            f: judder_db(reel, Fraction(f).limit_denominator(1001), work) for f in sorted({float(c.fps) for c in lower})
        }
        lap("judder")
        lower_vp9 = []
        notes: list[str] = []
        for c in lower:
            if c.codec != "vp9":
                unprobed[c] = "lower frame rate x264 is not probed"
                continue
            est = lower_rate_estimate(
                build(),
                curves.get(Candidate("vp9", c.height, src.fps)),
                jud[float(c.fps)],
                src_fps / float(c.fps),
                limit,
                akb.get("vp9", 0.0),
            )
            notes.append(f"{c.label()} {est if est is None else round(est, 1)}")
            if est is not None and best_vp9 is not None and not best_capped and est < best_vp9 - JUDDER_SLACK_DB:
                unprobed[c] = (
                    f"{float(c.fps):g} fps: judder ceiling {jud[float(c.fps)]:.1f} dB, so about {est:.1f} dB at best against {best_vp9:.1f} for full-rate VP9"
                )
            else:
                lower_vp9.append(c)
        if notes:
            log(
                "probe: lower-rate estimates (dB): "
                + ", ".join(notes)
                + ("; full-rate VP9 is at its ceiling, so none is ruled out on that" if best_capped else "")
            )
        if lower_vp9:
            add([(c, VP9_FLOOR_CRF) for c in lower_vp9])
            f2, _ = vp9_order(build(), lower_vp9, {}, limit, akb.get("vp9", 0.0))
            add(next_crfs(f2))
    lap("C: optional geometries")
    for c in unprobed:
        curves.pop(c, None)
    for c in curves.values():
        c.points.sort(key=lambda p: p.crf)
    reel.path.unlink(missing_ok=True)
    log("probe: " + ", ".join(f"{k} {v:.0f}s" for k, v in secs.items()))
    return FastProbe(build(), unprobed, secs)
