import math

from fit4.onepass import adjust_db, predict_crf
from fit4.preflight import CandCurve, Point, Probe


def make_probe(codec="vp9") -> tuple[Probe, CandCurve]:
    # inter_bytes halves every 10 CRF; 10 s window at 30 fps; timeline flat so complexity == 1.
    pts = [Point(crf, 0.0, 2000 * 2 ** (-(crf - 30) / 10), 60.0 - 0.5 * (crf - 30), 0.9) for crf in (30, 42, 54, 63)]
    curve = CandCurve(codec, 720, 30.0, pts)
    probe = Probe(0.0, 10.0, 30.0, [1.0] * 10, 1.0, [(0.0, 2.0)], [curve])
    return probe, curve


def test_predict_crf_interpolates_and_inverts():
    probe, curve = make_probe()
    a, b = probe.start, probe.start + probe.duration
    target = probe.kbps_at(curve, curve.points[1], a, b)  # the CRF-42 point's own bitrate
    crf, slope = predict_crf(probe, curve, target)
    assert crf == 42
    assert 2.0 <= slope <= 9.0


def test_predict_crf_extrapolates_and_clamps():
    probe, curve = make_probe()
    crf, _ = predict_crf(probe, curve, 1e-3)  # far below the floor: top CRF
    assert crf == 63
    crf, _ = predict_crf(probe, curve, 1e9)
    assert crf == 10


def test_adjust_db_moves_along_slope():
    assert adjust_db(30.0, 2_000_000, 4_000_000, 5.0) == 30.0 + 5.0 * math.log(2)
    assert adjust_db(30.0, 4_000_000, 4_000_000, 5.0) == 30.0


def test_next_rates_interpolates_between_under_and_over():
    from fit4.search import next_rates

    limit = 1_000_000
    ks = next_rates([(1000.0, 900_000), (1100.0, 1_050_000)], limit, 0.97, 2)
    assert 1000 < ks[0] < 1100  # lands between the bracketing points
    # all under the band: scale up by the size ratio
    assert next_rates([(1000.0, 500_000)], limit, 0.97, 1)[0] > 1900
    # all over the limit: scale down
    assert next_rates([(1000.0, 1_500_000)], limit, 0.97, 1)[0] < 700
