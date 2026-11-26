"""Build and run size-targeted ffmpeg encodes."""

from __future__ import annotations

import hashlib
import math
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from . import ff
from .probe import Source

# Audio bitrate reserved when the source has audio (kbps).
AUDIO_KBPS = {"x264": 96, "vp9": 64}
EXT = {"x264": "mp4", "vp9": "webm"}
# Rough container + muxing overhead as a fraction of payload.
OVERHEAD = 0.006


@dataclass(frozen=True)
class Candidate:
    codec: str  # "x264" | "vp9"
    height: int
    fps: Fraction

    def width_for(self, src: Source) -> int:
        w = round(src.width * self.height / src.height)
        return w - (w % 2)

    def label(self) -> str:
        fps = f"{float(self.fps):g}"
        return f"{self.codec}-{self.height}p{fps}"


@dataclass
class EncodeResult:
    cand: Candidate
    path: Path
    kbps: float
    size: int
    seconds: float
    attempts: list[tuple[float, int]] = field(default_factory=list)
    floor_fix: bool = False  # the screening flags could not reach the limit; the floor-lowering flags did


def norm_filter(src: Source, height: int | None = None, fps: Fraction | None = None) -> str:
    """Filter chain to limited-range bt709 yuv420p at the given size/fps.

    Used for encodes and (with source size/fps) for building the metric
    reference, so the colour conversion itself is never scored as loss.
    """
    parts = []
    if fps is not None and fps != src.fps:
        parts.append(f"fps=fps={fps}")
    h = height or src.height
    in_range = "pc" if src.full_range else "tv"
    parts.append(
        f"scale=-2:{h}:flags=lanczos:in_range={in_range}:out_range=tv"
        f":in_color_matrix={src.in_matrix}:out_color_matrix=bt709"
    )
    parts.append("format=yuv420p")
    return ",".join(parts)


COLOR_TAGS = [
    "-colorspace", "bt709", "-color_primaries", "bt709",
    "-color_trc", "bt709", "-color_range", "tv",
]  # fmt: skip


def codec_args(
    cand: Candidate,
    src: Source,
    kbps: float,
    final: bool,
    threads: int,
    vp9_overrides: Mapping[str, str] | None = None,
) -> list[str]:
    # Long GOP: these clips are mostly static backgrounds; one keyframe per
    # ~20 s measured +0.8 dB XPSNR over 20 s at 4 MiB (swarm-4-hand, 2026-11-04).
    gop = max(1, min(round(float(cand.fps) * 20), math.ceil(src.duration * float(cand.fps))))
    b = f"{kbps:.0f}k"
    if cand.codec == "x264":
        preset = "veryslow" if final else "slow"
        x264p = (
            "ref=16:bframes=8:aq-mode=3:rc-lookahead=120:me=umh:merange=32" if final else "aq-mode=3:rc-lookahead=60"
        )
        return [
            "-c:v", "libx264", "-preset", preset, "-b:v", b, "-g", str(gop),
            "-x264-params", x264p, "-threads", str(threads),
        ]  # fmt: skip
    if cand.codec == "vp9":
        # cpu-used 1/2/3/4 measured XPSNR 32.95/32.90/33.27/33.20 at 4 MiB on
        # swarm-4-full-small (2026-11-04): slower libvpx presets were not better,
        # so finals reuse the screening preset and only tighten the size band.
        w = cand.width_for(src)
        tiles = max(0, min(2, int(math.log2(max(1, w // 256)))))
        flags = {
            "-b:v": b, "-deadline": "good", "-cpu-used": "3", "-row-mt": "1",
            "-tile-columns": str(tiles), "-threads": str(threads), "-g": str(gop),
            "-auto-alt-ref": "6", "-lag-in-frames": "25", "-enable-tpl": "1", "-aq-mode": "0",
        }  # fmt: skip
        flags.update(vp9_overrides or {})
        return ["-c:v", "libvpx-vp9", *(a for kv in flags.items() for a in kv)]
    raise ValueError(cand.codec)


def audio_args(cand: Candidate, src: Source, audio: bool) -> list[str]:
    if not (audio and src.has_audio):
        return ["-an"]
    # Upload targets want stereo at most; libopus also rejects some 5.1 layouts.
    ch = ["-ac", str(min(2, src.audio_channels or 2))]
    return ["-c:a", ff.AUDIO_ENCODERS[cand.codec], "-b:a", f"{AUDIO_KBPS[cand.codec]}k", *ch]


def budget_kbps(src: Source, cand: Candidate, limit: int, audio: bool, margin: float = 0.985) -> float:
    bits = limit * 8 * margin / (1 + OVERHEAD)
    if audio and src.has_audio:
        bits -= AUDIO_KBPS[cand.codec] * 1000 * src.duration
    return bits / src.duration / 1000


def _video_cmd(
    src: Source,
    cand: Candidate,
    kbps: float,
    final: bool,
    threads: int,
    vp9_overrides: Mapping[str, str] | None = None,
) -> list[str]:
    """ffmpeg command up to (not including) the pass flags, audio and output."""
    # -color_* output flags alone left primaries/transfer "unknown" in the
    # x264 mp4 (independent check, 2026-11-04); set them on the frames.
    vf = norm_filter(src, cand.height, cand.fps) + (
        ",setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
    )
    vargs = codec_args(cand, src, kbps, final, threads, vp9_overrides)
    return [*ff.FFMPEG, "-v", "error", "-y", *src.in_args, "-i", str(src.path), "-vf", vf, *vargs, *COLOR_TAGS]


def first_pass_key(
    src: Source, cand: Candidate, final: bool, threads: int, vp9_overrides: Mapping[str, str] | None = None
) -> str:
    """Identity of a first-pass stats file: everything the analysis sees except the target bitrate.

    libvpx's first pass analyses the frames at the encoded size/fps with fixed
    settings, so its stats serve any later target bitrate (tests/test_encode.py
    checks the second-pass output is byte-identical). Source identity, filters,
    every codec flag and the thread count are in the key; only -b:v is not.
    """
    cmd = _video_cmd(src, cand, 1.0, final, threads, vp9_overrides)
    i = cmd.index("-b:v")
    del cmd[i : i + 2]
    ident = [str(src.path), f"{src.start:.3f}", f"{src.duration:.3f}", *cmd]
    return hashlib.sha1("\0".join(ident).encode()).hexdigest()[:16]


# How far (as a ratio either way) a later target bitrate may stray from the one
# pass 1 ran at before the stats are rebuilt. libvpx's first pass ignores the
# target bitrate (second-pass output was byte-identical from stats made at 2x
# the bitrate); x264's pass 1 runs ABR at the target, and 2x apart cost 0.2-0.3 dB.
REUSE_TOLERANCE: dict[str, float | None] = {"vp9": None, "x264": 0.15}


class FirstPassCache:
    """First-pass stats files for one run, shared by size-correction retries and later stages.

    One pass 1 per distinct key (and, where the codec's stats depend on it, per
    bitrate band); threads asking for the same key at once wait for the first.
    `runs` counts the pass-1 encodes executed, `hits` the reuses.
    """

    def __init__(self, root: Path):
        self.root = root
        self.runs = 0
        self.hits = 0
        self._lock = threading.Lock()
        self._done: dict[str, tuple[Path, float]] = {}
        self._gates: dict[str, threading.Lock] = {}

    def _valid(self, key: str, kbps: float, tol: float | None) -> Path | None:
        got = self._done.get(key)
        if got and (tol is None or abs(math.log(kbps / got[1])) <= math.log1p(tol)):
            return got[0]
        return None

    def get(self, key: str, kbps: float, tol: float | None, build: Callable[[Path], None]) -> Path:
        """Stats prefix for `key`, running build(prefix) when none is valid for `kbps`."""
        with self._lock:
            hit = self._valid(key, kbps, tol)
            if hit:
                self.hits += 1
                return hit
            gate = self._gates.setdefault(key, threading.Lock())
        with gate:
            with self._lock:
                hit = self._valid(key, kbps, tol)
                if hit:
                    self.hits += 1
                    return hit
                n = self.runs
                self.runs += 1
            self.root.mkdir(parents=True, exist_ok=True)
            prefix = self.root / f"{key}-{n}"
            build(prefix)
            with self._lock:
                self._done[key] = (prefix, kbps)
            return prefix

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self._done.clear()


def run_first_pass(
    src: Source,
    cand: Candidate,
    kbps: float,
    prefix: Path,
    *,
    final: bool,
    threads: int,
    vp9_overrides: Mapping[str, str] | None = None,
) -> None:
    base = _video_cmd(src, cand, kbps, final, threads, vp9_overrides)
    ff.run([*base, "-an", "-pass", "1", "-passlogfile", str(prefix), "-f", "null", "-"])


def encode_once(
    src: Source,
    cand: Candidate,
    kbps: float,
    out: Path,
    *,
    final: bool,
    threads: int,
    audio: bool,
    cache: FirstPassCache | None = None,
    vp9_overrides: Mapping[str, str] | None = None,
) -> int:
    """One 2-pass encode at `kbps`. With a cache, pass 1 runs once per first_pass_key and is reused."""
    if cache is None:
        prefix = out.with_suffix(".2pass")
        run_first_pass(src, cand, kbps, prefix, final=final, threads=threads, vp9_overrides=vp9_overrides)
    else:
        key = first_pass_key(src, cand, final, threads, vp9_overrides)
        prefix = cache.get(
            key,
            kbps,
            REUSE_TOLERANCE[cand.codec],
            lambda p: run_first_pass(src, cand, kbps, p, final=final, threads=threads, vp9_overrides=vp9_overrides),
        )
    base = _video_cmd(src, cand, kbps, final, threads, vp9_overrides)
    mux = ["-movflags", "+faststart"] if cand.codec == "x264" else []
    ff.run([*base, *audio_args(cand, src, audio), "-pass", "2", "-passlogfile", str(prefix), *mux, str(out)])
    if cache is None:
        for p in out.parent.glob(prefix.name + "*"):
            p.unlink()
    return out.stat().st_size


# libvpx 2-pass has a bitrate floor on noisy footage (see scripts/measure_floor.py and NOTES-sweep.md):
# when a candidate cannot get under the limit with the screening flags, one more ladder runs with these.
FLOOR_FIX: dict[str, str] = {"-cpu-used": "2"}


def stalled(prev: tuple[float, int], cur: tuple[float, int], limit: int) -> bool:
    """True when two over-limit tries show the size not following the requested bitrate (a floor).

    Size scales about linearly with the requested bitrate, so a cut of x% in
    kbps should cut the size by most of x%. Less than half of it, with both
    tries over the limit, is the floor, and a third try at the same flags
    would only repeat it.
    """
    (k0, s0), (k1, s1) = prev, cur
    if s0 <= limit or s1 <= limit or k1 >= k0:
        return False
    return (1 - s1 / s0) < 0.5 * (1 - k1 / k0)


def encode_to_size(
    src: Source,
    cand: Candidate,
    limit: int,
    out_dir: Path,
    *,
    final: bool,
    threads: int,
    audio: bool,
    low: float,
    max_tries: int,
    cache: FirstPassCache | None = None,
    floor_fix: Mapping[str, str] | None = FLOOR_FIX,
) -> EncodeResult:
    """2-pass encode, then rescale bitrate until size lands in [low*limit, limit].

    libvpx 2-pass VBR missed its target by -12%/+4% on swarm-4-hand, so a
    single bitrate-arithmetic pass is not enough. A VP9 candidate that stays
    over the limit (or whose size stops following the bitrate) gets one more
    ladder of 2 tries with `floor_fix` flags before it is given up on.
    """
    kbps0 = budget_kbps(src, cand, limit, audio)
    tag = "final" if final else "screen"
    out = out_dir / f"{cand.label()}.{tag}.{EXT[cand.codec]}"
    attempts: list[tuple[float, int]] = []
    best: tuple[float, int, Path] | None = None
    n_try = 0
    t0 = time.monotonic()

    def ladder(overrides: Mapping[str, str] | None, tries: int, detect_floor: bool) -> bool:
        """Run up to `tries` encodes from the budget bitrate; True if one landed in the band."""
        nonlocal best, n_try
        kbps = kbps0
        mine: list[tuple[float, int]] = []
        for _ in range(tries):
            path = out.with_name(f"{out.stem}.try{n_try}{out.suffix}")
            n_try += 1
            size = encode_once(
                src, cand, kbps, path, final=final, threads=threads, audio=audio, cache=cache, vp9_overrides=overrides
            )
            mine.append((kbps, size))
            attempts.append((kbps, size))
            if size <= limit and (best is None or size > best[1]):
                best = (kbps, size, path)
            if low * limit <= size <= limit:
                return True
            if detect_floor and len(mine) >= 2 and stalled(mine[-2], mine[-1], limit):
                return False
            # Video bits scale ~linearly with requested bitrate; aim at the middle.
            kbps *= limit * (1 + low) / 2 / size
        return False

    fixed = False
    can_fix = cand.codec == "vp9" and bool(floor_fix)
    ladder(None, max_tries, detect_floor=can_fix)
    if best is None and can_fix:
        fixed = True
        ladder(floor_fix, 2, detect_floor=False)
    t = time.monotonic() - t0
    if best is None:
        raise RuntimeError(f"{cand.label()}: never got under {limit} bytes: {attempts}")
    kbps_b, size_b, path_b = best
    for p in out_dir.glob(f"{out.stem}.try*{out.suffix}"):
        if p != path_b:
            p.unlink()
    path_b.rename(out)
    # `fixed` means the fix ladder ran, and it ran only because the base ladder had no result under the limit.
    return EncodeResult(cand, out, kbps_b, size_b, t, attempts, floor_fix=fixed)
