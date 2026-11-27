"""2-pass VP9 at the final bitrate with different libvpx flags, run side by side: time, size, XPSNR.

usage: uv run python scripts/measure_cpuused.py CLIP START SECONDS HEIGHT KBPS [name=flag,flag=val ...]
Each variant is `name:-flag=val,-flag=val` (empty flags = the defaults)."""

import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

from fit4 import encode
from fit4.metrics import score
from fit4.probe import probe, trim

clip, start, secs, height, kbps = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5])
variants = {}
for v in sys.argv[6:]:
    name, _, flags = v.partition(":")
    variants[name] = dict(f.split("=") for f in flags.split(",") if f)
src = trim(probe(clip), start, secs, None)
cand = encode.Candidate("vp9", height, src.fps)
work = Path(tempfile.mkdtemp(dir="/tmp", prefix="cpuused-"))


def run(item):
    name, ov = item
    out = work / f"{name}.webm"
    t0 = time.monotonic()
    size = encode.encode_once(src, cand, kbps, out, final=True, threads=4, audio=False, vp9_overrides=ov)
    dt = time.monotonic() - t0
    sc = score(src, out, out.with_suffix(".x.txt"))
    return name, dt, size, sc.xpsnr_yuv


with ThreadPoolExecutor(len(variants)) as ex:
    for name, dt, size, q in ex.map(run, variants.items()):
        print(f"{name:20s} {dt:6.1f}s  {size:>9,} B  XPSNR {q:.3f}", flush=True)
