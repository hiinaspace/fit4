"""fit4: squeeze a clip under an upload size limit at the best measured quality."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import report
from .boards import BOARDS
from .encode import EXT
from .probe import probe
from .search import grid, pick_finalists, run_trials


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fit4", description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="default: <input stem>.fit4.<ext> next to input")
    ap.add_argument("--board", choices=sorted(BOARDS), default="4chan")
    ap.add_argument("--limit", type=int, help="override byte limit")
    ap.add_argument("--codecs", help="comma list (x264,vp9); default: the board's")
    ap.add_argument("--scales", default="1,0.833,0.667,0.5", help="height scales to screen")
    ap.add_argument("--fps-divs", default="1,2", help="framerate divisors to screen")
    ap.add_argument("--finals", type=int, default=2, help="slow encodes of the leaders")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--work", type=Path, help="work/report dir; default: <input stem>.fit4.d next to input")
    args = ap.parse_args(argv)

    board = BOARDS[args.board]
    limit = args.limit or board.limit_bytes
    codecs = args.codecs.split(",") if args.codecs else list(board.codecs)
    src = probe(args.input)
    audio = not args.no_audio
    log(
        f"{src.path.name}: {src.width}x{src.height} {float(src.fps):g}fps {src.duration:.1f}s "
        f"{src.size / 1e6:.1f}MB{' +audio' if src.has_audio else ''}; limit {limit:,} B "
        f"→ {limit * 8 / src.duration / 1000:.0f} kbps total"
    )
    if board.max_duration and src.duration > board.max_duration:
        log(f"warning: {src.duration:.0f}s exceeds {board.name}'s reported {board.max_duration:.0f}s max")

    stem = args.input.stem
    work = args.work or args.input.with_name(f"{stem}.fit4.d")
    work.mkdir(parents=True, exist_ok=True)

    cands = grid(
        src,
        board,
        codecs,
        [float(s) for s in args.scales.split(",")],
        [int(d) for d in args.fps_divs.split(",")],
    )
    t0 = time.monotonic()
    log(f"screening {len(cands)} candidates ({args.jobs} at a time, fast presets)")
    screen = run_trials(src, cands, limit, work, final=False, jobs=args.jobs, audio=audio, log=log)
    fin = pick_finalists(screen, args.finals)
    log(f"final encodes: {', '.join(c.label() for c in fin)}")
    finals = run_trials(src, fin, limit, work, final=True, jobs=args.jobs, audio=audio, log=log)
    chosen = max(finals, key=lambda t: t.score.primary)

    output = args.output or args.input.with_name(f"{stem}.fit4.{EXT[chosen.cand.codec]}")
    output.write_bytes(chosen.enc.path.read_bytes())
    for t in screen:  # screening encodes are only needed for their scores
        t.enc.path.unlink(missing_ok=True)
    rpt = report.write(work, src, limit, screen, finals, chosen, output)
    results = [
        {
            "stage": stage,
            "label": t.cand.label(),
            "bytes": t.enc.size,
            "kbps": round(t.enc.kbps, 1),
            "xpsnr_yuv": round(t.score.xpsnr_yuv, 3),
            "xpsnr_y": round(t.score.xpsnr_y, 3),
            "ssim_y": round(t.score.ssim_y, 5),
            "seconds": round(t.enc.seconds, 1),
            "attempts": t.enc.attempts,
        }
        for stage, ts in (("screen", screen), ("final", finals))
        for t in ts
    ]
    (work / "results.json").write_text(json.dumps(results, indent=1))
    log(
        f"chose {chosen.cand.label()}: {chosen.enc.size:,} B, XPSNR {chosen.score.xpsnr_yuv:.2f} "
        f"({time.monotonic() - t0:.0f}s total)\n  {output}\n  {rpt}"
    )
    return 0
