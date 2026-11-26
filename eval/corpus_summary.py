import json, re, os, collections as C
D = "/mnt/pool/videos"
rows = json.load(open("corpus.json"))
good = [x for x in rows if "error" not in x and x["vcodec"] not in ("png", "mjpeg") and ".fit4" not in x["file"]]
unread = [x for x in rows if x.get("error") and x["file"].endswith((".mp4", ".webm", ".mkv"))]
LIM = 4 * 2**20
fin = [x for x in good if x["size"] <= LIM and x["dur"] < 120]
lon = [x for x in good if x not in fin and "sync-conflict" not in x["file"]]
def q(v, nd=1):
    v = sorted(v); n = len(v)
    return [round(v[int(p * (n - 1))], nd) for p in (0, .1, .25, .5, .75, .9, 1)]
out = []
P = out.append
P(f"Files in folder: {len(rows)} entries; ffprobe-readable videos: {len(good)}; unreadable (mode 0600 on the NFS share, permission denied for this agent): {len(unread)} mp4/webm/mkv.")
P(f"Finished clips (readable, <= 4 MiB, < 120 s): {len(fin)}; long or large recordings: {len(lon)}.\n")
P("| metric | min | p10 | p25 | median | p75 | p90 | max |\n|---|---|---|---|---|---|---|---|")
for name, vals, nd in (("duration s", [x["dur"] for x in fin], 1), ("size MiB", [x["size"] / 2**20 for x in fin], 2),
                       ("total kbps", [x["kbps"] for x in fin], 0), ("height px", [x["h"] for x in fin], 0)):
    P(f"| {name} | " + " | ".join(str(a) for a in q(vals, nd)) + " |")
fpsb = C.Counter("60" if x["fps"] > 45 else "30" if x["fps"] > 27 else "25" if x["fps"] > 20 else "other" for x in fin)
P("")
P(f"- fps: {dict(fpsb)}")
P(f"- video codec: {dict(C.Counter(x['vcodec'] for x in fin))}")
P(f"- audio present: {sum(x['audio'] for x in fin)} of {len(fin)}")
P(f"- resolution: " + ", ".join(f"{w}x{h} x{n}" for (w, h), n in C.Counter((x['w'], x['h']) for x in fin).most_common(8)))
P(f"- share under 15 s: {sum(x['dur']<15 for x in fin)/len(fin):.0%}; 15-30 s: {sum(15<=x['dur']<30 for x in fin)/len(fin):.0%}; over 30 s: {sum(x['dur']>=30 for x in fin)/len(fin):.0%}")
P(f"- mean size {sum(x['size'] for x in fin)/len(fin)/2**20:.2f} MiB; share within 0.5 MiB of the cap: {sum(x['size']>LIM-0.5*2**20 for x in fin)/len(fin):.0%}")
# unreadable: durations from the [a-b] range in their names
def secs(t):
    p = [float(z) for z in t.split(".")[:-1]] ; ms = float(t.split(".")[-1]) / 1000
    s = 0
    for z in p: s = s * 60 + z
    return s + ms
nd = []
for x in unread:
    m = re.search(r"\[(\d+(?:\.\d+)*)-(\d+(?:\.\d+)*)\]", x["file"])
    if m:
        try: nd.append(secs(m.group(2)) - secs(m.group(1)))
        except Exception: pass
if nd: P(f"- unreadable named clips: {len(nd)} with a time range in the name, durations p10/median/p90 = {q(nd)[1]}/{q(nd)[3]}/{q(nd)[5]} s (consistent with the readable set)")
P("")
P("Long recordings (readable, >= 120 s or > 4 MiB, excluding cut-downs and duplicates), grouped:\n")
def grp(f):
    l = f.lower()
    for k, g in (("replay 2024", "VR mech cockpit replays (2024-12-13)"), ("dragon fist", "dragon fist VR fighter"), ("2026-11-25", "Quake / 2026-11-25 sessions"),
                 ("2026-09-2", "Mushi Lantern dev captures (Sept 2026)"), ("mushi", "Mushi Lantern dev captures (Sept 2026)"), ("2026-09-1", "1908x2162 VR dev captures (Sept 2026)"),
                 ("osc-grimoire", "osc-grimoire Unity"), ("sneeditorium", "sneeditorium"), ("electronauts", "Electronauts VR"), ("demeo", "Demeo VR tabletop"),
                 ("deemo", "Demeo VR tabletop"), ("neos", "NeosVR / VRChat social"), ("vrg", "NeosVR / VRChat social"), ("vrchat", "VRChat"), ("2026-05-18", "VRChat street 2026"),
                 ("bikeheadvr", "bikeheadvr"), ("oversaber", "oversaber"), ("ardour", "Ardour DAW screencast"), ("beanstalker", "beanstalker"), ("multibox", "multibox VRChat world")):
        if k in l: return g
    return "other single recordings"
G = C.defaultdict(list)
for x in lon: G[grp(x["file"])].append(x)
P("| group | files | total min | resolution / fps | typical size |\n|---|---|---|---|---|")
for g, xs in sorted(G.items(), key=lambda kv: -sum(x['dur'] for x in kv[1])):
    res = ", ".join(sorted({f"{x['w']}x{x['h']}@{round(x['fps'])}" for x in xs}))
    P(f"| {g} | {len(xs)} | {sum(x['dur'] for x in xs)/60:.1f} | {res} | {sum(x['size'] for x in xs)/len(xs)/2**20:.0f} MiB avg |")
open("corpus-summary.md", "w").write("\n".join(out) + "\n")
print("\n".join(out))
