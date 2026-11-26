"""Where does one probe point's time go? decode+scale floor, encoder variants, scoring.

usage: uv run python scripts/profile_probe_point.py CLIP START SECONDS [slices slice_len]
"""

import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from fit4 import encode, ff, preflight
from fit4.metrics import distorted_filter, reference_filter, score
from fit4.probe import probe, trim

clip, start, secs = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
n, ln = (int(sys.argv[4]), float(sys.argv[5])) if len(sys.argv) > 5 else (3, 2.0)
src = trim(probe(clip), start, secs, None)
work = Path(tempfile.mkdtemp(dir="/tmp", prefix="prof-"))


def timed(label, args):
    t0 = time.monotonic()
    r = ff.run(args, capture=True)
    dt = time.monotonic() - t0
    print(f"{label:46s} {dt:6.2f}s", flush=True)
    return r


t0 = time.monotonic()
slices = preflight.pick_slices(src, n, ln)
reel = preflight.build_reel(src, slices, work)
print(
    f"reel {n}x{ln}s build {time.monotonic() - t0:.2f}s, {reel.path.stat().st_size / 1e6:.0f} MB, {reel.width}x{reel.height}"
)
cand = encode.Candidate("vp9", 720, Fraction(60))
vf = encode.norm_filter(reel, cand.height, cand.fps)
base = [*ff.FFMPEG, "-v", "error", "-y", "-i", str(reel.path)]
timed("decode only (null)", [*base, "-an", "-f", "null", "-"])
timed("decode + scale 720p60 (null)", [*base, "-an", "-vf", vf, "-f", "null", "-"])
out = work / "o.webm"
for name, ov, th in [
    ("vp9 good cpu3 t4", {}, 4),
    ("vp9 good cpu5 t4", {"-cpu-used": "5"}, 4),
    ("vp9 good cpu5 t8", {"-cpu-used": "5"}, 8),
    ("vp9 good cpu5 t4 tile-cols 0", {"-cpu-used": "5", "-tile-columns": "0"}, 4),
    ("vp9 rt cpu5 t4", {"-deadline": "realtime", "-cpu-used": "5", "-lag-in-frames": "0"}, 4),
    ("vp9 rt cpu8 t4", {"-deadline": "realtime", "-cpu-used": "8", "-lag-in-frames": "0"}, 4),
]:
    a = preflight.crf_args(cand, reel, 52, th)
    for k, v in ov.items():
        a[a.index(k) + 1] = v
    timed(name, [*base, "-an", "-vf", vf, *a, str(out)])
    if name == "vp9 good cpu5 t4":
        print(f"  size {out.stat().st_size}")
c264 = encode.Candidate("x264", 720, Fraction(60))
for preset in ("slow", "medium", "veryfast"):
    a = preflight.crf_args(c264, reel, 32, 4)
    a[a.index("-preset") + 1] = preset
    timed(f"x264 {preset} t4", [*base, "-an", "-vf", vf, *a, str(work / "o.mp4")])
dist = out
st = work / "x.txt"
lav_x = f"[0:v]{distorted_filter(reel)}[d];[1:v]{reference_filter(reel)}[r];[d][r]xpsnr=shortest=1:stats_file={st}"
timed(
    "score: xpsnr only",
    [*ff.FFMPEG, "-nostats", "-i", str(dist), "-i", str(reel.path), "-lavfi", lav_x, "-f", "null", "-"],
)
t0 = time.monotonic()
score(reel, dist, st)
print(f"{'score: xpsnr + ssim (current)':46s} {time.monotonic() - t0:6.2f}s")
