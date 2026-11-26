"""Full-reference quality scores at the source's display size and framerate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import ff
from .encode import norm_filter
from .probe import Source


@dataclass
class Score:
    xpsnr_y: float
    xpsnr_yuv: float  # (4y + u + v) / 6
    ssim_y: float
    per_frame_y: list[float]  # XPSNR y per source-rate frame

    @property
    def primary(self) -> float:
        return self.xpsnr_yuv


def distorted_filter(src: Source) -> str:
    # Upscale to display size (bicubic, roughly what a browser does) and
    # duplicate frames back to the source rate so low-fps candidates pay for
    # their judder instead of being scored on fewer frames.
    return f"scale={src.width}:{src.height}:flags=bicubic,fps=fps={src.fps},format=yuv420p,setpts=PTS-STARTPTS"


def reference_filter(src: Source) -> str:
    return norm_filter(src) + ",setpts=PTS-STARTPTS"


def score(src: Source, dist: Path, stats: Path) -> Score:
    lavfi = (
        f"[0:v]{distorted_filter(src)},split[d1][d2];"
        f"[1:v]{reference_filter(src)},split[r1][r2];"
        f"[d1][r1]ssim;"
        f"[d2][r2]xpsnr=shortest=1:stats_file={stats}"
    )
    out = ff.run(
        [
            *ff.FFMPEG,
            "-nostats",
            "-i",
            str(dist),
            *src.in_args,
            "-i",
            str(src.path),
            "-lavfi",
            lavfi,
            "-f",
            "null",
            "-",
        ],
        capture=True,
    ).stderr
    m = re.findall(r"XPSNR\s+y:\s*([\d.inf]+)\s+u:\s*([\d.inf]+)\s+v:\s*([\d.inf]+)", out)
    if not m:
        raise RuntimeError(f"no XPSNR in ffmpeg output for {dist}:\n{out[-2000:]}")
    y, u, v = (float(x) for x in m[-1])
    s = re.findall(r"SSIM Y:([\d.]+)", out)
    per = [float(x) for x in re.findall(r"XPSNR y:\s*([\d.inf]+)", stats.read_text())]
    return Score(xpsnr_y=y, xpsnr_yuv=(4 * y + u + v) / 6, ssim_y=float(s[-1]), per_frame_y=per)
