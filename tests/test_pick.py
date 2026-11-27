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
