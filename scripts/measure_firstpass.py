"""Measure first-pass reuse: byte-identity of pass 2 and the time pass 1 costs.

usage: uv run python scripts/measure_firstpass.py CLIP [seconds] [height]
"""

import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from fit4 import encode
from fit4.metrics import score
from fit4.probe import probe, trim

clip = Path(sys.argv[1])
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 8
height = int(sys.argv[3]) if len(sys.argv) > 3 else 540
src = trim(probe(clip), 0, secs, None)
fps = Fraction(30)


def sha(p: Path) -> str:
    """md5 of the packets (the container header carries a random segment UID, so hash the payload)."""
    r = encode.ff.run([*encode.ff.FFMPEG, "-v", "error", "-i", str(p), "-c", "copy", "-f", "md5", "-"], capture=True)
    return r.stdout.strip().split("=")[-1][:10]


with tempfile.TemporaryDirectory(dir="/tmp") as td:
    td = Path(td)
    for codec in ("vp9", "x264"):
        cand = encode.Candidate(codec, height, fps)
        for final in (False,) if codec == "vp9" else (False, True):
            threads = 4
            kb_a, kb_b = float(sys.argv[4]) if len(sys.argv) > 4 else 600.0, 450.0
            t0 = time.monotonic()
            encode.run_first_pass(src, cand, kb_a, td / "fpA", final=final, threads=threads)
            t_p1 = time.monotonic() - t0
            encode.run_first_pass(src, cand, kb_b, td / "fpB", final=final, threads=threads)
            outs = {}
            for name, prefix, kb in (
                ("fresh_B", "fpB", kb_b),
                ("reuse_A_for_B", "fpA", kb_b),
                ("fresh_A", "fpA", kb_a),
            ):
                out = td / f"{codec}{final}.{name}.{'webm' if codec == 'vp9' else 'mp4'}"
                t0 = time.monotonic()
                base = encode._video_cmd(src, cand, kb, final, threads)
                mux = ["-movflags", "+faststart"] if codec == "x264" else []
                encode.ff.run([*base, "-an", "-pass", "2", "-passlogfile", str(td / prefix), *mux, str(out)])
                outs[name] = (
                    out.stat().st_size,
                    sha(out),
                    time.monotonic() - t0,
                    score(src, out, out.with_suffix(".x.txt")).xpsnr_yuv,
                )
            # same stats rerun twice: is pass 2 itself deterministic?
            out2 = td / "again.webm"
            base = encode._video_cmd(src, cand, kb_b, final, threads)
            encode.ff.run(
                [
                    *base,
                    "-an",
                    "-pass",
                    "2",
                    "-passlogfile",
                    str(td / "fpB"),
                    *(["-movflags", "+faststart"] if codec == "x264" else []),
                    str(out2.with_suffix(".webm" if codec == "vp9" else ".mp4")),
                ]
            )
            again = out2.with_suffix(".webm" if codec == "vp9" else ".mp4")
            print(f"{codec} final={final}: pass1 {t_p1:.1f}s")
            for k, v in outs.items():
                print(f"  {k:14s} size {v[0]:8d} sha {v[1]} pass2 {v[2]:.1f}s XPSNR {v[3]:.3f}")
            print(f"  fresh_B rerun  size {again.stat().st_size:8d} sha {sha(again)}")
