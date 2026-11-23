from fractions import Fraction
from pathlib import Path

import pytest

from fit4.probe import BadInput, from_ffprobe

P = Path("clip.mp4")
VIDEO = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "60/1",
         "r_frame_rate": "60/1", "pix_fmt": "yuv420p", "nb_frames": "2940"}  # fmt: skip
FMT = {"duration": "49.0", "size": "25749621", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}


def test_plain_video():
    s = from_ffprobe(P, {"streams": [VIDEO], "format": FMT})
    assert (s.width, s.height, s.fps, s.has_audio, s.video_codec) == (1920, 1080, Fraction(60), False, "h264")


def test_audio_and_cover_art():
    cover = {"codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 600, "avg_frame_rate": "0/0",
             "disposition": {"attached_pic": 1}}  # fmt: skip
    audio = {"codec_type": "audio", "codec_name": "aac", "channels": 6}
    s = from_ffprobe(P, {"streams": [cover, VIDEO, audio], "format": FMT})
    assert s.width == 1920 and s.has_audio and s.audio_codec == "aac" and s.audio_channels == 6


def test_audio_only_is_rejected():
    cover = {"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}}
    with pytest.raises(BadInput, match="no video stream"):
        from_ffprobe(Path("song.m4a"), {"streams": [cover, {"codec_type": "audio"}], "format": FMT})


def test_still_image_is_rejected():
    png = {**VIDEO, "codec_name": "png", "avg_frame_rate": "0/0", "r_frame_rate": "25/1"}
    del png["nb_frames"]
    with pytest.raises(BadInput, match="still image"):
        from_ffprobe(Path("a.png"), {"streams": [png], "format": {"format_name": "png_pipe", "size": "1000"}})


def test_duration_falls_back_to_stream():
    s = from_ffprobe(
        P, {"streams": [{**VIDEO, "duration": "12.5"}], "format": {"size": "1", "format_name": "matroska,webm"}}
    )
    assert s.duration == 12.5
