"""ffprobe wrapper: the handful of source facts the search needs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


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

    @property
    def in_matrix(self) -> str:
        """Matrix name for ffmpeg scale's in_color_matrix."""
        if self.colorspace in ("bt470bg", "smpte170m"):
            return "bt601"
        if self.colorspace == "bt709":
            return "bt709"
        # Untagged: the common decoder assumption is 601 for SD, 709 for HD.
        return "bt709" if self.height >= 720 else "bt601"


def probe(path: Path) -> Source:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    info = json.loads(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    fps = Fraction(video.get("avg_frame_rate") or video["r_frame_rate"])
    if fps == 0:
        fps = Fraction(video["r_frame_rate"])
    duration = float(info["format"]["duration"])
    nb = int(video.get("nb_frames") or round(duration * fps))
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
        has_audio=has_audio,
        size=int(info["format"]["size"]),
    )
