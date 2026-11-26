"""Pruning decisions from a synthetic Probe (no ffmpeg)."""

from fractions import Fraction

from fit4.encode import Candidate
from fit4.preflight import CandCurve, Point, Probe
from fit4.prune import decide

LIMIT = 4 * 1024 * 1024


def curve(codec, height, fps, base_inter, top_db=33.0):
    # Each CRF step of 8 roughly thirds the bits and costs ~2.5 dB (as in test_preflight).
    pts = [
        Point(crf, base_inter * 10 / 3**i, base_inter / 3**i, top_db - 2.5 * i, 0.9)
        for i, crf in enumerate((22, 30, 38, 46))
    ]
    return CandCurve(codec, height, fps, pts)


def cand(codec, h, fps=30):
    return Candidate(codec, h, Fraction(fps))


def probe_with(curves):
    return Probe(start=0.0, duration=30.0, src_fps=60.0, timeline=[1.0] * 30, reel_complexity=1.0,
                 slices=[(0.0, 2.0)], curves=curves)  # fmt: skip


def test_keeps_within_margin_and_the_top_n():
    # Budget ~ 1.1 Mbps. Predicted dB falls with height here via top_db.
    cs = [
        curve("x264", h, 30.0, 4000.0, top_db=t)
        for h, t in [(720, 33.0), (540, 32.4), (360, 31.8), (270, 28.0), (180, 25.0)]
    ]
    cands = [cand("x264", h) for h in (720, 540, 360, 270, 180)]
    ds = decide(probe_with(cs), cands, LIMIT, margin=1.5, min_keep=2)
    kept = [d.cand.height for d in ds if d.keep]
    assert kept == [720, 540, 360]  # 360p is 1.2 dB behind (kept); 270p 5 dB behind (dropped)
    slow = [d for d in ds if not d.keep]
    assert all("dB behind" in d.reason for d in slow)


def test_min_keep_beats_margin():
    cs = [curve("x264", h, 30.0, 4000.0, top_db=t) for h, t in [(720, 33.0), (540, 28.0), (360, 20.0)]]
    cands = [cand("x264", h) for h in (720, 540, 360)]
    ds = decide(probe_with(cs), cands, LIMIT, margin=0.1, min_keep=3)
    assert all(d.keep for d in ds)


def test_vp9_under_floor_is_dropped_whatever_its_rank():
    # Heavy content: the VP9 CRF-63 stand-in costs more than the budget / 2.
    cs = [curve("x264", 720, 30.0, 4000.0), curve("vp9", 720, 30.0, 120000.0, top_db=40.0)]
    ds = {d.cand.codec: d for d in decide(probe_with(cs), [cand("x264", 720), cand("vp9", 720)], LIMIT)}
    assert not ds["vp9"].keep and ds["vp9"].reason.startswith("budget under 2x") and ds["vp9"].predicted is None
    assert ds["x264"].keep


def test_offsets_move_codecs_onto_one_scale():
    cs = [curve("x264", 720, 30.0, 4000.0, top_db=33.0), curve("vp9", 720, 30.0, 1500.0, top_db=33.0)]
    cands = [cand("x264", 720), cand("vp9", 720)]
    flat = decide(probe_with(cs), cands, LIMIT, margin=0.0, min_keep=1)
    assert sum(d.keep for d in flat) == 1
    boosted = decide(probe_with(cs), cands, LIMIT, offsets={"vp9": 1.0}, margin=0.0, min_keep=1)
    assert {d.cand.codec for d in boosted if d.keep} == {"vp9"}
    assert (
        next(d for d in boosted if d.cand.codec == "vp9").predicted
        == (next(d for d in flat if d.cand.codec == "vp9").predicted or 0.0) + 1.0
    )


def test_unprobed_candidates_carry_their_reason():
    cs = [curve("x264", 720, 30.0, 4000.0)]
    c2 = cand("vp9", 540)
    ds = decide(probe_with(cs), [cand("x264", 720), c2], LIMIT, unprobed={c2: "ranked 5th of 4 for VP9 probing"})
    assert ds[1].reason == "ranked 5th of 4 for VP9 probing" and not ds[1].keep


def test_nothing_probed_keeps_nothing_but_does_not_crash():
    ds = decide(probe_with([]), [cand("x264", 720)], LIMIT)
    assert not ds[0].keep and ds[0].reason == "no probe curve"
