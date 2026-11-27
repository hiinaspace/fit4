"""Table of a new-pipeline variant against the oracle. Usage: summarize_new.py VARIANT [split]  (default split tune)."""
import json, sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
LIMIT = 4194304
var = sys.argv[1]
split = sys.argv[2] if len(sys.argv) > 2 else "tune"
wins = [w for w in json.load(open(EVAL / "windows.json")) if w["split"] == split]
print(f"| window | oracle wall s | new wall s | ratio | oracle winner | oracle dB | new winner | new dB | gap dB | delivered bytes | under limit |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
tot_o = tot_n = 0.0
worst = 0.0
for w in wins:
    p = EVAL / "new" / var / f"{w['id']}.json"
    if not p.exists():
        print(f"| {w['id']} | (not run) |"); continue
    n = json.load(open(p))
    o = json.load(open(EVAL / "oracle" / f"{w['id']}.json"))
    d = o["dump"]
    fin = [c for c in d["candidates"] if c["final"] and not c["failed_to_fit"]]
    ow = max(fin, key=lambda c: c["xpsnr_yuv"])
    rows = [r for r in n.get("results", []) if r["stage"] == "final"]
    if not rows:
        print(f"| {w['id']} | {o['locked_wall_s']:.0f} | {n['locked_wall_s']:.0f} | | {ow['label']} | {ow['xpsnr_yuv']:.2f} | FAILED exit {n['exit']} |"); continue
    nw = max(rows, key=lambda r: r["xpsnr_yuv"])
    gap = ow["xpsnr_yuv"] - nw["xpsnr_yuv"]
    tot_o += o["locked_wall_s"]; tot_n += n["locked_wall_s"]; worst = max(worst, gap)
    print(f"| {w['id']} | {o['locked_wall_s']:.0f} | {n['locked_wall_s']:.0f} | {100*n['locked_wall_s']/o['locked_wall_s']:.0f}% | {ow['label']} | {ow['xpsnr_yuv']:.2f} | {nw['label']} | {nw['xpsnr_yuv']:.2f} | {gap:+.2f} | {n.get('delivered_bytes', nw['bytes']):,} | {'yes' if n.get('delivered_bytes', nw['bytes']) <= LIMIT else 'NO'} |")
if tot_o:
    print(f"| total | {tot_o:.0f} | {tot_n:.0f} | {100*tot_n/tot_o:.0f}% | | | | | worst {worst:+.2f} | | |")
