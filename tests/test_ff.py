import re
from pathlib import Path

import pytest

from fit4 import ff

ENCODERS = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D libwebp              libwebp WebP image (codec webp)
 A....D aac                  AAC (Advanced Audio Coding)
"""
FILTERS = """Filters:
  T.. = Timeline support
  A = Audio input/output
 TS ssim              VV->V      Calculate the SSIM between two video streams.
 .. fps               V->V       Force constant framerate.
 TSC scale            V->V       Scale the input video size.
 .. format            V->V       Convert the input video to one of the specified pixel formats.
 .. setparams         V->V       Force field, or color property for the output video frame.
 .. select            V->V       Select video frames to pass in output.
 .. split             V->N       Pass on the input to N video outputs.
"""


def test_parse_listing_skips_legend():
    enc = ff.parse_listing(ENCODERS)
    assert enc == {"libx264", "libwebp", "aac"}
    assert "=" not in enc and "------" not in enc


def test_missing_features_names_each_gap():
    enc, filt = ff.parse_listing(ENCODERS), ff.parse_listing(FILTERS)
    assert ff.missing_features(enc, filt, ["x264"], audio=True) == ["xpsnr filter"]
    gaps = ff.missing_features(enc, filt, ["x264", "vp9"], audio=True)
    assert gaps == ["libvpx-vp9 encoder", "libopus encoder", "xpsnr filter"]


def test_run_refuses_ffmpeg_without_nostdin():
    with pytest.raises(AssertionError):
        ff.run(["ffmpeg", "-version"])


def test_only_ff_module_spawns_processes():
    # Every ffmpeg/ffprobe call must go through ff.run (stdin=DEVNULL, -nostdin).
    src = Path(ff.__file__).parent
    offenders = [
        p.name
        for p in src.glob("*.py")
        if p.name != "ff.py" and re.search(r"subprocess\.(run|Popen|call|check_)", p.read_text())
    ]
    assert offenders == []
    bare = [
        p.name for p in src.glob("*.py") if p.name != "ff.py" and re.search(r"""["']ffmpeg["']\s*,""", p.read_text())
    ]
    assert bare == [], "build ffmpeg commands from ff.FFMPEG"
