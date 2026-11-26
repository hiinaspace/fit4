"""One-pass screening: rank candidates from one constant-quality encode each.

The 2-pass screening loop spends a first pass plus one to three second passes
per candidate to land a size near the limit, only to rank the candidates. Here
each candidate is encoded once at the CRF the pre-flight curve says lands on
its budget; the measured (size, XPSNR) point is moved to the budget along the
curve's local slope (dB per ln-bitrate), and that adjusted score ranks the
candidates. Finals still run the true size-fitted 2-pass encode.
"""

from __future__ import annotations

import math
import time
from itertools import pairwise
from pathlib import Path

from . import ff
from .encode import EXT, Candidate, EncodeResult, _video_cmd, budget_kbps
from .metrics import Score, score
from .preflight import CandCurve, Probe
from .probe import Source

CRF_LIMITS = {"x264": (10, 51), "vp9": (10, 63)}
# dB per ln(bitrate) bounds for the size correction: the probe's local slope is noisy on 3 points.
SLOPE_BOUNDS = (2.0, 9.0)
DEFAULT_SLOPE = 5.0


def curve_points(probe: Probe, curve: CandCurve) -> list[tuple[int, float, float, float]]:
    """(crf, kbps over the whole window, xpsnr) sorted by crf; 4-tuples (crf, ln kbps, xpsnr, kbps)."""
    a, b = probe.start, probe.start + probe.duration
    pts = [
        (p.crf, math.log(probe.kbps_at(curve, p, a, b)), p.xpsnr, probe.kbps_at(curve, p, a, b)) for p in curve.points
    ]
    return sorted(pts)


def predict_crf(probe: Probe, curve: CandCurve, kbps: float) -> tuple[int, float]:
    """(CRF, local dB per ln-bitrate) that should land at `kbps` for the whole probed window.

    Interpolates ln-bitrate against CRF (linear), extrapolating along the
    nearest segment, and clamps to the codec's usable CRF range.
    """
    pts = curve_points(probe, curve)
    x = math.log(kbps)
    segs = list(pairwise(pts))
    seg = segs[-1]
    for s in segs:
        if x >= s[1][1]:  # bitrate falls with CRF, so the points run high bitrate -> low
            seg = s
            break
    (c0, x0, q0, _), (c1, x1, q1, _) = seg
    if x1 == x0:
        crf = float(c0)
        slope = DEFAULT_SLOPE
    else:
        crf = c0 + (x - x0) * (c1 - c0) / (x1 - x0)
        slope = (q0 - q1) / (x0 - x1) if x0 != x1 else DEFAULT_SLOPE
    lo, hi = CRF_LIMITS[curve.codec]
    return int(min(hi, max(lo, round(crf)))), min(SLOPE_BOUNDS[1], max(SLOPE_BOUNDS[0], slope))


def crf_cmd(src: Source, cand: Candidate, crf: int, threads: int, out: Path) -> list[str]:
    base = _video_cmd(src, cand, 1000, False, threads)
    i = base.index("-b:v")
    base[i : i + 2] = ["-crf", str(crf)] if cand.codec == "x264" else ["-b:v", "0", "-crf", str(crf)]
    return [*base, "-an", str(out)]


def adjust_db(xpsnr: float, size: int, budget_bytes: float, slope: float) -> float:
    """Move a measured point to the budget size: +slope dB per ln of the size ratio."""
    return xpsnr + slope * math.log(budget_bytes / size)


def screen_one(
    src: Source,
    cand: Candidate,
    probe: Probe,
    curve: CandCurve,
    limit: int,
    work: Path,
    threads: int,
    audio: bool = False,
) -> tuple[EncodeResult, Score, float]:
    """Encode `cand` once at the predicted CRF; returns (encode, score, budget-adjusted dB)."""
    kbps = budget_kbps(src, cand, limit, audio)
    crf, slope = predict_crf(probe, curve, kbps)
    out = work / f"{cand.label()}.screen.{EXT[cand.codec]}"
    t0 = time.monotonic()
    ff.run(crf_cmd(src, cand, crf, threads, out))
    sec = time.monotonic() - t0
    size = out.stat().st_size
    sc = score(src, out, out.with_suffix(".xpsnr.txt"))
    enc = EncodeResult(cand, out, size * 8 / src.duration / 1000, size, sec, [(float(crf), size)])
    return enc, sc, adjust_db(sc.xpsnr_yuv, size, kbps * 1000 / 8 * src.duration, slope)
