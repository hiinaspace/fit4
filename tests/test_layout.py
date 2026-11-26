"""Thread/job split and the CPU-budget scheduler (no ffmpeg)."""

import threading
import time

import pytest

from fit4.layout import Layout, cgroup_cpu_limit, parse_cpu_max, plan_layout, run_weighted


def test_vp9_gets_four_threads_and_x264_a_quarter():
    lay = plan_layout(16, ["x264", "vp9"])
    assert lay.want == {"x264": 4, "vp9": 4} and lay.budget == 16 and not lay.flat
    assert plan_layout(32, ["x264", "vp9"]).want == {"x264": 8, "vp9": 4}


def test_small_machines_keep_two_threads_per_job():
    lay = plan_layout(2, ["x264", "vp9"])
    assert lay.want == {"x264": 2, "vp9": 2}
    one = plan_layout(1, ["vp9"])
    assert one.budget == 1 and one.want["vp9"] == 2 and one.max_jobs == 1


def test_x264_may_shrink_to_fill_a_gap_but_vp9_may_not():
    lay = plan_layout(14, ["x264", "vp9"])
    assert lay.floor == {"x264": 2, "vp9": 4}


def test_jobs_caps_concurrency_and_flat_restores_the_old_split():
    assert plan_layout(16, ["vp9"], jobs=2).max_jobs == 2
    old = plan_layout(16, ["x264", "vp9"], flat=True)
    assert old.flat and old.max_jobs == 4 and old.want == {"x264": 4, "vp9": 4}
    old = plan_layout(14, ["x264", "vp9"], jobs=3, flat=True)
    assert old.max_jobs == 3 and old.want["vp9"] == 4  # 14 // 3, as before


def test_parse_cpu_max():
    assert parse_cpu_max("max 100000\n") is None
    assert parse_cpu_max("1400000 100000\n") == 14.0
    assert parse_cpu_max("50000 100000") == 0.5
    assert parse_cpu_max("garbage") is None and parse_cpu_max("1 0") is None


def test_cgroup_limit_takes_the_tightest_ancestor(tmp_path):
    (tmp_path / "proc").write_text("0::/user.slice/run-1.scope\n")
    root = tmp_path / "cg"
    (root / "user.slice/run-1.scope").mkdir(parents=True)
    (root / "user.slice/run-1.scope/cpu.max").write_text("max 100000\n")
    (root / "user.slice/cpu.max").write_text("1400000 100000\n")
    assert cgroup_cpu_limit(str(tmp_path / "proc"), str(root)) == 14.0
    (root / "user.slice/run-1.scope/cpu.max").write_text("800000 100000\n")
    assert cgroup_cpu_limit(str(tmp_path / "proc"), str(root)) == 8.0
    assert cgroup_cpu_limit(str(tmp_path / "missing"), str(root)) is None


def watch(layout, items, hold=0.03):
    """Run items through the scheduler; return (peak threads in use, peak jobs, per-item thread counts)."""
    lock = threading.Lock()
    cur = {"threads": 0, "jobs": 0, "peak_t": 0, "peak_j": 0}
    got = {}

    def fn(item, threads):
        with lock:
            cur["threads"] += threads
            cur["jobs"] += 1
            cur["peak_t"] = max(cur["peak_t"], cur["threads"])
            cur["peak_j"] = max(cur["peak_j"], cur["jobs"])
            got[item] = threads
        time.sleep(hold)
        with lock:
            cur["threads"] -= threads
            cur["jobs"] -= 1
        return item

    out = run_weighted(items, fn, layout, lambda it: it.split("-")[0])
    assert out == items  # results keep the input order
    return cur["peak_t"], cur["peak_j"], got


def test_scheduler_never_exceeds_the_budget_and_fills_it():
    lay = plan_layout(16, ["vp9"])
    peak_t, peak_j, got = watch(lay, [f"vp9-{i}" for i in range(10)])
    assert peak_t <= 16 and peak_j == 4 and set(got.values()) == {4}


def test_x264_backfills_the_gap_a_vp9_job_cannot_use():
    lay = plan_layout(14, ["x264", "vp9"])
    # three vp9 jobs take 12 of 14 threads; the x264 job behind them starts at once on the last 2.
    peak_t, peak_j, got = watch(lay, [f"vp9-{i}" for i in range(3)] + ["x264-0"], hold=0.1)
    assert peak_j == 4 and peak_t == 14 and got["x264-0"] == 2


def test_jobs_cap_applies_even_with_budget_to_spare():
    peak_t, peak_j, _ = watch(plan_layout(16, ["x264"], jobs=2), [f"x264-{i}" for i in range(6)])
    assert peak_j == 2 and peak_t == 8


def test_flat_layout_runs_equal_workers():
    _t, peak_j, got = watch(plan_layout(16, ["x264", "vp9"], flat=True), [f"vp9-{i}" for i in range(6)])
    assert peak_j == 4 and set(got.values()) == {4}


def test_budget_below_the_floor_still_runs_one_job():
    lay = Layout(budget=1, max_jobs=1, want={"vp9": 4}, floor={"vp9": 4})
    _t, peak_j, _ = watch(lay, ["vp9-0", "vp9-1"])
    assert peak_j == 1


def test_first_error_stops_new_starts_and_is_raised():
    started = []

    def fn(item, threads):
        started.append(item)
        if item == "vp9-1":
            raise ValueError("boom")
        time.sleep(0.02)

    lay = plan_layout(4, ["vp9"], jobs=1)
    with pytest.raises(ValueError, match="boom"):
        run_weighted([f"vp9-{i}" for i in range(6)], fn, lay, lambda it: "vp9")
    assert started == ["vp9-0", "vp9-1"]
