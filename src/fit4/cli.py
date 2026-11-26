"""Command line: argument parsing, input checks, exit codes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from . import ff, report
from .boards import BOARDS, Board
from .encode import EXT, Candidate, FirstPassCache, budget_kbps
from .probe import BadInput, Source, probe, trim
from .search import grid, pick_finalists, run_trials

# Exit codes (also in --help and the README).
OK, FAILED, USAGE, NO_FFMPEG, INTERRUPTED = 0, 1, 2, 3, 130
# Below this video bitrate neither encoder produces anything watchable.
MIN_VIDEO_KBPS = 30

EPILOG = """\
examples:
  fit4 clip.mp4                          4 MiB, full sweep
  fit4 clip.mp4 --limit 10MB             any byte limit (B, KB, KiB, MB, MiB)
  fit4 clip.mp4 --codecs x264 --scales 1,0.667 --fps-divs 1 --finals 1
                                         quick: 2 screens + 1 final

time: the default sweep is 16 screening encodes plus 2 slow finals. A 49 s
1080p60 clip took 12 minutes on 14 cores, three quarters of it in VP9, so
--codecs x264 is the biggest saving. Cost scales with clip length and with
--codecs x --scales x --fps-divs.

exit codes: 0 done (or nothing to do), 1 an encode failed or nothing fit,
2 bad arguments or unusable input, 3 ffmpeg missing or lacking a feature,
130 interrupted.
"""

_UNITS = {"": 1, "b": 1, "kb": 1000, "kib": 1024, "mb": 1000**2, "mib": 1024**2, "gb": 1000**3, "gib": 1024**3}


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", text)
    unit = m and m.group(2).lower()
    if not m or unit not in _UNITS:
        raise argparse.ArgumentTypeError(f"{text!r}: want bytes, or a number with B, KB, KiB, MB or MiB")
    n = int(float(m.group(1)) * _UNITS[unit])
    if n <= 0:
        raise argparse.ArgumentTypeError("limit must be positive")
    return n


def _csv(conv, check, what):
    def parse(text: str):
        try:
            vals = [conv(x) for x in text.split(",") if x.strip()]
        except ValueError:
            vals = []
        if not vals or not all(check(v) for v in vals):
            raise argparse.ArgumentTypeError(f"{text!r}: want a comma list of {what}")
        return vals

    return parse


parse_codecs = _csv(str.strip, lambda c: c in EXT, f"codecs from {', '.join(EXT)}")
parse_scales = _csv(float, lambda s: 0 < s <= 1, "height scales in (0, 1]")
parse_divs = _csv(int, lambda d: d >= 1, "whole framerate divisors >= 1")


def parse_time(text: str) -> float:
    """Seconds, M:SS or H:MM:SS (fractions allowed)."""
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        parts = []
    if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
        raise argparse.ArgumentTypeError(f"{text!r}: want seconds, M:SS or H:MM:SS")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def positive(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text!r}: want a whole number >= 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    try:
        ver = version("fit4")
    except PackageNotFoundError:
        ver = "unknown"
    ap = argparse.ArgumentParser(
        prog="fit4",
        description="Squeeze a video under an upload size limit at the best measured quality. "
        "Screens x264 and VP9 at several sizes and frame rates, scores each with XPSNR/SSIM, "
        "final-encodes the leaders, and writes the winner plus an A/B report.html.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", type=Path, help="source video (anything ffmpeg can read)")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file; default <input>.fit4.mp4|webm. A .mp4/.webm suffix picks the codec",
    )
    ap.add_argument("--limit", type=parse_size, help="size limit, e.g. 4MiB, 10MB, 8000000; default 4MiB")
    ap.add_argument(
        "--board",
        choices=sorted(BOARDS),
        default="4chan",
        help="upload preset: size limit, max dimension, duration warning (default: %(default)s); --limit overrides its size",
    )
    ap.add_argument("--codecs", type=parse_codecs, help="comma list from x264,vp9 (default: both)")
    ap.add_argument(
        "--scales",
        type=parse_scales,
        default="1,0.833,0.667,0.5",
        help="height scales to screen (default: %(default)s)",
    )
    ap.add_argument(
        "--fps-divs", type=parse_divs, default="1,2", help="frame rate divisors to screen (default: %(default)s)"
    )
    ap.add_argument(
        "--finals", type=positive, default=2, help="slow-preset encodes of the leaders (default: %(default)s)"
    )
    ap.add_argument("--jobs", type=positive, default=4, help="encodes run at once; CPU threads are split between them")
    ap.add_argument("--start", type=parse_time, default=0.0, help="trim: start here (seconds, M:SS or H:MM:SS)")
    ap.add_argument("--duration", type=parse_time, help="trim: encode only this much from --start (e.g. 51)")
    ap.add_argument("--end", type=parse_time, help="trim: stop here instead of using --duration")
    ap.add_argument("--no-audio", action="store_true", help="drop the audio track (frees its bits for video)")
    ap.add_argument("--force", action="store_true", help="re-encode even if the source already fits")
    ap.add_argument("--work", type=Path, help="work and report directory; default <input>.fit4.d next to the input")
    ap.add_argument("--version", action="version", version=f"%(prog)s {ver}")
    return ap


CONSOLE = Console(stderr=True, highlight=False)


def log(msg: str) -> None:
    CONSOLE.print(msg, markup=False, soft_wrap=True)


def run_stage(name: str, src, cands, limit, work, **kw):
    """run_trials under a progress bar (one per stage, so the ETA isn't skewed by slower final presets)."""
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        console=CONSOLE,
        transient=True,
    ) as prog:
        task = prog.add_task(name, total=len(cands))
        return run_trials(src, cands, limit, work, log=log, on_done=lambda: prog.advance(task), **kw)


def already_fits(src: Source, board: Board, limit: int, audio: bool) -> bool:
    """True when the source could be uploaded as-is: under the limit, in a format the target takes."""
    if src.size > limit or max(src.width, src.height) > board.max_dim:
        return False
    ext = src.path.suffix.lower()
    ok_video = (ext == ".mp4" and src.video_codec == "h264") or (ext == ".webm" and src.video_codec in ("vp8", "vp9"))
    if not src.has_audio or not audio:
        # With --no-audio a source that has audio still needs a re-encode to drop it.
        return ok_video and not src.has_audio
    ok_audio = src.audio_codec == ("aac" if ext == ".mp4" else "opus") or (
        ext == ".webm" and src.audio_codec == "vorbis"
    )
    return ok_video and ok_audio


def codecs_for(args: argparse.Namespace, board: Board) -> list[str]:
    codecs = args.codecs or list(board.codecs)
    if args.output and args.output.suffix.lower() in (".mp4", ".webm"):
        want = [c for c, e in EXT.items() if "." + e == args.output.suffix.lower()]
        if args.codecs and not set(codecs) & set(want):
            raise BadInput(f"--output {args.output.name} needs codec {want[0]}, but --codecs is {','.join(codecs)}")
        codecs = [c for c in codecs if c in want]
    elif args.output:
        raise BadInput(f"--output {args.output.name}: use a .mp4 (x264) or .webm (vp9) name")
    return codecs


def run(args: argparse.Namespace) -> int:
    board = BOARDS[args.board]
    limit = args.limit or board.limit_bytes
    audio = not args.no_audio
    codecs = codecs_for(args, board)
    if not args.input.is_file():
        raise BadInput(f"{args.input}: no such file")
    ff.require_binaries()
    src = trim(probe(args.input), args.start, args.duration, args.end)
    log(
        f"{src.path.name}: {src.width}x{src.height} {float(src.fps):g}fps {src.duration:.1f}s "
        f"{f'(trimmed from {src.start:g}s) ' if src.trimmed else ''}{src.size / 1e6:.1f}MB{' +audio' if src.has_audio else ''}; limit {limit:,} B "
        f"→ {limit * 8 / src.duration / 1000:.0f} kbps total"
    )
    if not src.trimmed and already_fits(src, board, limit, audio) and not args.force:
        log(
            f"{src.path.name} already fits ({src.size:,} B) in an uploadable format; nothing to do (--force re-encodes)"
        )
        return OK
    if not src.trimmed and src.size <= limit:
        log("note: the source is under the limit, but its format or dimensions need a re-encode")
    if board.max_duration and src.duration > board.max_duration:
        log(f"warning: {src.duration:.0f}s exceeds {board.name}'s reported {board.max_duration:.0f}s max")
    ff.check(codecs, audio and src.has_audio)

    lowest = min(budget_kbps(src, Candidate(c, src.height, src.fps), limit, audio) for c in codecs)
    if lowest < MIN_VIDEO_KBPS:
        hint = "; --no-audio frees the audio's share" if audio and src.has_audio else ""
        raise BadInput(
            f"{src.duration:.0f}s leaves about {max(lowest, 0):.0f} kbps for video under {limit:,} B; "
            f"trim the clip or raise --limit{hint}"
        )

    stem = args.input.stem
    work = args.work or args.input.with_name(f"{stem}.fit4.d")
    work.mkdir(parents=True, exist_ok=True)
    cands = grid(src, board, codecs, args.scales, args.fps_divs)
    t0 = time.monotonic()
    log(f"screening {len(cands)} candidates ({args.jobs} at a time, fast presets)")
    cache = FirstPassCache(work / ".firstpass")
    try:
        screen = run_stage("screening", src, cands, limit, work, final=False, jobs=args.jobs, audio=audio, cache=cache)
        fin = pick_finalists(screen, args.finals)
        log(f"final encodes: {', '.join(c.label() for c in fin)}")
        finals = run_stage("final", src, fin, limit, work, final=True, jobs=args.jobs, audio=audio, cache=cache)
    finally:
        log(f"first passes: {cache.runs} run, {cache.hits} reused")
        cache.clear()
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
        f"({time.monotonic() - t0:.0f}s total)\n  report: {rpt}"
    )
    print(output)  # stdout carries only the result path, for scripts
    return OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except BadInput as e:
        log(f"fit4: {e}")
        return USAGE
    except ff.MissingFFmpeg as e:
        log(f"fit4: {e}")
        return NO_FFMPEG
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or "").strip().splitlines()[-5:] if isinstance(e.stderr, str) else []
        log(f"fit4: ffmpeg failed (exit {e.returncode}): {' '.join(map(str, e.cmd))[:300]}")
        for line in tail:
            log(f"  {line}")
        return FAILED
    except RuntimeError as e:  # e.g. an encode never got under the limit
        log(f"fit4: {e}")
        return FAILED
    except KeyboardInterrupt:
        log("fit4: interrupted")
        return INTERRUPTED
