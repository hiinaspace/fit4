"""Baseline-only instrumentation: collect per-try / per-candidate timings into a JSON file (FIT4_DUMP=path)."""

from __future__ import annotations

import json
import os
import threading
import time

_lock = threading.Lock()
RECORDS: dict[tuple[str, bool], dict] = {}
META: dict = {"t_start": time.time()}
tls = threading.local()


def enabled() -> bool:
    return bool(os.environ.get("FIT4_DUMP"))


def add(label: str, final: bool, rec: dict) -> None:
    with _lock:
        RECORDS[(label, final)] = rec


def update(label: str, final: bool, **kw) -> None:
    with _lock:
        RECORDS.setdefault((label, final), {}).update(kw)


def write() -> None:
    path = os.environ.get("FIT4_DUMP")
    if not path:
        return
    META["t_end"] = time.time()
    META["wall_s"] = round(META["t_end"] - META["t_start"], 1)
    META["loadavg_end"] = os.getloadavg()
    with _lock:
        recs = sorted(RECORDS.values(), key=lambda r: (r.get("final", False), r.get("t_begin", 0)))
    with open(path, "w") as f:
        json.dump({"meta": META, "candidates": recs}, f, indent=1, default=str)
