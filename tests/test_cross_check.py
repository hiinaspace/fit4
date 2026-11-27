from types import SimpleNamespace as NS

from fit4 import cli
from fit4.encode import Candidate


def trial(codec, h, db):
    c = Candidate(codec, h, 60)
    return NS(cand=c, score=NS(primary=db), enc=NS(path=NS(unlink=lambda **k: None)))


def setup(monkeypatch, x_screen, x_final):
    calls = []
    monkeypatch.setattr(cli, "run_stage", lambda name, src, cs, *a, **k: calls.append("screen") or [trial("x264", 720, x_screen)])
    monkeypatch.setattr(cli, "run_final_stage", lambda src, fin, *a: calls.append("final") or [trial("x264", 720, x_final)])
    monkeypatch.setattr(cli, "log", lambda m: None)
    cands = [Candidate(c, h, 60) for c in ("x264", "vp9") for h in (720, 540)]
    return calls, cands


def run(cands, finals, margin=0.5):
    return cli.cross_check_x264(None, finals, cands, 1, None, NS(budget=1), True, None, margin)


def test_close_screen_gets_final_and_can_win(monkeypatch):
    calls, cands = setup(monkeypatch, 40.0, 41.0)
    out = run(cands, [trial("vp9", 720, 40.2)])
    assert calls == ["screen", "final"]
    assert max(out, key=lambda t: t.score.primary).cand.codec == "x264"


def test_far_behind_screen_skips_final(monkeypatch):
    calls, cands = setup(monkeypatch, 38.0, 41.0)
    out = run(cands, [trial("vp9", 720, 40.2)])
    assert calls == ["screen"] and len(out) == 1


def test_x264_lead_or_off_does_nothing(monkeypatch):
    calls, cands = setup(monkeypatch, 40.0, 41.0)
    assert len(run(cands, [trial("x264", 720, 40.2)])) == 1
    assert len(run(cands, [trial("vp9", 720, 40.2)], margin=-1)) == 1
    assert calls == []
