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
from dataclasses import dataclass, field
from pathlib import Path

from . import ff
from .encode import Candidate, norm_filter
from .layout import Layout, run_weighted
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
VP9_FLOOR_CRF = REF_CRF["vp9"]
# dB the screening settings gain over the fast probe settings, per codec (scripts/measure_probe_fast.py).
OFFSET_DB = {"x264": 0.20, "vp9": 0.0}
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
    sc = score(reel, out, out.with_suffix(".xpsnr.txt"))
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
            out[c] = f"budget {kb:.0f} kbps is under 1.6x the VP9 CRF-63 bitrate ({floor_kbps:.0f}): would not fit"
        else:
            feasible.append(c)
    feasible.sort(key=lambda c: x264_quality.get((c.height, float(c.fps)), -math.inf), reverse=True)
    return feasible, out


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
    vp9_top: int | None = VP9_PROBE_TOP,
    log: Callable[[str], None] = lambda s: None,
) -> FastProbe:
    work.mkdir(parents=True, exist_ok=True)
    secs: dict[str, float] = {}
    t = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t
        now = time.monotonic()
        secs[name] = now - t
        t = now

    tl = timeline(src, work, threads=layout.budget)
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
        return run_weighted(jobs, lambda j, th: encode_point(reel, j[0], j[1], work, th), layout, lambda j: j[0].codec)

    x264 = [c for c in cands if c.codec == "x264"]
    vp9 = [c for c in cands if c.codec == "vp9"]
    curves: dict[Candidate, CandCurve] = {}
    a, b = src.start, src.start + src.duration
    akb = audio_kbps or {}

    def add(jobs: list[tuple[Candidate, int]]) -> None:
        for (c, _), p in zip(jobs, points(jobs)):
            curves.setdefault(c, CandCurve(c.codec, c.height, float(c.fps))).points.append(p)

    def build() -> Probe:
        return Probe(
            start=src.start, duration=src.duration, src_fps=float(src.fps), timeline=tl,
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

    add([(c, REF_CRF["x264"]) for c in x264] + [(c, VP9_FLOOR_CRF) for c in vp9])
    lap("reference points")
    add(next_crfs(x264))
    lap("x264 points")

    unprobed: dict[Candidate, str] = {}
    if vp9:
        interim = build()
        xq = {}
        for c in x264:
            q = interim.predict(curves[c], a, b, video_budget(limit, b - a, akb.get("x264", 0.0)))
            if q is not None:
                xq[(c.height, float(c.fps))] = q
        order, unprobed = vp9_order(interim, vp9, xq, limit, akb.get("vp9", 0.0))
        chosen = order if vp9_top is None or not x264 else order[:vp9_top]
        for i, c in enumerate(order):
            if c not in chosen:
                unprobed[c] = f"VP9 probe skipped: x264 ranks this geometry {i + 1} of {len(order)} feasible"
        add(next_crfs(chosen))
        for c in unprobed:
            curves.pop(c, None)
        lap("vp9 points")
    for c in curves.values():
        c.points.sort(key=lambda p: p.crf)
    reel.path.unlink(missing_ok=True)
    log("probe: " + ", ".join(f"{k} {v:.0f}s" for k, v in secs.items()))
    return FastProbe(build(), unprobed, secs)
