"""Compare the new fast pipeline against the old full-sweep ground truth.

Usage: python3 compare_fast.py ORACLE_DIR FAST_DIR   (e.g. /mnt/pool/fit4-sweep-eval/cluster .../cluster-fast)
Prints Markdown. Fields compared:
  oracle (old tooling): dump.meta.winner, winner XPSNR = the winner candidate's final xpsnr_yuv in dump.candidates
    (final=true, label == winner), dump.meta.wall_s, cpu_s.children_user + children_sys, dump.meta.limit.
  new: winner, xpsnr_yuv (re-measured on the delivered file), wall_s, cpu_s.children_total, delivered_bytes, limit.
Both wall figures are fit4 only (oracle: whole sweep in a 3-CPU pod with ncpu pinned to 16; new: layout sees the pod).
CPU-s is the rusage of children around the whole fit4 run in both, but the new one is the run only (the XPSNR
re-measure is excluded). Wall and CPU ratios are new/oracle.
"""
import json, statistics, sys
from pathlib import Path

LIMIT = 4194304


def load(d: Path, i: str):
    p = d / f"{i}.json"
    return json.load(open(p)) if p.exists() else None


def oracle_row(j):
    m = j["dump"]["meta"]
    win = m.get("winner")
    fin = [c for c in j["dump"]["candidates"] if c.get("final") and c["label"] == win]
    c = j["cpu_s"]
    return {"winner": win, "db": fin[0]["xpsnr_yuv"] if fin else None, "wall": m.get("wall_s", j.get("wall_s")),
            "cpu": c["children_user"] + c["children_sys"]}


def f(x, n=1):
    return "-" if x is None else f"{x:.{n}f}"


def main():
    od, fd = Path(sys.argv[1]), Path(sys.argv[2])
    ids = sorted({p.name.split(".")[0] for p in fd.glob("*.json")} | {p.name.split(".")[0] for p in fd.glob("*.error.json")})
    rows, fails, over = [], [], []
    print("| window | old winner | old dB | old s | old CPU-s | new winner | new dB | new s | new CPU-s | dB diff | CPU ratio | wall ratio | under limit | same winner |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i in ids:
        o, n = load(od, i), load(fd, i)
        if n is None:
            e = json.load(open(fd / f"{i}.error.json"))
            fails.append((i, e.get("error")))
            print(f"| {i} | | | | | FAILED: {e.get('error')} | | | | | | | | |")
            continue
        r = {"id": i, "n": n}
        ov = oracle_row(o) if o else None
        nw = {"winner": n.get("winner"), "db": n["xpsnr_yuv"], "wall": n["wall_s"], "cpu": n["cpu_s"]["children_total"]}
        under = n["delivered_bytes"] <= n.get("limit", LIMIT)
        if not under:
            over.append((i, n["delivered_bytes"]))
        if ov is None:
            print(f"| {i} | (no oracle) | | | | {nw['winner']} | {f(nw['db'],2)} | {f(nw['wall'])} | {f(nw['cpu'])} | | | | {'yes' if under else 'NO'} | |")
            continue
        r.update(d=nw["db"] - ov["db"], cr=nw["cpu"] / ov["cpu"], wr=nw["wall"] / ov["wall"], same=nw["winner"] == ov["winner"])
        rows.append(r)
        print(f"| {i} | {ov['winner']} | {f(ov['db'],2)} | {f(ov['wall'])} | {f(ov['cpu'])} | {nw['winner']} | {f(nw['db'],2)} | "
              f"{f(nw['wall'])} | {f(nw['cpu'])} | {r['d']:+.2f} | {r['cr']:.3f} | {r['wr']:.3f} | {'yes' if under else 'NO'} | {'yes' if r['same'] else 'no'} |")
    print()
    if rows:
        w03 = sum(1 for r in rows if r["d"] >= -0.3)
        print(f"- windows compared: {len(rows)}; new within 0.3 dB of oracle (or better): {w03}; same winner: {sum(r['same'] for r in rows)}")
        print("- worst 5 by dB loss: " + ", ".join(f"{r['id']} {r['d']:+.2f}" for r in sorted(rows, key=lambda r: r["d"])[:5]))
        print(f"- CPU-s ratio (new/old): median {statistics.median(r['cr'] for r in rows):.3f}; "
              f"total {sum(r['n']['cpu_s']['children_total'] for r in rows) / sum(oracle_row(load(od, r['id']))['cpu'] for r in rows):.3f}")
        print(f"- wall ratio (new/old): median {statistics.median(r['wr'] for r in rows):.3f}; "
              f"total {sum(r['n']['wall_s'] for r in rows) / sum(oracle_row(load(od, r['id']))['wall'] for r in rows):.3f}")
    print(f"- over-limit delivered files: {over or 'none'}")
    print(f"- failures: {fails or 'none'}")


main()
