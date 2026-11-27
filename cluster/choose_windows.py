"""Pick the cluster ground-truth windows deterministically (seed below).

Usage: python3 choose_windows.py CORPUS.json OLD_WINDOWS.json > windows.json
CORPUS.json is an ffprobe inventory of the recordings folder (file, size, dur, w, h, fps, ...);
OLD_WINDOWS.json holds the eight earlier windows, which are carried over (flag "rerun") and never overlapped.
"""
import collections, json, random, re, sys

SEED = 20261126
N_NEW = 40
MAX_PER_FILE = 3
EDGE = 5            # keep windows this many seconds away from either end of the recording
MAX_BYTES = 4 << 30  # sources are copied to local disk whole, so keep them modest
RERUN = ("w3-ardour", "w7-flat")  # the two shortest earlier windows; the others are kept for completeness
# cut-downs, duplicates and files that are the same footage as another entry
SKIP = ("sync-conflict", ".fit4", "[", " small ", "720p", " cut", "-audio", "ardour ambisonics 2021", "sparseposer.mp4",
        "vrchat-midi-osc-2 2022", "sneeditorium.mp4", "SOS Sports")


def group(f):
    """Dated captures group by month; named recordings by their first word (letters only)."""
    if re.match(r"\d{4}-\d\d", f):
        return "dated " + f[:7]
    m = re.match(r"[A-Za-z]+", f)
    return (m.group(0).lower() if m else "misc")


def year(f):
    m = re.search(r"(20\d\d)", f)
    return m.group(1) if m else "?"


def main():
    rows = json.load(open(sys.argv[1]))
    old = json.load(open(sys.argv[2]))
    rng = random.Random(SEED)
    lim = 4 << 20
    fin = [x for x in rows if "error" not in x and x["vcodec"] not in ("png", "mjpeg") and ".fit4" not in x["file"]
           and x["size"] <= lim and x["dur"] < 120]
    pool = sorted(round(x["dur"]) for x in fin if 10 <= x["dur"] <= 45)  # finished-clip durations, clipped to 10-45 s
    old_files = {w["path"].rsplit("/", 1)[-1] for w in old}
    # long or large recordings only (as in the corpus summary), minus cut-downs and duplicates
    cands = [x for x in rows if "error" not in x and x["vcodec"] not in ("png", "mjpeg") and x["dur"] >= 22
             and not (x["size"] <= lim and x["dur"] < 120) and x["size"] <= MAX_BYTES and x["file"] not in old_files
             and not any(s in x["file"] for s in SKIP)]
    cands.sort(key=lambda x: x["file"])
    by_group = collections.defaultdict(list)
    for x in cands:
        by_group[group(x["file"])].append(x)
    picks, per_file, yrs = [], collections.Counter(), collections.Counter()
    while len(picks) < N_NEW:
        progressed = False
        gs = sorted(by_group)
        rng.shuffle(gs)
        for g in gs:
            if len(picks) >= N_NEW:
                break
            # use every recording once before any gets a second window
            least = min(per_file[x["file"]] for x in cands if per_file[x["file"]] < MAX_PER_FILE)
            opts = [x for x in by_group[g] if per_file[x["file"]] == least]
            if not opts:
                continue
            low = min(yrs[year(x["file"])] for x in opts)
            x = rng.choice([x for x in opts if yrs[year(x["file"])] == low])
            # duration from the finished-clip distribution, clipped to what the recording can hold
            d = rng.choice(pool)
            d = int(min(d, x["dur"] - 2 * EDGE))
            if d < 10:
                per_file[x["file"]] = MAX_PER_FILE
                continue
            taken = [(p["start"], p["start"] + p["duration"]) for p in picks if p["file"] == x["file"]]
            for _ in range(50):
                s = rng.randint(EDGE, int(x["dur"] - EDGE - d))
                if all(s >= b + 1 or s + d <= a - 1 for a, b in taken):
                    break
            else:
                per_file[x["file"]] = MAX_PER_FILE
                continue
            per_file[x["file"]] += 1
            yrs[year(x["file"])] += 1
            picks.append({"file": x["file"], "start": s, "duration": d, "group": g, "src": x})
            progressed = True
        if not progressed:
            break
    picks.sort(key=lambda p: (p["file"], p["start"]))
    out = []
    for w in old:
        out.append({**w, "path": "/videos/" + w["path"].rsplit("/", 1)[-1], "rerun": True,
                    "smoke": w["id"] in RERUN})
    for i, p in enumerate(picks, 1):
        x = p["src"]
        out.append({"id": f"c{i:02d}", "path": "/videos/" + p["file"], "start": p["start"], "duration": p["duration"],
                    "split": "cluster", "kind": p["group"],
                    "description": f"{x['w']}x{x['h']} {x['fps']:g}fps {x['vcodec']} recording, {x['dur']:.0f} s long"})
    json.dump(out, sys.stdout, indent=1)
    print()


main()
