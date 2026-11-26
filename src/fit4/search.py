"""Screen a codec x height x fps grid at a fast preset, then final-encode the leaders."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .boards import Board
from .encode import (
    EXT,
    Candidate,
    EncodeResult,
    FirstPassCache,
    budget_kbps,
    encode_once,
    encode_to_size,
    stalled,
)
from .layout import Layout, run_weighted
from .metrics import Score, score
from .probe import Source


@dataclass
class Trial:
    enc: EncodeResult
    score: Score
    rank: float | None = None  # screening rank score when it differs from the raw XPSNR (one-pass screening)

    @property
    def key(self) -> float:
        return self.score.primary if self.rank is None else self.rank

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


def run_onepass(
    src: Source,
    cands: list[Candidate],
    limit: int,
    work: Path,
    probe,
    *,
    layout: Layout,
    audio: bool,
    log: Callable[[str], None],
    on_done: Callable[[], None] = lambda: None,
) -> list[Trial]:
    """One-pass screening (see onepass.py): one CRF encode per candidate, ranked by the budget-adjusted XPSNR."""
    from .onepass import screen_one
    from .prune import curve_for

    def one(c: Candidate, threads: int) -> Trial | None:
        curve = curve_for(probe, c)
        if curve is None:
            on_done()
            return None
        enc, sc, adj = screen_one(src, c, probe, curve, limit, work, threads, audio)
        log(
            f"  {c.label():<18} {enc.size / 1e6:6.3f} MB  {enc.kbps:5.0f} kbps  CRF {int(enc.attempts[0][0])}  "
            f"XPSNR {sc.xpsnr_yuv:6.3f}  at budget {adj:6.3f}  ({enc.seconds:.0f}s)"
        )
        on_done()
        return Trial(enc, sc, adj)

    order = sorted(range(len(cands)), key=lambda i: cands[i].codec != "vp9")
    done = run_weighted([cands[i] for i in order], one, layout, lambda c: c.codec)
    by_index = dict(zip(order, done))
    trials = [t for i in range(len(cands)) if (t := by_index[i]) is not None]
    if not trials:
        raise RuntimeError("one-pass screening produced nothing")
    return trials


def pick_finalists(trials: list[Trial], n: int) -> list[Candidate]:
    """Best overall plus the best of each other codec, up to n."""
    ranked = sorted(trials, key=lambda t: t.key, reverse=True)
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


# -- speculative final fitting ---------------------------------------------------

# Bitrate multipliers tried side by side in the first round, by how many fit per candidate.
# libvpx 2-pass at the budget bitrate lands at 0.95-0.99 of the limit (median 0.976, 64 first
# tries on the eval windows), so nudging up covers the band without a serial retry.
FIRST_ROUND = {1: [1.0], 2: [1.0, 1.02], 3: [1.0, 1.015, 1.03]}


def next_rates(points: list[tuple[float, int]], limit: int, low: float, width: int) -> list[float]:
    """Bitrates (kbps) to try next, given (kbps, size) results so far that are all outside [low*limit, limit].

    Aims at the middle of the band. Between a bracketing under/over pair it
    interpolates in ln-size; otherwise it scales the nearest point by the
    size ratio (video bits follow the requested bitrate about linearly).
    """
    mid = limit * (1 + low) / 2
    under = [p for p in points if p[1] < low * limit]
    over = [p for p in points if p[1] > limit]
    if under and over:
        (k0, s0), (k1, s1) = max(under, key=lambda p: p[1]), min(over, key=lambda p: p[1])
        t = (math.log(mid) - math.log(s0)) / (math.log(s1) - math.log(s0))
        k = math.exp(math.log(k0) + t * (math.log(k1) - math.log(k0)))
    else:
        k0, s0 = max(points, key=lambda p: p[1]) if under else min(points, key=lambda p: p[1])
        k = k0 * mid / s0
    spread = [1.0, 0.985, 1.015][:width]
    return [k * m for m in spread]


def run_finals(
    src: Source,
    cands: list[Candidate],
    limit: int,
    work: Path,
    *,
    budget: int,
    audio: bool,
    low: float,
    max_rounds: int,
    log: Callable[[str], None],
    cache: FirstPassCache | None = None,
    overrides: dict[str, str] | None = None,
    on_done: Callable[[], None] = lambda: None,
) -> list[Trial]:
    """Fit every candidate to the size band with 2-pass encodes tried side by side, in rounds.

    Round 1 runs a few bitrates per candidate at once (FIRST_ROUND); a candidate
    is done as soon as one result lands in [low*limit, limit], else the next
    round aims between its results. Only the chosen encode of each candidate is
    scored. A candidate that never gets under the limit is logged and dropped.
    """
    width = max(1, min(3, 4 // max(1, len(cands))))
    todo: dict[Candidate, list[float]] = {
        c: [budget_kbps(src, c, limit, audio) * m for m in FIRST_ROUND[width]] for c in cands
    }
    results: dict[Candidate, list[tuple[float, int, Path]]] = {c: [] for c in cands}
    secs: dict[Candidate, float] = dict.fromkeys(cands, 0.0)
    done: set[Candidate] = set()
    n_try = 0
    for rnd in range(max_rounds):
        jobs: list[tuple[Candidate, float, Path]] = []
        for c in cands:
            if c in done:
                continue
            for k in todo[c]:
                jobs.append((c, k, work / f"{c.label()}.final.try{n_try}.{EXT[c.codec]}"))
                n_try += 1
        if not jobs:
            break
        th = max(2, min(8, budget // len(jobs)))
        lay = Layout(budget, len(jobs), {c.codec: th for c, _, _ in jobs}, {c.codec: th for c, _, _ in jobs})

        def one(j: tuple[Candidate, float, Path], threads: int) -> int:
            c, k, path = j
            t0 = time.monotonic()
            size = encode_once(
                src, c, k, path, final=True, threads=threads, audio=audio, cache=cache, vp9_overrides=overrides
            )
            secs[c] += time.monotonic() - t0
            return size

        sizes = run_weighted(jobs, one, lay, lambda j: j[0].codec)
        for (c, k, p), sz in zip(jobs, sizes):
            results[c].append((k, sz, p))
        for c in cands:
            if c in done:
                continue
            pts = [(k, sz) for k, sz, _ in results[c]]
            if any(low * limit <= sz <= limit for _, sz in pts):
                done.add(c)
                continue
            ranked = sorted(pts, reverse=True)
            # size not following the bitrate is libvpx's floor: more of the same would repeat it
            if (
                c.codec == "vp9"
                and len(ranked) >= 2
                and ranked[-1][0] < 0.95 * ranked[0][0]
                and stalled(ranked[0], ranked[-1], limit)
            ):
                done.add(c)
                continue
            todo[c] = next_rates(pts, limit, low, 2)
        log(f"  final round {rnd + 1}: {len(jobs)} encodes, {len(done)}/{len(cands)} candidates in the band")
    trials = []
    for c in cands:
        fit = [r for r in results[c] if r[1] <= limit]
        if not fit:
            log(f"  {c.label():<18} skipped: never got under the limit ({[(round(k), sz) for k, sz, _ in results[c]]})")
            on_done()
            continue
        k, sz, p = max(fit, key=lambda r: r[1])
        out = work / f"{c.label()}.final.{EXT[c.codec]}"
        p.rename(out)
        for _, _, q in results[c]:
            q.unlink(missing_ok=True)
        enc = EncodeResult(c, out, k, sz, secs[c], [(kk, ss) for kk, ss, _ in results[c]])
        sc = score(src, out, out.with_suffix(".xpsnr.txt"))
        log(
            f"  {c.label():<18} {sz / 1e6:6.3f} MB  {k:5.0f} kbps  XPSNR {sc.xpsnr_yuv:6.3f}  SSIM {sc.ssim_y:.4f}  ({len(results[c])} encodes)"
        )
        on_done()
        trials.append(Trial(enc, sc))
    return trials
