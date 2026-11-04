"""Build and run size-targeted ffmpeg encodes."""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

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


def codec_args(cand: Candidate, src: Source, kbps: float, final: bool, threads: int) -> list[str]:
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
        return [
            "-c:v", "libvpx-vp9", "-b:v", b, "-deadline", "good",
            "-cpu-used", "3", "-row-mt", "1",
            "-tile-columns", str(tiles), "-threads", str(threads),
            "-g", str(gop), "-auto-alt-ref", "6", "-lag-in-frames", "25",
            "-enable-tpl", "1", "-aq-mode", "0",
        ]  # fmt: skip
    raise ValueError(cand.codec)


def audio_args(cand: Candidate, src: Source, audio: bool) -> list[str]:
    if not (audio and src.has_audio):
        return ["-an"]
    if cand.codec == "x264":
        return ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS['x264']}k"]
    return ["-c:a", "libopus", "-b:a", f"{AUDIO_KBPS['vp9']}k"]


def budget_kbps(src: Source, cand: Candidate, limit: int, audio: bool, margin: float = 0.985) -> float:
    bits = limit * 8 * margin / (1 + OVERHEAD)
    if audio and src.has_audio:
        bits -= AUDIO_KBPS[cand.codec] * 1000 * src.duration
    return bits / src.duration / 1000


def encode_once(
    src: Source,
    cand: Candidate,
    kbps: float,
    out: Path,
    *,
    final: bool,
    threads: int,
    audio: bool,
) -> int:
    # -color_* output flags alone left primaries/transfer "unknown" in the
    # x264 mp4 (independent check, 2026-11-04); set them on the frames.
    vf = norm_filter(src, cand.height, cand.fps) + (
        ",setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
    )
    vargs = codec_args(cand, src, kbps, final, threads)
    log = out.with_suffix(".2pass")
    base = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src.path),
        "-vf",
        vf,
        *vargs,
        *COLOR_TAGS,
    ]
    subprocess.run(
        [
            *base,
            "-an",
            "-pass",
            "1",
            "-passlogfile",
            str(log),
            "-f",
            "null",
            "/dev/null",
        ],
        check=True,
    )
    mux = ["-movflags", "+faststart"] if cand.codec == "x264" else []
    subprocess.run(
        [
            *base,
            *audio_args(cand, src, audio),
            "-pass",
            "2",
            "-passlogfile",
            str(log),
            *mux,
            str(out),
        ],
        check=True,
    )
    for p in out.parent.glob(log.name + "*"):
        p.unlink()
    return out.stat().st_size


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
) -> EncodeResult:
    """2-pass encode, then rescale bitrate until size lands in [low*limit, limit].

    libvpx 2-pass VBR missed its target by -12%/+4% on swarm-4-hand, so a
    single bitrate-arithmetic pass is not enough.
    """
    kbps = budget_kbps(src, cand, limit, audio)
    tag = "final" if final else "screen"
    out = out_dir / f"{cand.label()}.{tag}.{EXT[cand.codec]}"
    attempts: list[tuple[float, int]] = []
    best: tuple[float, int, Path] | None = None
    t0 = time.monotonic()
    for i in range(max_tries):
        path = out.with_name(f"{out.stem}.try{i}{out.suffix}")
        size = encode_once(src, cand, kbps, path, final=final, threads=threads, audio=audio)
        attempts.append((kbps, size))
        if size <= limit and (best is None or size > best[1]):
            best = (kbps, size, path)
        if low * limit <= size <= limit:
            break
        # Video bits scale ~linearly with requested bitrate; aim at the middle.
        target = limit * (1 + low) / 2
        kbps *= target / size
    t = time.monotonic() - t0
    if best is None:
        raise RuntimeError(f"{cand.label()}: never got under {limit} bytes: {attempts}")
    kbps_b, size_b, path_b = best
    for p in out_dir.glob(f"{out.stem}.try*{out.suffix}"):
        if p != path_b:
            p.unlink()
    path_b.rename(out)
    return EncodeResult(cand, out, kbps_b, size_b, t, attempts)
