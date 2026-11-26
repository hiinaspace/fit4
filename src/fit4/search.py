"""Screen a codec x height x fps grid at a fast preset, then final-encode the leaders."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .boards import Board
from .encode import Candidate, EncodeResult, encode_to_size
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
    jobs: int,
    audio: bool,
    log: Callable[[str], None],
    on_done: Callable[[], None] = lambda: None,
) -> list[Trial]:
    threads = max(2, (os.cpu_count() or 4) // jobs)
    low, tries = (0.975, 4) if final else (0.95, 3)

    def one(c: Candidate) -> Trial:
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
        )
        sc = score(src, enc.path, enc.path.with_suffix(".xpsnr.txt"))
        log(
            f"  {c.label():<18} {enc.size / 1e6:6.3f} MB  {enc.kbps:5.0f} kbps  "
            f"XPSNR {sc.xpsnr_yuv:6.3f}  SSIM {sc.ssim_y:.4f}  ({enc.seconds:.0f}s, {len(enc.attempts)} tries)"
        )
        on_done()
        return Trial(enc, sc)

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        return list(ex.map(one, cands))


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
