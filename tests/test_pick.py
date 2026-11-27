from fractions import Fraction

from fit4.encode import Candidate
from fit4.search import pick_by_prediction


def c(h):
    return Candidate("vp9", h, Fraction(60))


def test_pick_by_prediction_keeps_close_seconds_only():
    ranked = [(c(1080), 35.0), (c(900), 34.8), (c(720), 33.0)]
    assert pick_by_prediction(ranked, 3, 0.5) == [c(1080), c(900)]
    assert pick_by_prediction(ranked, 1, 0.5) == [c(1080)]
    assert pick_by_prediction(ranked, 2, 0.1) == [c(1080)]
    assert pick_by_prediction([], 2, 0.5) == []


def c30(h):
    return Candidate("vp9", h, Fraction(30))


def test_hedge_adds_best_other_frame_rate_only_at_the_ceiling():
    from fit4.search import hedge_finals

    ranked = [(c(720), 58.0), (c(900), 57.9), (c30(720), 56.0), (c30(540), 55.0)]
    fin = [c(720)]
    assert hedge_finals(fin, ranked, set(), 2) == fin
    assert hedge_finals(fin, ranked, {c(720).label()}, 1) == fin
    assert hedge_finals(fin, ranked, {c(720).label()}, 2) == [c(720), c30(720)]
    both = [c(720), c30(720)]
    assert hedge_finals(both, ranked, {c(720).label()}, 2) == both
