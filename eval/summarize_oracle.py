"""Summarise oracle/*.json into oracle-summary.md (tables for README)."""
import json, glob, os
from pathlib import Path
E = Path(__file__).resolve().parent
order = [w["id"] for w in json.load(open(E / "windows.json"))]
recs = {}
for p in glob.glob(str(E / "oracle/w*.json")):
    r = json.load(open(p)); recs[r["window"]["id"]] = r
L = []
P = L.append
P("| window | split | dur s | wall s (screen/final) | winner | XPSNR dB | cands (infeasible) | job-s in infeasible | job-s in retries | job-s total |")
P("|---|---|---|---|---|---|---|---|---|---|")
tot = dict(wall=0, inf=0, ret=0, job=0, cands=0, infc=0)
detail = []
for wid in order:
    r = recs.get(wid)
    if not r: P(f"| {wid} | | | MISSING | | | | | | |"); continue
    d = r.get("dump")
    w = r["window"]
    if not d or r["exit"] != 0:
        P(f"| {wid} | {w['split']} | {w['duration']} | FAILED exit {r['exit']} | | | | | | |"); continue
    m = d["meta"]; cs = d["candidates"]
    scr = [c for c in cs if not c["final"]]; fin = [c for c in cs if c["final"]]
    job = sum(c["seconds"] for c in cs)
    inf = sum(c["seconds"] for c in cs if c["failed_to_fit"])
    ret = sum(t["seconds"] for c in cs if not c["failed_to_fit"] for t in c["tries"][1:])
    ninf = sum(c["failed_to_fit"] for c in scr)
    win = [c for c in fin if c["label"] == m["winner"]][0]
    P(f"| {wid} | {w['split']} | {w['duration']} | {r['locked_wall_s']:.0f} ({m['screen_wall_s']:.0f}/{m['final_wall_s']:.0f}) | {m['winner']} | {win['xpsnr_yuv']:.2f} | {len(scr)} ({ninf}) | {inf:.0f} ({inf/job:.0%}) | {ret:.0f} ({ret/job:.0%}) | {job:.0f} |")
    tot["vp9scr"] = tot.get("vp9scr", 0) + sum(c["seconds"] for c in scr if c["codec"] == "vp9")
    tot["scr"] = tot.get("scr", 0) + sum(c["seconds"] for c in scr)
    tot["fin"] = tot.get("fin", 0) + sum(c["seconds"] for c in fin)
    tot["finvp9"] = tot.get("finvp9", 0) + sum(c["seconds"] for c in fin if c["codec"] == "vp9")
    tot["p1"] = tot.get("p1", 0) + sum(t["pass1_s"] or 0 for c in cs for t in c["tries"] if c["codec"] == "vp9")
    tot["p2"] = tot.get("p2", 0) + sum(t["pass2_s"] or 0 for c in cs for t in c["tries"] if c["codec"] == "vp9")
    tot["score"] = tot.get("score", 0) + sum(c.get("score_seconds", 0) for c in cs)
    tot["n3"] = tot.get("n3", 0) + sum(len(c["tries"]) == 3 for c in scr if not c["failed_to_fit"])
    tot["nret"] = tot.get("nret", 0) + sum(len(c["tries"]) > 1 for c in scr if not c["failed_to_fit"])
    tot["nfeas"] = tot.get("nfeas", 0) + sum(not c["failed_to_fit"] for c in scr)
    tot["wall"] += r["locked_wall_s"]; tot["inf"] += inf; tot["ret"] += ret; tot["job"] += job; tot["cands"] += len(scr); tot["infc"] += ninf
    # per-candidate table
    detail.append(f"\n#### {wid} ({w['kind']}; {w['split']})\n")
    top = sorted((c for c in scr if not c["failed_to_fit"]), key=lambda c: -c["xpsnr_yuv"])[:5]
    detail.append("Top screening: " + "; ".join(f"{c['label']} {c['xpsnr_yuv']:.2f}" for c in top) + f". Finals: " + "; ".join(f"{c['label']} {c['xpsnr_yuv']:.2f}" for c in fin) + ".\n")
    detail.append("| stage | candidate | tries (target kbps -> MB, s) | final MB | XPSNR | SSIM | fit? |\n|---|---|---|---|---|---|---|")
    for c in sorted(cs, key=lambda c: (c["final"], c["codec"], -c["height"], -c["fps"])):
        tr = "; ".join(f"{t['target_kbps']:.0f}->{t['size']/1e6:.2f}{'' if t['fits'] else '!'},{t['seconds']:.0f}s" for t in c["tries"])
        if c["failed_to_fit"]:
            detail.append(f"| {'final' if c['final'] else 'screen'} | {c['label']} | {tr} | - | - | - | NEVER FIT |")
        else:
            detail.append(f"| {'final' if c['final'] else 'screen'} | {c['label']} | {tr} | {c['size']/1e6:.2f} | {c['xpsnr_yuv']:.2f} | {c['ssim_y']:.4f} | yes |")
P("")
P(f"Totals over finished windows: locked wall {tot['wall']/60:.1f} min; screening candidates {tot['cands']}, never fit {tot['infc']}; "
  f"job-seconds {tot['job']:.0f}, of which infeasible candidates {tot['inf']/max(tot['job'],1):.0%}, size-correction retries on feasible ones {tot['ret']/max(tot['job'],1):.0%}.")
L.append(f"\nVP9 share of screening job-seconds: {tot['vp9scr']/tot['scr']:.0%}. Final stage job-seconds: {tot['fin']:.0f} ({tot['finvp9']:.0f} of them VP9, which re-runs the screening settings). "
  f"VP9 pass 1 vs pass 2 job-seconds: {tot['p1']:.0f} vs {tot['p2']:.0f} (pass 1 = {tot['p1']/(tot['p1']+tot['p2']):.0%}). XPSNR/SSIM scoring job-seconds: {tot['score']:.0f}. "
  f"Feasible screening candidates that needed a retry: {tot['nret']} of {tot['nfeas']} ({tot['n3']} used all three tries).")
(E / "oracle-summary.md").write_text("\n".join(L + detail) + "\n")
print("\n".join(L))
