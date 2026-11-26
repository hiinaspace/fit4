"""How many encodes run side by side and how many threads each gets.

libvpx scales poorly with threads (measured 4 -> 16 threads gives 1.0-1.6x),
so narrow VP9 jobs side by side beat a few wide ones. Jobs are scheduled by
CPU budget: each takes `threads` slots of the machine's budget, and a job
that fits is started even when an earlier one in the queue does not
(backfill), so the remainder of the budget never sits idle while work waits.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

VP9_THREADS = 4
X264_JOBS = 4  # x264 keeps the old split: about budget/4 threads each
MIN_THREADS = 2
LEGACY_JOBS = 4


@dataclass(frozen=True)
class Layout:
    budget: int  # thread slots available
    max_jobs: int  # most encodes in flight at once
    want: dict[str, int]  # threads a job of this codec asks for
    floor: dict[str, int]  # fewest threads a job of this codec may take (backfill)
    flat: bool = False  # the old layout: a fixed number of equal workers

    def describe(self) -> str:
        if self.flat:
            return f"flat: {self.max_jobs} jobs x {next(iter(self.want.values()))} threads"
        return f"budget {self.budget} threads: " + ", ".join(
            f"{c} {w} threads (min {self.floor[c]})" for c, w in self.want.items()
        )


def plan_layout(cpus: int, codecs: Sequence[str], jobs: int | None = None, flat: bool = False) -> Layout:
    """Pure: the thread/job split for a machine with `cpus` usable threads.

    flat=True restores the pre-budget layout (`jobs` equal workers, cpus // jobs
    threads each, default 4 jobs). Otherwise VP9 jobs get VP9_THREADS, x264
    jobs cpus // 4 (at least 2) and may shrink to MIN_THREADS to fill a gap;
    `jobs` only caps how many run at once.
    """
    cpus = max(1, cpus)
    if flat:
        n = jobs or LEGACY_JOBS
        t = max(MIN_THREADS, cpus // n)
        return Layout(cpus, n, {c: t for c in codecs}, {c: t for c in codecs}, flat=True)
    want = {"vp9": min(VP9_THREADS, max(MIN_THREADS, cpus)), "x264": max(MIN_THREADS, cpus // X264_JOBS)}
    floor = {"vp9": want["vp9"], "x264": MIN_THREADS}
    cap = jobs or max(1, cpus // MIN_THREADS)
    return Layout(cpus, cap, {c: want[c] for c in codecs}, {c: floor[c] for c in codecs})


def parse_cpu_max(text: str) -> float | None:
    """CPUs granted by a cgroup-v2 `cpu.max` line ('max 100000' or '1400000 100000'); None if unlimited."""
    parts = text.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if period > 0 else None


def cgroup_cpu_limit(proc_cgroup: str = "/proc/self/cgroup", root: str = "/sys/fs/cgroup") -> float | None:
    """Tightest cpu.max in this process's cgroup and its ancestors (local-job sets one on its scope)."""
    try:
        line = next(ln for ln in Path(proc_cgroup).read_text().splitlines() if ln.startswith("0::"))
    except (OSError, StopIteration):
        return None
    path = Path(line[3:])
    best: float | None = None
    for p in [path, *path.parents]:
        try:
            lim = parse_cpu_max((Path(root) / str(p).lstrip("/") / "cpu.max").read_text())
        except OSError:
            continue
        if lim is not None and (best is None or lim < best):
            best = lim
    return best


def available_cpus() -> int:
    """Threads this process may really use: affinity, then any cgroup CPU quota (floored)."""
    n = len(os.sched_getaffinity(0)) or os.cpu_count() or 4
    lim = cgroup_cpu_limit()
    return max(1, min(n, math.floor(lim))) if lim is not None else n


def run_weighted[T, R](
    items: Sequence[T],
    fn: Callable[[T, int], R],
    layout: Layout,
    codec_of: Callable[[T], str],
) -> list[R]:
    """Run fn(item, threads) for every item within the layout's thread budget; results in item order.

    Items start in order, except that a later item that fits the free budget
    starts ahead of an earlier one that does not. The first exception stops
    new starts and is re-raised once running jobs finish.
    """
    n = len(items)
    results: list[R | None] = [None] * n
    cond = threading.Condition()
    state = {"free": layout.budget, "running": 0}
    errors: list[BaseException] = []
    workers: list[threading.Thread] = []

    def work(i: int, threads: int) -> None:
        try:
            results[i] = fn(items[i], threads)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller's thread
            errors.append(e)
        finally:
            with cond:
                state["free"] += threads
                state["running"] -= 1
                cond.notify_all()

    pending = list(range(n))
    with cond:
        while pending and not errors:
            pick = None
            if state["running"] < layout.max_jobs:
                for i in pending:
                    c = codec_of(items[i])
                    th = min(layout.want[c], state["free"])
                    if th >= layout.floor[c]:
                        pick = (i, th)
                        break
                if pick is None and state["running"] == 0:  # budget below every floor: run anyway
                    i = pending[0]
                    pick = (i, layout.floor[codec_of(items[i])])
            if pick is None:
                cond.wait()
                continue
            i, th = pick
            pending.remove(i)
            state["free"] -= th
            state["running"] += 1
            t = threading.Thread(target=work, args=(i, th))
            workers.append(t)
            t.start()
    for t in workers:
        t.join()
    if errors:
        raise errors[0]
    return cast(list[R], results)
