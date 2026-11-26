"""VP9 floor fallback in encode_to_size, with a fake encoder (no ffmpeg)."""

from fractions import Fraction

import pytest

from fit4 import encode
from fit4.encode import Candidate, encode_to_size, stalled
from fit4.probe import Source

LIMIT = 100_000


def src():
    return Source(path=__import__("pathlib").Path("x.mp4"), width=1280, height=720, fps=Fraction(30), duration=10.0,
                  nb_frames=300, full_range=False, colorspace="bt709", has_audio=False, size=1)  # fmt: skip


def fake_encoder(monkeypatch, size_for):
    calls = []

    def fake(_src, cand, kbps, out, *, final, threads, audio, cache=None, vp9_overrides=None):
        size = size_for(kbps, vp9_overrides)
        out.write_bytes(b"x")
        calls.append((round(kbps), size, dict(vp9_overrides or {})))
        return size

    monkeypatch.setattr(encode, "encode_once", fake)
    return calls


def test_stalled_detects_sizes_that_ignore_the_bitrate():
    assert stalled((800, 130_000), (400, 128_000), LIMIT)  # halved bitrate, 1.5% smaller: a floor
    assert not stalled((800, 130_000), (400, 70_000), LIMIT)  # followed the bitrate
    assert not stalled((800, 90_000), (400, 88_000), LIMIT)  # already under the limit: nothing to fix
    assert not stalled((400, 130_000), (800, 140_000), LIMIT)  # bitrate went up: not evidence


def test_floor_triggers_the_fix_ladder_after_a_stall(tmp_path, monkeypatch):
    # Base flags: size pinned at 130 kB whatever the target. With the fix: tracks the target.
    calls = fake_encoder(monkeypatch, lambda k, ov: 130_000 if not ov else int(k * 1250 * 0.97))
    r = encode_to_size(src(), Candidate("vp9", 720, Fraction(30)), LIMIT, tmp_path, final=False, threads=4,
                       audio=False, low=0.95, max_tries=3)  # fmt: skip
    base = [c for c in calls if not c[2]]
    fixed = [c for c in calls if c[2]]
    assert len(base) == 2  # stall noticed after two tries, no third wasted
    assert fixed and fixed[0][2] == encode.FLOOR_FIX
    assert r.floor_fix and r.size <= LIMIT and r.path.exists()


def test_no_fix_when_the_base_flags_get_under(tmp_path, monkeypatch):
    calls = fake_encoder(monkeypatch, lambda k, ov: int(k * 1250 * 0.97))
    r = encode_to_size(src(), Candidate("vp9", 720, Fraction(30)), LIMIT, tmp_path, final=False, threads=4,
                       audio=False, low=0.95, max_tries=3)  # fmt: skip
    assert not r.floor_fix and all(not c[2] for c in calls)


def test_x264_never_gets_the_vp9_fix_and_still_fails_cleanly(tmp_path, monkeypatch):
    calls = fake_encoder(monkeypatch, lambda k, ov: 130_000)
    with pytest.raises(RuntimeError, match="never got under"):
        encode_to_size(src(), Candidate("x264", 720, Fraction(30)), LIMIT, tmp_path, final=False, threads=4,
                       audio=False, low=0.95, max_tries=3)  # fmt: skip
    assert len(calls) == 3 and all(not c[2] for c in calls)


def test_fix_ladder_failure_is_reported_with_every_attempt(tmp_path, monkeypatch):
    fake_encoder(monkeypatch, lambda k, ov: 130_000)
    with pytest.raises(RuntimeError, match="never got under") as e:
        encode_to_size(src(), Candidate("vp9", 720, Fraction(30)), LIMIT, tmp_path, final=False, threads=4,
                       audio=False, low=0.95, max_tries=3)  # fmt: skip
    assert str(e.value).count("130000") == 4  # 2 base tries (stall) + 2 fix tries


def test_fix_can_be_disabled(tmp_path, monkeypatch):
    calls = fake_encoder(monkeypatch, lambda k, ov: 130_000)
    with pytest.raises(RuntimeError):
        encode_to_size(src(), Candidate("vp9", 720, Fraction(30)), LIMIT, tmp_path, final=False, threads=4,
                       audio=False, low=0.95, max_tries=3, floor_fix=None)  # fmt: skip
    assert len(calls) == 3
