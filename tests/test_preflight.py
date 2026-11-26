"""Pure prediction math of the pre-flight probe (no ffmpeg)."""

import math

from fit4.preflight import CandCurve, Point, Probe, video_budget

LIMIT = 4 * 1024 * 1024


def curve(codec, height, fps, base_inter):
    # Each CRF step of 8 roughly thirds the bits and costs ~2.5 dB.
    pts = [
        Point(crf, base_inter * 10 / 3**i, base_inter / 3**i, 32 - 2.5 * i, 0.9)
        for i, crf in enumerate((22, 30, 38, 46))
    ]
    return CandCurve(codec, height, fps, pts)


def mk(timeline):
    return Probe(
        start=0.0, duration=len(timeline), src_fps=60.0, timeline=timeline, reel_complexity=1.0,
        slices=[(0.0, 3.0)], curves=[curve("x264", 720, 30.0, 4000.0), curve("vp9", 720, 30.0, 3000.0)],
    )  # fmt: skip


def test_prediction_rises_with_budget_and_with_shorter_windows():
    p = mk([1.0] * 60)
    c = p.curves[0]
    assert p.predict(c, 0, 60, 800) > p.predict(c, 0, 60, 400)
    assert p.best(0, 20, LIMIT)[0] > p.best(0, 60, LIMIT)[0]


def test_expensive_stretch_predicts_lower():
    p = mk([1.0] * 30 + [3.0] * 30)
    assert p.best(0, 30, LIMIT)[0] > p.best(30, 60, LIMIT)[0]


def test_vp9_floor_returns_none_below_its_ceiling_bitrate():
    p = mk([1.0] * 60)
    vp9 = p.curves[1]
    floor = min(p.kbps_at(vp9, pt, 0, 60) for pt in vp9.points)
    assert p.predict(vp9, 0, 60, floor * 1.9) is None
    assert p.predict(vp9, 0, 60, floor * 2.1) is not None


def test_split_balances_parts_and_fits_is_monotone():
    p = mk([1.0] * 20 + [3.0] * 40)
    _worst, cuts, parts = p.best_split(LIMIT, 2)
    # The cheap stretch should get the longer part.
    assert cuts[0] > 30
    assert abs(parts[0][0] - parts[1][0]) < 1.0
    assert p.fits_for(0, LIMIT, 25) >= p.fits_for(0, LIMIT, 30)


def test_json_round_trip_and_budget():
    p = mk([1.0, 2.0])
    q = Probe.from_json(p.to_json())
    assert q.curves[0].points[1].crf == 30 and q.slices == [(0.0, 3.0)]
    assert math.isclose(video_budget(LIMIT, 10), LIMIT * 8 * 0.985 / 1.006 / 10 / 1000)
