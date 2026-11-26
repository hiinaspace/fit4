"""Which libvpx settings lower the 2-pass bitrate floor on noisy footage?

usage: uv run python scripts/measure_floor.py CLIP SECONDS HEIGHT FPS KBPS[,KBPS...] [variant,...]
Prints, per variant and target, the bitrate libvpx actually produced, XPSNR, and wall time.
"""

import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

from fit4 import encode, ff
from fit4.metrics import score
from fit4.probe import probe, trim

VARIANTS = {
    "base": {},
    "cpu4": {"-cpu-used": "4"},
    "cpu2": {"-cpu-used": "2"},
    "cpu1": {"-cpu-used": "1"},
    "cpu0": {"-cpu-used": "0"},
    "aar0": {"-auto-alt-ref": "0", "-lag-in-frames": "0"},
    "cpu2-aar0": {"-cpu-used": "2", "-auto-alt-ref": "0", "-lag-in-frames": "0"},
    "cpu3-tpl0": {"-enable-tpl": "0"},
    "cpu2-aq3": {"-cpu-used": "2", "-aq-mode": "3"},
    "cpu2-st": {"-cpu-used": "2", "-static-thresh": "1000"},
    "cpu2-qmax": {"-cpu-used": "2", "-qmax": "63", "-qmin": "40"},
    "cpu2-sharp": {"-cpu-used": "2", "-sharpness": "7"},
    "cpu2-arnr": {"-cpu-used": "2", "-arnr-strength": "6", "-arnr-maxframes": "15"},
    "cpu2-minsec": {"-cpu-used": "2", "-minrate": "0", "-maxrate": "0"},
}

clip, secs, height, fps = Path(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3]), Fraction(sys.argv[4])
targets = [float(x) for x in sys.argv[5].split(",")]
names = sys.argv[6].split(",") if len(sys.argv) > 6 else list(VARIANTS)
src = trim(probe(clip), 0, secs, None)
cand = encode.Candidate("vp9", height, fps)
td = Path(tempfile.mkdtemp(dir="/tmp", prefix="floor-"))


def run(job):
    name, kbps = job
    base = [
        *ff.FFMPEG, "-v", "error", "-y", *src.in_args, "-i", str(src.path), "-vf", encode.norm_filter(src, height, fps),
        *encode.codec_args(cand, src, kbps, False, 4, VARIANTS[name]), *encode.COLOR_TAGS,
    ]  # fmt: skip
    log = td / f"{name}-{kbps:.0f}"
    out = td / f"{name}-{kbps:.0f}.webm"
    t0 = time.monotonic()
    ff.run([*base, "-an", "-pass", "1", "-passlogfile", str(log), "-f", "null", "-"])
    ff.run([*base, "-an", "-pass", "2", "-passlogfile", str(log), str(out)])
    dt = time.monotonic() - t0
    real = out.stat().st_size * 8 / src.duration / 1000
    sc = score(src, out, out.with_suffix(".x.txt"))
    return name, kbps, real, sc.xpsnr_yuv, dt


jobs = [(n, k) for k in targets for n in names]
with ThreadPoolExecutor(3) as ex:
    for name, kbps, real, q, dt in ex.map(run, jobs):
        print(f"{name:12s} target {kbps:5.0f} -> {real:6.0f} kbps  XPSNR {q:6.2f}  {dt:5.0f}s", flush=True)
