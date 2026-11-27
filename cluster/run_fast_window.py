"""Run fit4's new default pipeline on one window and record a result JSON.

Usage: python3 run_fast_window.py ID [--windows windows.json] [--out DIR] [--scratch DIR] [--timeout S]
Runs `python -m fit4 <local copy> --start S --duration D --no-audio --work W` with defaults and PYTHONPATH=<repo>/src.
No CPU-count pin: fit4's layout reads the process's affinity and the cgroup cpu.max, so it sees the pod's real limit
(`available_cpus` is recorded in host.fit4_available_cpus). The delivered file's XPSNR is re-measured with
fit4.metrics.score against trim(probe(source), start, duration), at source size and frame rate.
Writes DIR/ID.json atomically (skips the window if it exists); a failure writes DIR/ID.error.json and exits 1.
Only JSON, the log and the delivered file are kept under DIR/ID/.
"""
import argparse, json, os, platform, re, resource, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def cgroup_cpu_s():
    try:
        for line in open("/sys/fs/cgroup/cpu.stat"):
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return None


def host_info() -> dict:
    from fit4.layout import available_cpus
    try:
        cpu_max = open("/sys/fs/cgroup/cpu.max").read().strip()
    except OSError:
        cpu_max = None
    try:
        model = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except (OSError, StopIteration):
        model = ""
    return {"node": platform.node(), "cpu_model": model, "os_cpu_count": os.cpu_count(),
            "sched_affinity": len(os.sched_getaffinity(0)), "cc_job_cpu": os.environ.get("CC_JOB_CPU"),
            "cgroup_cpu_max": cpu_max, "fit4_available_cpus": available_cpus(), "cpu_pin": None}


def parse_log(text: str) -> dict:
    d: dict = {}
    m = re.search(r"^chose (\S+): ([\d,]+) B", text, re.M)
    if m:
        d["winner"], d["chose_bytes"] = m[1], int(m[2].replace(",", ""))
    m = re.search(r"limit ([\d,]+) B", text)
    if m:
        d["limit"] = int(m[1].replace(",", ""))
    m = re.search(r"^probe: (\d+)s;", text, re.M)
    d["probe_s"] = int(m[1]) if m else None
    d["encodes"] = sum(int(x) for x in re.findall(r"\((\d+) encodes?\)", text))
    m = re.search(r"first passes: (\d+) run", text)
    d["first_passes_run"] = int(m[1]) if m else None
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--windows", type=Path, default=HERE / "windows.json")
    ap.add_argument("--out", type=Path, default=Path("/videos-out/cluster-fast2"))
    ap.add_argument("--scratch", type=Path, default=Path(os.environ.get("FIT4_SCRATCH", "/w/scratch")))
    ap.add_argument("--timeout", type=float, default=3 * 3600)
    a = ap.parse_args()
    w = {x["id"]: x for x in json.load(open(a.windows))}[a.id]
    a.out.mkdir(parents=True, exist_ok=True)
    final, err = a.out / f"{a.id}.json", a.out / f"{a.id}.error.json"
    if final.exists():
        print(a.id, "already done, skipping", flush=True)
        return 0
    a.scratch.mkdir(parents=True, exist_ok=True)
    work = a.scratch / a.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    rec: dict = {"window": w}
    t_all = time.time()
    try:
        rec["host"] = host_info()
        src = Path(w["path"])
        cache = a.scratch / ("src-" + src.name)  # one local copy per recording, reused by the next window
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
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        cmd = [sys.executable, "-m", "fit4", str(local), "--start", str(w["start"]), "--duration", str(w["duration"]),
               "--no-audio", "--work", str(work / "work")]
        cg0, ru0 = cgroup_cpu_s(), resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.time()
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=a.timeout)
        wall = time.time() - t0
        ru1, cg1 = resource.getrusage(resource.RUSAGE_CHILDREN), cgroup_cpu_s()
        rec.update(exit=r.returncode, wall_s=round(wall, 1),
                   cpu_s={"children_user": round(ru1.ru_utime - ru0.ru_utime, 1),
                          "children_sys": round(ru1.ru_stime - ru0.ru_stime, 1),
                          "children_total": round(ru1.ru_utime - ru0.ru_utime + ru1.ru_stime - ru0.ru_stime, 1),
                          "cgroup": None if cg0 is None or cg1 is None else round(cg1 - cg0, 1)},
                   log_tail=r.stderr.splitlines()[-40:], **parse_log(r.stderr))
        win_dir = a.out / a.id
        win_dir.mkdir(exist_ok=True)
        (win_dir / "log.txt").write_text(r.stderr[-200_000:])
        for n in ("results.json", "probe.json", "prune.json"):
            if (work / "work" / n).exists():
                rec[n.split(".")[0]] = json.load(open(work / "work" / n))
        delivered = sorted(work.glob("*.fit4.*"))
        if r.returncode != 0 or not delivered:
            raise RuntimeError(f"fit4 exit {r.returncode}, delivered {len(delivered)} files")
        d = delivered[0]
        rec["delivered_bytes"] = d.stat().st_size
        rec["delivered_file"] = d.name
        rec["under_limit"] = d.stat().st_size <= rec.get("limit", 4194304)
        # re-measure with the oracle's metric: source size and frame rate, against the trimmed source
        from fit4 import metrics, probe
        s = probe.trim(probe.probe(cache), w["start"], w["duration"])
        t = time.time()
        sc = metrics.score(s, d, work / "remeasure.xpsnr.txt")
        rec.update(xpsnr_yuv=round(sc.xpsnr_yuv, 3), xpsnr_y=round(sc.xpsnr_y, 3), ssim_y=sc.ssim_y,
                   remeasure_s=round(time.time() - t, 1), source_frames=len(sc.per_frame_y))
        shutil.copyfile(d, win_dir / d.name)
        rec["total_s"] = round(time.time() - t_all, 1)
        atomic_json(final, rec)
        err.unlink(missing_ok=True)
        print(a.id, "ok", rec["wall_s"], "s", rec["xpsnr_yuv"], "dB", flush=True)
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
