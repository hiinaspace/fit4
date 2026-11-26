"""Run the new pipeline on eval windows, one sweep per lock hold. Usage:
   run_new.py OUTDIR WINDOW_IDS|tune|heldout [-- extra fit4 args]
Writes OUTDIR/<id>.json (timing, exit, stderr, results/probe/prune) and OUTDIR/<id>.log."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
LOCAL = Path("/tmp/claude-1000/sc/local"); LOCAL.mkdir(parents=True, exist_ok=True)
pool_out = Path("/mnt/pool/videos/.fit4-sweep-eval")
wins = {w["id"]: w for w in json.load(open(EVAL / "windows.json"))}

def main():
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--"); extra = argv[i + 1:]; argv = argv[:i]
    out = Path(argv[0]); out.mkdir(parents=True, exist_ok=True)
    sel = argv[1:]
    ids = []
    for s in sel:
        if s in ("tune", "heldout"):
            ids += [w for w, d in wins.items() if d["split"] == s]
        else:
            ids.append(s)
    for wid in ids:
        w = wins[wid]
        dst = out / f"{wid}.json"
        if dst.exists():
            print(wid, "exists; skip", flush=True); continue
        work = LOCAL / f"{out.name}-{wid}"
        shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
        src_local = LOCAL / f"{wid}.src.mp4"
        if not src_local.exists():
            for attempt in range(5):
                try:
                    shutil.copyfile(w["path"], str(src_local) + ".part"); os.replace(str(src_local) + ".part", src_local); break
                except OSError as e:
                    print("stage retry", wid, e, flush=True); time.sleep(20)
        marker = work / "started.txt"
        fit = " ".join(f"'{a}'" for a in extra)
        inner = (f"date +%s.%N > '{marker}'; exec uv run --project '{ROOT}' --frozen fit4 '{src_local}' "
                 f"--start {w['start']} --duration {w['duration']} --no-audio --work '{work}' {fit}")
        t_wait0 = time.time()
        while True:
            gate = "i=0; until local-job --check >/dev/null 2>&1 || [ $i -ge 60 ]; do sleep 10; i=$((i+1)); done; "
            r = subprocess.run(["flock", "/tmp/fit4-heavy.lock", "sh", "-c", gate + 'exec local-job -- sh -c "$0"', inner],
                               capture_output=True, text=True)
            if r.returncode == 75 and not marker.exists():
                print(wid, "local-job refused; waiting", flush=True); time.sleep(240); continue
            break
        t_end = time.time()
        t_start = float(marker.read_text()) if marker.exists() else t_end
        rec = {"window": w, "args": extra, "exit": r.returncode, "locked_wall_s": round(t_end - t_start, 1),
               "waited_for_lock_s": round(t_start - t_wait0, 1), "stdout": r.stdout.strip()}
        for n in ("results.json", "probe.json", "prune.json"):
            if (work / n).exists(): rec[n.split(".")[0]] = json.load(open(work / n))
        (out / f"{wid}.log").write_text(r.stderr)
        # delivered file size
        outs = list(src_local.parent.glob(f"{wid}.src.fit4.*"))
        if outs:
            rec["delivered_bytes"] = outs[0].stat().st_size
            for o in outs: o.unlink()
        json.dump(rec, open(dst, "w"), indent=1)
        print(wid, "exit", r.returncode, "locked", rec["locked_wall_s"], flush=True)
        shutil.rmtree(work, ignore_errors=True)
    # sources kept for reuse across runs

main()
