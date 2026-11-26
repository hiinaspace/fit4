"""Run the baseline sweep for each eval window, one sweep per lock hold, and write oracle/<id>.json."""
import json, os, subprocess, sys, time
from pathlib import Path

EVAL = Path(__file__).resolve().parent
OUT = Path("/mnt/pool/videos/.fit4-sweep-eval/baseline")
LOCAL = Path("/tmp/claude-1000/sc/local"); LOCAL.mkdir(parents=True, exist_ok=True)
import shutil
BASE = Path.home() / "code/fit4-wt/baseline"
BUDGET_S = 150 * 60
ORDER = ["w1-mechvr", "w2-dragonfist", "w3-ardour", "w4-quake", "w5-mushdark", "w6-ui", "w7-flat", "w8-street"]
wins = {w["id"]: w for w in json.load(open(EVAL / "windows.json"))}
used = float(sum(json.load(open(p))['locked_wall_s'] for p in __import__('glob').glob(str(EVAL/'oracle_sshfs/*.json'))))
done = []
skipped = []
for wid in ORDER:
    w = wins[wid]
    dst = EVAL / "oracle" / f"{wid}.json"
    if dst.exists():
        d = json.load(open(dst)); used += d["locked_wall_s"]; done.append(wid); continue
    avg = used / len(done) if done else 15 * 60
    if used + 0.7 * avg > BUDGET_S:
        skipped.append(wid); continue
    # /mnt/pool is an sshfs mount that fails transiently and adds network I/O to every encode, so the source
    # is staged to local disk (unlocked, I/O only) and work files live in /tmp; small results are copied back.
    work = LOCAL / wid; work.mkdir(parents=True, exist_ok=True)
    for old in work.iterdir(): old.unlink()
    src_local = LOCAL / f"{wid}.src.mp4"
    if not src_local.exists():
        for attempt in range(5):
            try:
                shutil.copyfile(w["path"], str(src_local) + ".part"); os.replace(str(src_local) + ".part", src_local); break
            except OSError as e:
                print("stage retry", wid, e, flush=True); time.sleep(20)
    dump = work / "dump.json"; marker = work / "started.txt"
    inner = (
        f"date +%s.%N > '{marker}'; exec env FIT4_DUMP='{dump}' uv run --project '{BASE}' --frozen fit4 '{src_local}' "
        f"--start {w['start']} --duration {w['duration']} --no-audio --work '{work}'"
    )
    t_wait0 = time.time()
    while True:
        # Hold the lock while waiting for the load average to settle (local-job refuses right after another
        # job ends, and flock is not fair, so releasing between retries starves this driver).
        gate = "i=0; until local-job --check >/dev/null 2>&1 || [ $i -ge 60 ]; do sleep 10; i=$((i+1)); done; "
        r = subprocess.run(["flock", "/tmp/fit4-heavy.lock", "sh", "-c", gate + 'exec local-job -- sh -c "$0"', inner],
                           capture_output=True, text=True)
        if r.returncode == 75 and not marker.exists():
            print(wid, "local-job refused; waiting", flush=True); time.sleep(240); continue
        break
    t_end = time.time()
    t_start = float(marker.read_text()) if marker.exists() else t_end
    locked = t_end - t_start
    used += locked
    rec = {"window": w, "exit": r.returncode, "locked_wall_s": round(locked, 1),
           "waited_for_lock_s": round(t_start - t_wait0, 1), "source": "local copy of the pool file (staged before the lock)", "stderr_tail": r.stderr[-3000:].splitlines()[-25:]}
    if dump.exists():
        rec["dump"] = json.load(open(dump))
    json.dump(rec, open(dst, "w"), indent=1)
    try:
        pd = OUT / wid; pd.mkdir(parents=True, exist_ok=True)
        for n in ("dump.json", "results.json"):
            if (work / n).exists(): shutil.copyfile(work / n, pd / n)
    except OSError as e:
        print("copy-back to pool failed", e, flush=True)
    src_local.unlink(missing_ok=True)
    done.append(wid)
    print(wid, "exit", r.returncode, "locked", round(locked), "total used", round(used), flush=True)
print("DONE", done, "SKIPPED", skipped, "used_s", round(used), flush=True)
json.dump({"done": done, "skipped": skipped, "used_s": used}, open(EVAL / "oracle" / "_driver.json", "w"))
