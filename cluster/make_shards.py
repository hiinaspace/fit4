"""Split windows into N shards of similar estimated cost: python3 make_shards.py N [out_dir] (default cluster/shards)."""
import json, sys
from pathlib import Path

here = Path(__file__).resolve().parent
n = int(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "shards"
corpus = json.load(open(here / "windows.json"))
ids = sys.argv[3].split(",") if len(sys.argv) > 3 else [w["id"] for w in corpus]
by = {w["id"]: w for w in corpus}
src = {}
def cost(w):  # pixels * fps * seconds; the sizes are in the description ("WxH fps")
    d = w.get("description", "")
    import re
    m = re.match(r"(\d+)x(\d+) (\d+)fps", d)
    if m: px = int(m[1]) * int(m[2]); fps = int(m[3])
    else: px, fps = 1920 * 1080, 60
    return min(px, 1920 * 1080 * 1.5) * fps * w["duration"]
shards = [[] for _ in range(n)]; load = [0.0] * n
for i in sorted(ids, key=lambda i: -cost(by[i])):
    k = load.index(min(load)); shards[k].append(i); load[k] += cost(by[i])
out.mkdir(exist_ok=True)
for k, s in enumerate(shards):
    s.sort(key=lambda i: by[i]["path"])  # same recording back to back: one copy off the share
    (out / f"shard{k}.txt").write_text("\n".join(s) + "\n")
    print(f"shard{k}: {len(s)} windows, load {load[k]/sum(load)*n:.2f}")
