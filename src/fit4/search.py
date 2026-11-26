"""Screen a codec x height x fps grid at a fast preset, then final-encode the leaders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .boards import Board
from .encode import Candidate, EncodeResult, FirstPassCache, encode_to_size
from .layout import Layout, run_weighted
from .metrics import Score, score
from .probe import Source


@dataclass
class Trial:
    enc: EncodeResult
    score: Score

    @property
    def cand(self) -> Candidate:
        return self.enc.cand


def grid(
    src: Source,
    board: Board,
    codecs: list[str],
    scales: list[float],
    fps_divs: list[int],
) -> list[Candidate]:
    h_max = min(src.height, board.max_dim, int(board.max_dim * src.height / src.width))
    heights = sorted({(min(h_max, round(src.height * s)) // 2) * 2 for s in scales}, reverse=True)
    # Skip scales below 144p, but never drop the largest (a tiny source still gets one).
    heights = [h for h in heights if h >= 144] or heights[:1]
    rates = []
    for d in fps_divs:
        r = src.fps / d
        if d == 1 or r >= 10:
            rates.append(Fraction(r))
    return [Candidate(c, h, r) for c in codecs for h in heights for r in rates]


def run_trials(
    src: Source,
    cands: list[Candidate],
    limit: int,
    work: Path,
    *,
    final: bool,
    layout: Layout,
    audio: bool,
    log: Callable[[str], None],
    on_done: Callable[[], None] = lambda: None,
    cache: FirstPassCache | None = None,
) -> list[Trial]:
    low, tries = (0.975, 4) if final else (0.95, 3)

    def one(c: Candidate, threads: int) -> Trial | None:
        try:
            enc = encode_to_size(
                src,
                c,
                limit,
                work,
                final=final,
                threads=threads,
                audio=audio,
                low=low,
                max_tries=tries,
                cache=cache,
            )
        except RuntimeError as e:  # one candidate that can't reach the limit shouldn't sink the sweep
            log(f"  {c.label():<18} skipped: {e}")
            on_done()
            return None
        sc = score(src, enc.path, enc.path.with_suffix(".xpsnr.txt"))
        log(
            f"  {c.label():<18} {enc.size / 1e6:6.3f} MB  {enc.kbps:5.0f} kbps  "
            f"XPSNR {sc.xpsnr_yuv:6.3f}  SSIM {sc.ssim_y:.4f}  ({enc.seconds:.0f}s, {len(enc.attempts)} tries)"
        )
        on_done()
        return Trial(enc, sc)

    # Slowest first (VP9 before x264) so short jobs fill the tail; results come back in `cands` order.
    order = sorted(range(len(cands)), key=lambda i: cands[i].codec != "vp9")
    done = run_weighted([cands[i] for i in order], one, layout, lambda c: c.codec)
    by_index = dict(zip(order, done))
    trials = [t for i in range(len(cands)) if (t := by_index[i]) is not None]
    if not trials:
        raise RuntimeError("every candidate failed to reach the size limit; try a larger --limit or a shorter clip")
    return trials


def pick_finalists(trials: list[Trial], n: int) -> list[Candidate]:
    """Best overall plus the best of each other codec, up to n."""
    ranked = sorted(trials, key=lambda t: t.score.primary, reverse=True)
    picks: list[Candidate] = [ranked[0].cand]
    for t in ranked[1:]:
        if len(picks) >= n:
            break
        if t.cand.codec not in {c.codec for c in picks}:
            picks.append(t.cand)
    for t in ranked[1:]:
        if len(picks) >= n:
            break
        if t.cand not in picks:
            picks.append(t.cand)
    return picks
