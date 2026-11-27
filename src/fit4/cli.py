"""Command line: argument parsing, input checks, exit codes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from . import fastprobe, ff, preflight, prune, report
from .boards import BOARDS, Board
from .encode import EXT, FLOOR_FIX, Candidate, FirstPassCache, budget_kbps
from .layout import available_cpus, plan_layout
from .probe import BadInput, Source, probe, trim
from .search import Trial, grid, pick_by_prediction, pick_finalists, run_finals, run_onepass, run_trials

# Exit codes (also in --help and the README).
OK, FAILED, USAGE, NO_FFMPEG, INTERRUPTED = 0, 1, 2, 3, 130
# Shorter clips screen the whole grid: the probe would cost about as much as it saves.
PROBE_MIN_SECONDS = 10.0
# Below this video bitrate neither encoder produces anything watchable.
MIN_VIDEO_KBPS = 30
# Finals land in [FINAL_LOW * limit, limit] within FINAL_ROUNDS rounds of side-by-side 2-pass encodes.
FINAL_LOW = 0.97
FINAL_ROUNDS = 3

SCREEN_MODES = ("2pass", "crf", "none")
DEFAULT_SCREEN = "none"

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


def parse_reel(text: str) -> tuple[int, float]:
    m = re.fullmatch(r"(\d+)x(\d+(?:\.\d+)?)", text.strip())
    if not m or int(m.group(1)) < 1 or float(m.group(2)) <= 0:
        raise argparse.ArgumentTypeError(f"{text!r}: want NxS, e.g. 3x2 for three 2-second slices")
    return int(m.group(1)), float(m.group(2))


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
    ap.add_argument(
        "--finals-margin",
        type=float,
        default=0.5,
        metavar="DB",
        help="with --screen none or crf, a second final (up to --finals) only when it is predicted within this many "
        "dB of the best (default: %(default)s)",
    )
    ap.add_argument(
        "--jobs",
        type=positive,
        help="most encodes at once (default: as many as the CPU budget allows; with --flat-layout, 4)",
    )
    ap.add_argument(
        "--flat-layout",
        action="store_true",
        help="old scheduling: --jobs equal workers sharing the CPUs evenly, instead of VP9 jobs of 4 threads side by side",
    )
    ap.add_argument("--start", type=parse_time, default=0.0, help="trim: start here (seconds, M:SS or H:MM:SS)")
    ap.add_argument("--duration", type=parse_time, help="trim: encode only this much from --start (e.g. 51)")
    ap.add_argument("--end", type=parse_time, help="trim: stop here instead of using --duration")
    ap.add_argument(
        "--full-grid",
        action="store_true",
        help="screen every candidate: skip the pre-flight probe and its pruning (the old behaviour)",
    )
    ap.add_argument(
        "--prune-margin",
        type=float,
        default=prune.DEFAULT_MARGIN_DB,
        metavar="DB",
        help="screen candidates the probe predicts within this many dB of the best (default: %(default)s)",
    )
    ap.add_argument(
        "--prune-keep",
        type=positive,
        default=prune.DEFAULT_MIN_KEEP,
        metavar="N",
        help="but always screen at least the best N predicted (default: %(default)s)",
    )
    ap.add_argument(
        "--probe-reel",
        type=parse_reel,
        default=fastprobe.DEFAULT_REEL,
        metavar="NxS",
        help="probe sample: N slices of S seconds each (default: %(default)s)",
    )
    ap.add_argument(
        "--probe-only",
        action="store_true",
        help="run the pre-flight probe, write probe.json and the pruning decisions to the work directory, and stop",
    )
    ap.add_argument(
        "--screen",
        choices=SCREEN_MODES,
        default=DEFAULT_SCREEN,
        help="how the probe's survivors are ranked before the finals: 2pass = size-fitted 2-pass encodes (the old "
        "screening), crf = one constant-quality encode each at the CRF the probe predicts, none = trust the probe's "
        "prediction and final-encode its top --finals (default: %(default)s; crf and none need the probe)",
    )
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


def run_final_stage(src, fin, limit, work, budget, audio, cache):
    """Size-fit the finalists; if none gets under the limit, retry with libvpx's floor-lowering flags (slow)."""

    def fit(cs, overrides=None):
        return run_finals(
            src, cs, limit, work, budget=budget, audio=audio, low=FINAL_LOW, max_rounds=FINAL_ROUNDS, log=log,
            cache=cache, overrides=overrides,
        )  # fmt: skip

    finals = fit(fin)
    if not finals and any(c.codec == "vp9" for c in fin):
        log("no final fit; retrying VP9 with cpu-used 2 (slower)")
        finals = fit([c for c in fin if c.codec == "vp9"], FLOOR_FIX)
    if not finals:
        raise RuntimeError("no final encode got under the size limit; try a larger --limit or a shorter clip")
    return finals


def run_onepass_stage(src, cands, limit, work, pf, **kw):
    with Progress(
        TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(), console=CONSOLE,
        transient=True,
    ) as prog:  # fmt: skip
        task = prog.add_task("screening (1 pass)", total=len(cands))
        return run_onepass(src, cands, limit, work, pf, log=log, on_done=lambda: prog.advance(task), **kw)


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


def preflight_prune(
    src: Source, cands: list[Candidate], limit: int, work: Path, layout, audio: bool, args: argparse.Namespace
) -> tuple[list[Candidate], list[Candidate], preflight.Probe | None, dict[str, float]]:
    """Run the fast probe and split the grid into (screen these, skipped, the probe). Falls back to the full grid on failure."""
    t0 = time.monotonic()
    audio_kbps = preflight.audio_kbps_for(src, audio)
    try:
        fp = fastprobe.run_fast_probe(
            src,
            cands,
            limit,
            work / "probe",
            layout,
            audio_kbps=audio_kbps,
            n_slices=args.probe_reel[0],
            slice_len=args.probe_reel[1],
            log=log,
        )
    except (subprocess.CalledProcessError, RuntimeError) as e:
        log(f"probe failed ({e}); screening the full grid")
        return cands, [], None, {}
    (work / "probe.json").write_text(fp.probe.to_json())
    decisions = prune.decide(
        fp.probe, cands, limit, audio_kbps=audio_kbps, offsets=fastprobe.OFFSET_DB,
        margin=args.prune_margin, min_keep=args.prune_keep, unprobed=fp.unprobed,
    )  # fmt: skip
    kept = [d.cand for d in decisions if d.keep]
    log(f"probe: {time.monotonic() - t0:.0f}s; keeping {len(kept)} of {len(cands)} candidates")
    for d in sorted(decisions, key=lambda d: (not d.keep, -(d.predicted or -1e9))):
        log("  " + d.line())
    (work / "prune.json").write_text(
        json.dumps(
            [
                {"label": d.cand.label(), "keep": d.keep, "reason": d.reason, "predicted": d.predicted}
                for d in decisions
            ],
            indent=1,
        )
    )
    if not kept:
        log("probe kept nothing; screening the full grid")
        return cands, [], None, {}
    pred = {d.cand.label(): d.predicted for d in decisions if d.predicted is not None}
    return kept, [d.cand for d in decisions if not d.keep], fp.probe, pred


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
    cpus = (os.cpu_count() or 4) if args.flat_layout else available_cpus()
    layout = plan_layout(cpus, codecs, args.jobs, args.flat_layout)
    skipped: list[Candidate] = []
    pf: preflight.Probe | None = None
    pred: dict[str, float] = {}
    if args.probe_only or not (args.full_grid or src.duration < PROBE_MIN_SECONDS):
        kept, skipped, pf, pred = preflight_prune(src, cands, limit, work, layout, audio, args)
        if args.probe_only:
            return OK
        cands = kept
    elif not args.full_grid:
        log(f"clip under {PROBE_MIN_SECONDS:g}s: screening the full grid")
    mode = args.screen if pf is not None else "2pass"
    cache = FirstPassCache(work / ".firstpass")
    screen: list[Trial] = []
    try:
        if mode == "none":
            # The probe's prediction ranks the survivors; only its top --finals are encoded.
            ranked = sorted(((c, pred[c.label()]) for c in cands if c.label() in pred), key=lambda t: -t[1])
            fin = pick_by_prediction(ranked, args.finals, args.finals_margin) or cands[: args.finals]
            log(f"no screening: final encodes of the probe's top {len(fin)}: {', '.join(c.label() for c in fin)}")
        else:
            log(f"screening {len(cands)} candidates ({mode}; {layout.describe()})")
            try:
                if mode == "crf" and pf is not None:
                    screen = run_onepass_stage(src, cands, limit, work, pf, layout=layout, audio=audio)
                else:
                    screen = run_stage(
                        "screening", src, cands, limit, work, final=False, layout=layout, audio=audio, cache=cache
                    )
            except RuntimeError:
                if not skipped:
                    raise
                log(f"no pruned candidate fit; screening the {len(skipped)} the probe skipped")
                screen = run_stage(
                    "screening", src, skipped, limit, work, final=False, layout=layout, audio=audio, cache=cache
                )
            if mode == "crf":
                ranked = sorted(((t.cand, t.key) for t in screen), key=lambda t: -t[1])
                fin = pick_by_prediction(ranked, args.finals, args.finals_margin)
            else:
                fin = pick_finalists(screen, args.finals)
            log(f"final encodes: {', '.join(c.label() for c in fin)}")
        finals = run_final_stage(src, fin, limit, work, layout.budget, audio, cache)
        if not finals and mode == "none":
            log("no final fit; screening the rest of the grid")
            rest = [c for c in cands + skipped if c not in fin]
            screen = run_stage(
                "screening", src, rest, limit, work, final=False, layout=layout, audio=audio, cache=cache
            )
            fin = pick_finalists(screen, args.finals)
            finals = run_final_stage(src, fin, limit, work, layout.budget, audio, cache)
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
            "floor_fix": t.enc.floor_fix,
            "rank_db": None if t.rank is None else round(t.rank, 3),
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
