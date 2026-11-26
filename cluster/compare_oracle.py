"""Compare two per-window oracle JSONs (e.g. a local run and a cluster run): python3 compare_oracle.py A.json B.json"""
import json, sys

a, b = (json.load(open(p)) for p in sys.argv[1:3])
ca = {(c["label"], c["final"]): c for c in a["dump"]["candidates"]}
cb = {(c["label"], c["final"]): c for c in b["dump"]["candidates"]}
print(f"winner: {a['dump']['meta'].get('winner')} vs {b['dump']['meta'].get('winner')}")
print(f"wall: {a['dump']['meta']['wall_s']} s vs {b['dump']['meta']['wall_s']} s")
same = 0
for k in sorted(set(ca) | set(cb), key=lambda k: (k[1], k[0])):
    x, y = ca.get(k), cb.get(k)
    if not x or not y:
        print(("final " if k[1] else "screen"), k[0], "only in", "A" if x else "B")
        continue
    ts = [t["size"] for t in x["tries"]], [t["size"] for t in y["tries"]]
    ok = x["size"] == y["size"] and x["xpsnr_yuv"] == y["xpsnr_yuv"] and x["ssim_y"] == y["ssim_y"]
    same += ok
    print(f"{'final ' if k[1] else 'screen'} {k[0]:<14} size {x['size']:>8} {y['size']:>8} ({y['size']-x['size']:+d}) "
          f"xpsnr {x['xpsnr_yuv']:.3f} {y['xpsnr_yuv']:.3f} ({y['xpsnr_yuv']-x['xpsnr_yuv']:+.3f}) "
          f"ssim {x['ssim_y']:.5f} {y['ssim_y']:.5f} tries {len(x['tries'])}/{len(y['tries'])} "
          f"secs {x['seconds']:.0f}/{y['seconds']:.0f}{'  ==' if ok else ''}")
print(f"identical size+xpsnr+ssim: {same} of {len(set(ca) & set(cb))}")
