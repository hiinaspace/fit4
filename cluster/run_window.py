"""Run the baseline sweep on one window and record a per-candidate JSON (same shape as the local oracle).

Usage: python3 run_window.py ID [--windows windows.json] [--out DIR] [--scratch DIR] [--ncpu N]
Writes DIR/ID.json atomically; skips the window if that file exists (resume). A failure writes DIR/ID.error.json
and exits 1. Big files stay on local disk (SCRATCH); DIR/ID/ keeps the winning encode and the full log.
"""
import argparse, json, os, platform, resource, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF_NCPU = 16  # the reference setting: fit4 then runs 4 encodes at once with 4 threads each


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def cgroup_cpu_s() -> float | None:
    try:
        for line in open("/sys/fs/cgroup/cpu.stat"):
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return None


def host_info(ncpu: int) -> dict:
    model = ""
    try:
        model = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except (OSError, StopIteration):
        pass
    try:
        cpu_max = open("/sys/fs/cgroup/cpu.max").read().strip()
    except OSError:
        cpu_max = None
    ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[:1]
    return {"node": platform.node(), "cpu_model": model, "os_cpu_count": os.cpu_count(),
            "cc_job_cpu": os.environ.get("CC_JOB_CPU"), "cgroup_cpu_max": cpu_max, "fit4_cpu_count": ncpu,
            "ffmpeg": ff[0] if ff else None, "python": platform.python_version()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--windows", type=Path, default=HERE / "windows.json")
    ap.add_argument("--out", type=Path, default=Path("/videos-out/cluster"))
    ap.add_argument("--scratch", type=Path, default=Path(os.environ.get("FIT4_SCRATCH", "/w/scratch")))
    ap.add_argument("--ncpu", type=int, default=REF_NCPU)
    ap.add_argument("--timeout", type=float, default=3 * 3600)
    a = ap.parse_args()
    wins = {w["id"]: w for w in json.load(open(a.windows))}
    w = wins[a.id]
    a.out.mkdir(parents=True, exist_ok=True)
    final, err = a.out / f"{a.id}.json", a.out / f"{a.id}.error.json"
    if final.exists():
        print(a.id, "already done, skipping", flush=True)
        return 0
    work = a.scratch / a.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    rec: dict = {"window": w, "host": host_info(a.ncpu)}
    t_all = time.time()
    try:
        # One local copy of the source, reused by the next window if it is the same recording.
        src = Path(w["path"])
        cache = a.scratch / ("src-" + src.name)
        if not cache.exists():
            for old in a.scratch.glob("src-*"):
                old.unlink()
            t = time.time()
            shutil.copyfile(src, cache.with_name(cache.name + ".part"))
            os.replace(cache.with_name(cache.name + ".part"), cache)
            rec["copy_s"] = round(time.time() - t, 1)
        else:
            rec["copy_s"] = 0.0
        local = work / f"{a.id}{src.suffix}"
        os.symlink(cache, local)
        dump = work / "dump.json"
        env = {**os.environ, "FIT4_DUMP": str(dump), "PYTHONPATH": os.environ.get("PYTHONPATH") or str(HERE.parent / "src")}
        cmd = [sys.executable, str(HERE / "fit4_runner.py"), str(a.ncpu), str(local), "--start", str(w["start"]),
               "--duration", str(w["duration"]), "--no-audio", "--work", str(work / "work")]
        cg0, ru0 = cgroup_cpu_s(), resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.time()
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=a.timeout)
        wall = time.time() - t0
        ru1, cg1 = resource.getrusage(resource.RUSAGE_CHILDREN), cgroup_cpu_s()
        rec.update(exit=r.returncode, wall_s=round(wall, 1), locked_wall_s=round(wall, 1), waited_for_lock_s=0.0,
                   cpu_s={"children_user": round(ru1.ru_utime - ru0.ru_utime, 1),
                          "children_sys": round(ru1.ru_stime - ru0.ru_stime, 1),
                          "cgroup": None if cg0 is None or cg1 is None else round(cg1 - cg0, 1)},
                   stderr_tail=r.stderr[-3000:].splitlines()[-25:])
        if dump.exists():
            rec["dump"] = json.load(open(dump))
        win_dir = a.out / a.id
        win_dir.mkdir(exist_ok=True)
        (win_dir / "log.txt").write_text(r.stderr[-200_000:])
        for f in work.glob("*.fit4.*"):
            shutil.copyfile(f, win_dir / f.name)
            rec["winner_file"] = f.name
        if r.returncode != 0:
            raise RuntimeError(f"fit4 exit {r.returncode}")
        rec["total_s"] = round(time.time() - t_all, 1)
        atomic_json(final, rec)
        err.unlink(missing_ok=True)
        print(a.id, "ok", rec["wall_s"], "s", flush=True)
        return 0
    except BaseException as e:  # record and let the shard carry on
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["total_s"] = round(time.time() - t_all, 1)
        atomic_json(err, rec)
        print(a.id, "FAILED", rec["error"], flush=True)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


sys.exit(main())
