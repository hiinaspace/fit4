from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from fit4.boards import BOARDS, MIB
from fit4.encode import Candidate, budget_kbps, norm_filter
from fit4.probe import Source
from fit4.report import pick_frames
from fit4.search import grid

BASE = Source(
    path=Path("x.mp4"), width=1280, height=720, fps=Fraction(24), duration=90.0, nb_frames=2160,
    full_range=True, colorspace="bt470bg", has_audio=False, size=30_000_000,
)  # fmt: skip


def src(**kw) -> Source:
    return replace(BASE, **kw)


def test_budget_no_audio_fits_limit():
    s = src()
    kbps = budget_kbps(s, Candidate("x264", 720, Fraction(24)), 4 * MIB, audio=True)
    assert kbps * 1000 * s.duration / 8 < 4 * MIB
    assert kbps > 360


def test_budget_subtracts_audio():
    c = Candidate("x264", 720, Fraction(24))
    assert budget_kbps(src(has_audio=True), c, 4 * MIB, True) < budget_kbps(src(), c, 4 * MIB, True) - 90


def test_grid_heights_even_and_fps_halves():
    cands = grid(src(), BOARDS["4chan"], ["x264"], [1, 0.833, 0.5], [1, 2])
    assert {c.height for c in cands} == {720, 600, 360}
    assert {c.fps for c in cands} == {Fraction(24), Fraction(12)}
    assert all(c.width_for(src()) % 2 == 0 for c in cands)


def test_grid_drops_too_low_fps():
    cands = grid(src(fps=Fraction(15)), BOARDS["4chan"], ["vp9"], [1], [1, 2])
    assert [c.fps for c in cands] == [Fraction(15)]


def test_norm_filter_converts_range_and_matrix():
    f = norm_filter(src(), 360, Fraction(12))
    assert f.startswith("fps=fps=12,")
    assert "in_range=pc:out_range=tv" in f and "in_color_matrix=bt601:out_color_matrix=bt709" in f


def test_pick_frames_one_per_window():
    vals = [5.0] * 30
    vals[3], vals[14], vals[29] = 1.0, 2.0, 0.5
    assert pick_frames(vals) == [3, 14, 29]
