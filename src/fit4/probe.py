"""ffprobe wrapper: the handful of source facts the search needs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import ff


class BadInput(ValueError):
    """The input can't be used: missing, not media, no video, no duration."""


@dataclass(frozen=True)
class Source:
    path: Path
    width: int
    height: int
    fps: Fraction
    duration: float
    nb_frames: int
    full_range: bool
    colorspace: str  # ffprobe color_space, e.g. "bt709", "bt470bg", "unknown"
    has_audio: bool
    size: int
    video_codec: str = ""  # ffprobe codec_name, e.g. "h264"
    audio_codec: str = ""  # "" when there is no audio
    audio_channels: int = 0

    @property
    def in_matrix(self) -> str:
        """Matrix name for ffmpeg scale's in_color_matrix."""
        if self.colorspace in ("bt470bg", "smpte170m"):
            return "bt601"
        if self.colorspace == "bt709":
            return "bt709"
        # Untagged: the common decoder assumption is 601 for SD, 709 for HD.
        return "bt709" if self.height >= 720 else "bt601"


def _rate(s: str | None) -> Fraction:
    try:
        return Fraction(s or "0")
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def from_ffprobe(path: Path, info: dict) -> Source:
    """Build a Source from `ffprobe -show_streams -show_format -of json` output."""
    streams = info.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        kinds = sorted({s.get("codec_type", "?") for s in streams}) or ["none"]
        raise BadInput(f"{path}: no video stream (streams: {', '.join(kinds)})")
    video = videos[0]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = info.get("format", {})

    fps = _rate(video.get("avg_frame_rate"))
    if fps <= 0:
        fps = _rate(video.get("r_frame_rate"))
    if fps <= 0:
        raise BadInput(f"{path}: can't read a frame rate from the video stream")
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    nb = int(video.get("nb_frames") or round(duration * fps))
    if duration <= 0 or nb <= 1 or fmt.get("format_name", "").endswith("_pipe"):
        raise BadInput(f"{path}: looks like a still image or has no duration; fit4 needs a video")

    full = video.get("color_range") == "pc" or video.get("pix_fmt", "").startswith("yuvj")
    return Source(
        path=path,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        duration=duration,
        nb_frames=nb,
        full_range=full,
        colorspace=video.get("color_space", "unknown"),
        has_audio=bool(audios),
        size=int(fmt.get("size") or path.stat().st_size),
        video_codec=video.get("codec_name", ""),
        audio_codec=audios[0].get("codec_name", "") if audios else "",
        audio_channels=int(audios[0].get("channels") or 2) if audios else 0,
    )


def probe(path: Path) -> Source:
    if not path.is_file():
        raise BadInput(f"{path}: no such file")
    try:
        out = ff.run(
            [*ff.FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture=True,
        )
    except subprocess.CalledProcessError as e:
        lines = (e.stderr or "").strip().splitlines()
        why = lines[-1] if lines else f"exit {e.returncode}"
        raise BadInput(f"{path}: ffprobe can't read it ({why})") from None
    return from_ffprobe(path, json.loads(out.stdout))
