"""Use a pre-flight Probe to drop screening candidates that cannot win.

Pure: takes a Probe and the candidate grid, returns one Decision per candidate
with the reason, so the sweep can log what was skipped and why.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .encode import Candidate
from .preflight import Probe

DEFAULT_MARGIN_DB = 1.5
DEFAULT_MIN_KEEP = 4


@dataclass(frozen=True)
class Decision:
    cand: Candidate
    keep: bool
    reason: str  # "kept", "floor", "margin", "unprobed" ...
    predicted: float | None  # dB (probe prediction plus the fast-setting offset); None if infeasible/unprobed

    def line(self) -> str:
        p = "   n/a" if self.predicted is None else f"{self.predicted:6.2f}"
        return f"{self.cand.label():<18} predicted {p} dB  {'keep' if self.keep else 'skip'}: {self.reason}"


def curve_for(probe: Probe, cand: Candidate):
    for c in probe.curves:
        if c.codec == cand.codec and c.height == cand.height and abs(c.fps - float(cand.fps)) < 1e-3:
            return c
    return None


def decide(
    probe: Probe,
    cands: Sequence[Candidate],
    limit: int,
    *,
    audio_kbps: Mapping[str, float] | None = None,
    offsets: Mapping[str, float] | None = None,
    margin: float = DEFAULT_MARGIN_DB,
    min_keep: int = DEFAULT_MIN_KEEP,
    unprobed: Mapping[Candidate, str] | None = None,
) -> list[Decision]:
    """Keep candidates within `margin` dB of the predicted best, or the top `min_keep`, whichever is more.

    offsets: dB to add per codec to turn a fast-setting prediction into the
    screening setting's (so x264 and VP9 compare on one scale). unprobed:
    candidates the probe deliberately skipped, with the reason to log. A VP9
    candidate under the bitrate floor is dropped whatever its rank.
    """
    from .preflight import video_budget

    offs = offsets or {}
    a, b = probe.start, probe.start + probe.duration
    scored: list[tuple[float | None, Candidate, str]] = []
    for c in cands:
        if unprobed and c in unprobed:
            scored.append((None, c, unprobed[c]))
            continue
        curve = curve_for(probe, c)
        if curve is None:
            scored.append((None, c, "unprobed"))
            continue
        kb = video_budget(limit, b - a, (audio_kbps or {}).get(c.codec, 0.0))
        q = probe.predict(curve, a, b, kb) if kb > 0 else None
        if q is None:
            scored.append((None, c, "floor"))
        else:
            scored.append((q + offs.get(c.codec, 0.0), c, "kept"))
    ranked = sorted((s for s in scored if s[0] is not None), key=lambda s: s[0], reverse=True)
    best = ranked[0][0] if ranked else None
    keep_set = set()
    for i, (q, c, _) in enumerate(ranked):
        if i < min_keep or (best is not None and q is not None and q >= best - margin):
            keep_set.add(c)
    out = []
    for q, c, why in scored:
        if q is None:
            out.append(Decision(c, False, _explain(why), None))
        elif c in keep_set:
            out.append(Decision(c, True, "kept", q))
        else:
            out.append(Decision(c, False, f"{best - q:.2f} dB behind the predicted best (margin {margin:g})", q))  # ty: ignore[unsupported-operator]
    return out


def _explain(why: str) -> str:
    return {
        "floor": "budget under 2x the VP9 CRF-63 bitrate: libvpx 2-pass would not get under the limit",
        "unprobed": "no probe curve",
    }.get(why, why)
