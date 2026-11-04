"""Upload targets. Limits are community-reported (2026-11), not official."""

from __future__ import annotations

from dataclasses import dataclass

MIB = 1024 * 1024


@dataclass(frozen=True)
class Board:
    name: str
    limit_bytes: int
    max_dim: int
    max_duration: float | None  # seconds; None = unknown
    codecs: tuple[str, ...]  # encoder keys from encode.CODECS


BOARDS = {
    # 4chan: mp4 must be h264+aac, webm vp8/vp9 + vorbis/opus; no AV1.
    "4chan": Board("4chan", 4 * MIB, 2048, 120.0, ("x264", "vp9")),
    "wsg": Board("wsg", 6 * MIB, 2048, 400.0, ("x264", "vp9")),
}
