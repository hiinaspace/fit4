"""Do cheaper encoder settings give probe curves that track the screening settings?

For a clip window, build the sample reel, then for each geometry and each setting variant
encode the reel at several CRFs and record (bitrate, XPSNR, seconds). analyse() then
compares each variant's curve with the reference variant: the dB gap at equal bitrate,
its mean per codec, and how much it varies across geometry and bitrate.

usage: uv run python scripts/measure_probe_fast.py CLIP START SECONDS OUT.json [slices slice_len]
       uv run python scripts/measure_probe_fast.py analyse OUT.json
"""

import itertools
import json
import math
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from fit4 import encode, ff, preflight
from fit4.layout import plan_layout, run_weighted
from fit4.metrics import score
from fit4.probe import probe, trim

GEOMS = [("x264", 1080, 30), ("x264", 720, 60), ("x264", 720, 30), ("x264", 540, 30)]
GEOMS += [("vp9", h, f) for _, h, f in GEOMS]
CRFS = {"x264": (24, 30, 36, 42, 48), "vp9": (32, 42, 52, 58, 63)}
# variant -> (codec, extra) ; first variant of each codec is the reference (what screening uses)
X264 = {"slow": "slow:rc-lookahead=60", "medium": "medium", "medium-la60": "medium:rc-lookahead=60"}
VP9 = {
    "cpu3": {},
    "cpu5": {"-cpu-used": "5"},
    "cpu5-tpl0": {"-cpu-used": "5", "-enable-tpl": "0"},
    "cpu5-tpl0-t8": {"-cpu-used": "5", "-enable-tpl": "0", "-tile-columns": "3"},
    "rt5": {"-deadline": "realtime", "-cpu-used": "5", "-lag-in-frames": "0", "-enable-tpl": "0"},
    "rt8": {"-deadline": "realtime", "-cpu-used": "8", "-lag-in-frames": "0", "-enable-tpl": "0"},
}


def args_for(cand, reel, variant, crf, threads):
    if cand.codec == "x264":
        preset, _, extra = X264[variant].partition(":")
        params = ":".join(["aq-mode=3", *([extra] if extra else [])])
        gop = max(1, min(round(float(cand.fps) * 20), math.ceil(reel.duration * float(cand.fps))))
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", str(gop), "-x264-params", params,
                "-threads", str(threads)]  # fmt: skip
    a = preflight.crf_args(cand, reel, crf, threads)
    for k, v in VP9[variant].items():
        a[a.index(k) + 1] = v
    return a


def one(job, threads):
    reel, work, cand, variant, crf = job
    ext = "mp4" if cand.codec == "x264" else "webm"
    out = work / f"{cand.label()}.{variant}.{crf}.{ext}"
    t0 = time.monotonic()
    ff.run([*ff.FFMPEG, "-v", "error", "-y", "-i", str(reel.path), "-vf", encode.norm_filter(reel, cand.height, cand.fps),
            "-an", *args_for(cand, reel, variant, crf, threads), str(out)])  # fmt: skip
    dt = time.monotonic() - t0
    sc = score(reel, out, out.with_suffix(".x.txt"))
    kbps = out.stat().st_size * 8 / reel.duration / 1000
    out.unlink()
    return {"label": cand.label(), "codec": cand.codec, "variant": variant, "crf": crf,
            "kbps": kbps, "xpsnr": sc.xpsnr_yuv, "secs": dt}  # fmt: skip


def collect(clip, start, secs, outp, n_slices=3, slice_len=2.0):
    src = trim(probe(Path(clip)), start, secs, None)
    work = Path(tempfile.mkdtemp(dir="/tmp", prefix="pfast-"))
    t0 = time.monotonic()
    slices = preflight.pick_slices(src, n_slices, slice_len)
    reel = preflight.build_reel(src, slices, work)
    t_reel = time.monotonic() - t0
    jobs = []
    for codec, h, f in GEOMS:
        cand = encode.Candidate(codec, h, Fraction(f))
        for variant in X264 if codec == "x264" else VP9:
            for crf in CRFS[codec]:
                jobs.append((reel, work, cand, variant, crf))
    lay = plan_layout(14, ["x264", "vp9"])
    rows = run_weighted(jobs, one, lay, lambda j: j[2].codec)
    Path(outp).write_text(json.dumps({"reel_secs": reel.duration, "reel_build": t_reel, "rows": rows}, indent=1))
    print("wrote", outp, f"{len(rows)} encodes, {time.monotonic() - t0:.0f}s")


def interp(curve, kbps):
    """XPSNR of a (kbps, xpsnr) curve at kbps by log-linear interpolation; None outside its range."""
    pts = sorted(curve)
    if not pts[0][0] <= kbps <= pts[-1][0]:
        return None
    for (k0, q0), (k1, q1) in itertools.pairwise(pts):
        if k0 <= kbps <= k1:
            t = (math.log(kbps) - math.log(k0)) / (math.log(k1) - math.log(k0))
            return q0 + t * (q1 - q0)
    return None


def analyse(path):
    """Per codec and variant: dB the reference variant gains over this one at equal bitrate, and the time ratio."""
    rows = json.loads(Path(path).read_text())["rows"]
    by = {}
    for r in rows:
        by.setdefault((r["codec"], r["label"], r["variant"]), []).append(r)
    ref = {"x264": "slow", "vp9": "cpu3"}
    for codec in ("x264", "vp9"):
        variants = list(X264 if codec == "x264" else VP9)
        for v in variants[1:]:
            gaps, per_geom, tr = [], {}, []
            for (c, label, var), rs in by.items():
                if c != codec or var != v:
                    continue
                refrs = by[(c, label, ref[codec])]
                vc = [(r["kbps"], r["xpsnr"]) for r in rs]
                for r in refrs:
                    q = interp(vc, r["kbps"])
                    if q is not None:
                        gaps.append(r["xpsnr"] - q)
                        per_geom.setdefault(label, []).append(r["xpsnr"] - q)
                tr.append(sum(r["secs"] for r in refrs) / sum(r["secs"] for r in rs))
            if gaps:
                m = sum(gaps) / len(gaps)
                sd = (sum((g - m) ** 2 for g in gaps) / len(gaps)) ** 0.5
                gm = {k: round(sum(g) / len(g), 2) for k, g in per_geom.items()}
                print(f"{codec} {v:11s} ref gains {m:+.2f} dB (sd {sd:.2f}, range {min(gaps):+.2f}..{max(gaps):+.2f}) "
                      f"speedup x{sum(tr) / len(tr):.1f}; per geometry {gm}")  # fmt: skip


if __name__ == "__main__":
    a = sys.argv
    if a[1] == "analyse":
        analyse(a[2])
    else:
        collect(
            a[1], float(a[2]), float(a[3]), a[4], int(a[5]) if len(a) > 5 else 3, float(a[6]) if len(a) > 6 else 2.0
        )
