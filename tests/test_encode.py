"""First-pass reuse: cache bookkeeping (pure) and byte-identity of reused second passes (real ffmpeg)."""

import contextlib
import subprocess
import threading
from fractions import Fraction

import pytest

from fit4 import encode, ff
from fit4.encode import Candidate, FirstPassCache
from fit4.probe import probe


def test_cache_builds_once_per_key_and_counts_hits(tmp_path):
    built = []
    c = FirstPassCache(tmp_path / "fp")
    a = c.get("k", 500, None, lambda p: built.append(p))
    b = c.get("k", 5000, None, lambda p: built.append(p))  # no tolerance: any bitrate reuses
    assert a == b and len(built) == 1 and (c.runs, c.hits) == (1, 1)
    c.get("other", 500, None, lambda p: built.append(p))
    assert len(built) == 2


def test_cache_rebuilds_when_bitrate_leaves_tolerance(tmp_path):
    built = []
    c = FirstPassCache(tmp_path / "fp")
    a = c.get("k", 1000, 0.15, built.append)
    assert c.get("k", 1100, 0.15, built.append) == a
    assert c.get("k", 900, 0.15, built.append) == a
    b = c.get("k", 700, 0.15, built.append)
    assert b != a and len(built) == 2
    assert c.get("k", 720, 0.15, built.append) == b  # the new stats are what later retries compare with


def test_cache_concurrent_requests_share_one_build(tmp_path):
    c = FirstPassCache(tmp_path / "fp")
    gate, started, n = threading.Event(), threading.Event(), []

    def build(p):
        n.append(p)
        started.set()
        gate.wait(2)

    out = []
    ts = [threading.Thread(target=lambda: out.append(c.get("k", 1, None, build))) for _ in range(4)]
    for t in ts:
        t.start()
    started.wait(2)
    gate.set()
    for t in ts:
        t.join()
    assert len(n) == 1 and len(set(out)) == 1


def test_vp9_key_ignores_bitrate_but_not_geometry_or_threads(tmp_path):
    src = _fake_src(tmp_path)
    c = Candidate("vp9", 180, Fraction(24))
    k = encode.first_pass_key(src, c, False, 2)
    assert k == encode.first_pass_key(src, c, False, 2)
    assert k != encode.first_pass_key(src, Candidate("vp9", 144, Fraction(24)), False, 2)
    assert k != encode.first_pass_key(src, Candidate("vp9", 180, Fraction(12)), False, 2)
    assert k != encode.first_pass_key(src, c, False, 4)
    x = Candidate("x264", 180, Fraction(24))
    assert encode.first_pass_key(src, x, False, 2) != encode.first_pass_key(src, x, True, 2)  # preset differs


def _fake_src(tmp_path):
    from fit4.probe import Source

    return Source(
        path=tmp_path / "x.mp4", width=320, height=180, fps=Fraction(24), duration=2.0, nb_frames=48,
        full_range=False, colorspace="bt709", has_audio=False, size=1,
    )  # fmt: skip


# -- real ffmpeg -----------------------------------------------------------------


def _ffmpeg_ok() -> str | None:
    try:
        ff.check(["x264", "vp9"], audio=False)
    except (ff.MissingFFmpeg, subprocess.CalledProcessError) as e:
        return str(e)
    return None


why = _ffmpeg_ok()
real = pytest.mark.skipif(why is not None, reason=f"needs a full ffmpeg: {why}")


def payload_md5(path) -> str:
    """md5 of the packets: the WebM header carries a random segment UID, so whole-file hashes differ."""
    out = ff.run([*ff.FFMPEG, "-v", "error", "-i", str(path), "-c", "copy", "-f", "md5", "-"], capture=True)
    return out.stdout.strip()


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    # Moving, noisy content so the first pass has something to analyse.
    ff.run([
        *ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=3",
        "-vf", "noise=alls=12:allf=t", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
        "-pix_fmt", "yuv420p", str(path),
    ])  # fmt: skip
    return probe(path)


def second_pass(src, cand, kbps, prefix, out):
    base = encode._video_cmd(src, cand, kbps, False, 2)
    ff.run([*base, "-an", "-pass", "2", "-passlogfile", str(prefix), str(out)])
    return payload_md5(out)


@pytest.mark.ffmpeg
@real
@pytest.mark.parametrize("target", [250.0, 1000.0])
def test_vp9_second_pass_from_reused_stats_is_byte_identical(tmp_path, clip, target):
    cand = Candidate("vp9", 180, Fraction(24))
    # Stats made at 500 kbps, reused for a target that is half or double that...
    encode.run_first_pass(clip, cand, 500.0, tmp_path / "other", final=False, threads=2)
    reused = second_pass(clip, cand, target, tmp_path / "other", tmp_path / "reused.webm")
    # ...must equal a second pass that ran its own first pass at the target.
    encode.run_first_pass(clip, cand, target, tmp_path / "fresh", final=False, threads=2)
    fresh = second_pass(clip, cand, target, tmp_path / "fresh", tmp_path / "fresh.webm")
    assert reused == fresh


@pytest.mark.ffmpeg
@real
def test_x264_same_bitrate_stats_are_deterministic(tmp_path, clip):
    """x264 stats depend on the bitrate they were made at, so reuse is limited to a band (REUSE_TOLERANCE)."""
    cand = Candidate("x264", 180, Fraction(24))
    outs = []
    for i in range(2):
        encode.run_first_pass(clip, cand, 400.0, tmp_path / f"fp{i}", final=False, threads=2)
        base = encode._video_cmd(clip, cand, 400.0, False, 2)
        out = tmp_path / f"o{i}.mp4"
        ff.run([*base, "-an", "-pass", "2", "-passlogfile", str(tmp_path / f"fp{i}"), str(out)])
        outs.append(payload_md5(out))
    assert outs[0] == outs[1]


@pytest.mark.ffmpeg
@real
def test_retries_share_one_first_pass(tmp_path, clip):
    cache = FirstPassCache(tmp_path / "fp")
    # A 99%-100% band takes a retry or two to hit (a miss raises; the counts still hold).
    with contextlib.suppress(RuntimeError):
        encode.encode_to_size(
            clip, Candidate("vp9", 180, Fraction(24)), 25_000, tmp_path, final=False, threads=2, audio=False,
            low=0.99, max_tries=3, cache=cache,
        )  # fmt: skip
    assert cache.runs == 1 and cache.hits >= 1
