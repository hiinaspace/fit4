"""Fast pre-flight probe: argument rewriting and VP9 ordering (pure), plus one short real run."""

import math
import subprocess
from fractions import Fraction

import pytest

from fit4 import cli, fastprobe, ff
from fit4.encode import Candidate
from fit4.layout import plan_layout
from fit4.preflight import CandCurve, Point, Probe
from fit4.probe import probe

LIMIT = 4 * 1024 * 1024


def cand(codec, h, fps=30):
    return Candidate(codec, h, Fraction(fps))


def test_fast_args_x264_uses_the_probe_preset_and_drops_lookahead(tmp_path):
    from fit4.probe import Source

    reel = Source(path=tmp_path / "r.mkv", width=1920, height=1080, fps=Fraction(60), duration=6.0, nb_frames=360,
                  full_range=False, colorspace="bt709", has_audio=False, size=1)  # fmt: skip
    a = fastprobe.fast_args(cand("x264", 720), reel, 30, 4)
    assert a[a.index("-preset") + 1] == fastprobe.X264_PRESET
    assert a[a.index("-crf") + 1] == "30" and "lookahead" not in a[a.index("-x264-params") + 1]
    v = fastprobe.fast_args(cand("vp9", 720), reel, 52, 4)
    assert v[v.index("-cpu-used") + 1] == "3"  # VP9 probe points use the screening settings
    assert v[v.index("-b:v") + 1] == "0" and v[v.index("-crf") + 1] == "52"


def test_pick_crfs_bracket_the_budget_and_stay_in_range():
    # Reference x264 CRF 32 at exactly the budget: points straddle CRF 32.
    assert fastprobe.pick_crfs("x264", 1000, 1000) == [28, 36]
    # Twice the bitrate wanted (budget 500): the budget CRF is ~6.5 higher.
    lo, hi = fastprobe.pick_crfs("x264", 1000, 500)
    assert lo < 38.5 < hi
    # Far below the reference: clamped, deduplicated, never the reference itself.
    assert fastprobe.pick_crfs("x264", 1000, 1e9) == [10]
    assert fastprobe.pick_crfs("x264", 1000, 1e-9) == [51]
    # VP9: the CRF-63 point is the reference; the budget is above it, so the picks sit below 63.
    got = fastprobe.pick_crfs("vp9", 300, 1200)
    assert got == sorted(got) and max(got) < 63 and len(got) == 2
    assert got[0] < got[1] < 63


def floor_curve(codec, h, fps, kbps_at_63):
    # one CRF-63 point: inter-frame bytes chosen so the whole-window bitrate is ~kbps_at_63
    inter = kbps_at_63 * 1000 / 8 / fps
    return CandCurve(codec, h, float(fps), [Point(63, inter * 5, inter, 25.0, 0.8)])


def test_vp9_order_ranks_by_x264_and_explains_the_floor():
    p = Probe(start=0.0, duration=30.0, src_fps=60.0, timeline=[1.0] * 30, reel_complexity=1.0, slices=[(0.0, 2.0)],
              curves=[floor_curve("vp9", 720, 30, 300), floor_curve("vp9", 540, 30, 150), floor_curve("vp9", 1080, 30, 900)])  # fmt: skip
    vp9 = [cand("vp9", 720), cand("vp9", 540), cand("vp9", 1080)]
    # budget is ~1.1 Mbps: 1080p's floor (900 x 1.6) does not fit.
    xq = {(720, 30.0): 33.0, (540, 30.0): 34.0, (1080, 30.0): 40.0}
    feasible, out = fastprobe.vp9_order(p, vp9, xq, LIMIT, 0.0)
    assert [c.height for c in feasible] == [540, 720]
    assert list(out) == [cand("vp9", 1080)] and "1.6x" in out[cand("vp9", 1080)]


def _ffmpeg_ok() -> str | None:
    try:
        ff.check(["x264", "vp9"], audio=False)
    except (ff.MissingFFmpeg, subprocess.CalledProcessError) as e:
        return str(e)
    return None


why = _ffmpeg_ok()


@pytest.mark.ffmpeg
@pytest.mark.skipif(why is not None, reason=f"needs a full ffmpeg: {why}")
def test_probe_runs_end_to_end_on_a_short_clip(tmp_path):
    clip = tmp_path / "c.mp4"
    ff.run([
        *ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=12",
        "-vf", "noise=alls=8:allf=t", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(clip),
    ])  # fmt: skip
    src = probe(clip)
    cands = [cand(c, h, 24) for c in ("x264", "vp9") for h in (180, 144)]
    fp = fastprobe.run_fast_probe(src, cands, 150_000, tmp_path / "w", plan_layout(4, ["x264", "vp9"]), vp9_top=1)
    labels = {c.label() for c in fp.probe.curves}
    assert {"x264-180p24", "x264-144p24"} <= labels
    # every VP9 geometry is either fully probed or reported as skipped, never silently dropped
    vp9_seen = {c.label() for c in fp.probe.curves if c.codec == "vp9"} | {c.label() for c in fp.unprobed}
    assert vp9_seen == {"vp9-180p24", "vp9-144p24"}
    q = Probe.from_json(fp.probe.to_json())
    assert all(2 <= len(c.points) <= 3 for c in q.curves if c.codec == "x264")  # reference + bracketing points
    assert all(c.points[0].crf <= c.points[-1].crf for c in q.curves)
    assert not list((tmp_path / "w").glob("reel.*"))


def test_probe_flags_exist():
    a = cli.build_parser().parse_args(["x.mp4", "--full-grid", "--prune-margin", "2", "--prune-keep", "6"])
    assert a.full_grid and a.prune_margin == 2.0 and a.prune_keep == 6 and not a.probe_only


@pytest.mark.ffmpeg
@pytest.mark.skipif(why is not None, reason=f"needs a full ffmpeg: {why}")
def test_reel_scores_lossless_against_itself(tmp_path):
    """Regression: a 60 fps mkv reel (1 ms stamps) paired neighbouring frames, so self-score read ~35 dB."""
    from fit4.metrics import score
    from fit4.preflight import build_reel

    clip = tmp_path / "c.mp4"
    ff.run([
        *ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=60:d=6",
        "-vf", "noise=alls=8:allf=t", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(clip),
    ])  # fmt: skip
    src = probe(clip)
    reel = build_reel(src, [(0.0, 2.0), (3.0, 2.0)], tmp_path)
    s = score(reel, reel.path, tmp_path / "x.txt")
    assert s.xpsnr_yuv > 60 or math.isinf(s.xpsnr_yuv)
