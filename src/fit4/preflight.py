"""Pre-flight probe: predict quality at the size limit before the expensive sweep.

Two cheap measurements stand in for the full screening grid:

1. A cost timeline: one fast fixed-quality x264 encode of the whole range at
   low resolution. Bits per second at constant quality is a content-complexity
   curve, so it says which stretches are expensive and scales predictions to
   any sub-window (a trim or a split).
2. A sample reel: a few short slices spread over the range, encoded per
   candidate (codec x height x fps) at several CRFs and scored with XPSNR the
   same way the sweep scores. CRF holds quality roughly constant across
   content, so each (CRF, XPSNR) point says "this quality costs this many bits
   per frame on reel-like content". Scaling by the timeline turns that into a
   bitrate for any window, and interpolating in log-bitrate gives a predicted
   XPSNR at the window's budget.

Keyframe packets are counted separately: the reel has a cut (and so a
keyframe) every slice, while a real encode uses ~20 s GOPs.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

from . import ff
from .encode import AUDIO_KBPS, OVERHEAD, Candidate, codec_args, norm_filter
from .metrics import score
from .probe import Source, probe

CRFS = {"x264": (22, 30, 38, 46), "vp9": (30, 42, 54, 63)}
VP9_FLOOR = 2.0
GOP_SECONDS = 20  # matches encode.codec_args
TIMELINE_HEIGHT = 540
TIMELINE_CRF = 30


@dataclass
class Point:
    crf: int
    key_bytes: float  # mean keyframe packet size
    inter_bytes: float  # mean non-keyframe packet size
    xpsnr: float
    ssim: float


@dataclass
class CandCurve:
    codec: str
    height: int
    fps: float
    points: list[Point] = field(default_factory=list)

    def cand(self) -> Candidate:
        return Candidate(self.codec, self.height, Fraction(self.fps).limit_denominator(1001))

    def label(self) -> str:
        return self.cand().label()


@dataclass
class Probe:
    start: float
    duration: float
    src_fps: float
    timeline: list[float]  # mean inter-frame bytes per second of source, at fixed CRF, by second
    reel_complexity: float  # same measure averaged over the reel's slices
    slices: list[tuple[float, float]]
    curves: list[CandCurve]

    # -- predictions -------------------------------------------------------
    def complexity(self, a: float, b: float) -> float:
        """Mean timeline cost over [a, b) seconds (absolute source time), relative to the reel."""
        i0 = max(0, math.floor(a - self.start))
        i1 = min(len(self.timeline), max(i0 + 1, math.ceil(b - self.start)))
        seg = self.timeline[i0:i1]
        return (sum(seg) / len(seg)) / self.reel_complexity

    def kbps_at(self, curve: CandCurve, p: Point, a: float, b: float) -> float:
        d = b - a
        frames = d * curve.fps
        keys = max(1, math.ceil(d / GOP_SECONDS))
        c = self.complexity(a, b)
        total = keys * p.key_bytes * c + (frames - keys) * p.inter_bytes * c
        return total * 8 / d / 1000

    def predict(self, curve: CandCurve, a: float, b: float, video_kbps: float) -> float | None:
        """Predicted XPSNR (YUV) for this candidate over [a, b) at video_kbps; None if it can't get that low."""
        pts = sorted(((self.kbps_at(curve, p, a, b), p.xpsnr) for p in curve.points), key=lambda t: t[0])
        # VP9's top CRF is its quantizer ceiling, and its 2-pass VBR mode stalls
        # well above that: on a 51 s Quake clip every VP9 candidate whose budget
        # was under ~1.6x its CRF-63 bitrate never got under the limit, and
        # every one above it did (2026-11-25). x264 has no such floor.
        if curve.codec == "vp9" and video_kbps < pts[0][0] * VP9_FLOOR:
            return None
        xs = [math.log(k) for k, _ in pts]
        ys = [q for _, q in pts]
        x = math.log(video_kbps)
        if x <= xs[0]:
            i = 0
        elif x >= xs[-1]:
            i = len(xs) - 2
        else:
            i = max(j for j in range(len(xs) - 1) if xs[j] <= x)
        slope = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) if xs[i + 1] != xs[i] else 0.0
        return ys[i] + slope * (x - xs[i])

    def best(self, a: float, b: float, limit: int, audio_codec_kbps: dict[str, float] | None = None):
        """(predicted XPSNR, curve, video kbps) of the best candidate for window [a, b)."""
        out = []
        for c in self.curves:
            kb = video_budget(limit, b - a, (audio_codec_kbps or {}).get(c.codec, 0.0))
            q = self.predict(c, a, b, kb) if kb > 0 else None
            if q is not None:
                out.append((q, c, kb))
        return max(out, key=lambda t: t[0]) if out else None

    def fits_for(self, a: float, limit: int, bar: float, audio=None, step: float = 0.5) -> float:
        """Longest duration from a whose best predicted XPSNR is >= bar (0 if none)."""
        end = self.start + self.duration
        best_d = 0.0
        d = step * 2
        while a + d <= end + 1e-6:
            r = self.best(a, a + d, limit, audio)
            if r and r[0] >= bar:
                best_d = d
            d += step
        return best_d

    def best_split(self, limit: int, parts: int = 2, audio=None, step: float = 0.5):
        """Cut points maximizing the worst part's predicted XPSNR (parts 2 or 3)."""
        s, e = self.start, self.start + self.duration
        grid = [s + i * step for i in range(1, int(self.duration / step))]
        best = None
        if parts == 2:
            for t in grid:
                r1, r2 = self.best(s, t, limit, audio), self.best(t, e, limit, audio)
                if r1 and r2:
                    worst = min(r1[0], r2[0])
                    if best is None or worst > best[0]:
                        best = (worst, [t], [r1, r2])
        else:
            for i, t1 in enumerate(grid):
                for t2 in grid[i + 2 :]:
                    rs = [
                        self.best(s, t1, limit, audio),
                        self.best(t1, t2, limit, audio),
                        self.best(t2, e, limit, audio),
                    ]
                    if all(rs):
                        worst = min(r[0] for r in rs)
                        if best is None or worst > best[0]:
                            best = (worst, [t1, t2], rs)
        return best

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_json(cls, text: str) -> Probe:
        d = json.loads(text)
        d["slices"] = [tuple(x) for x in d["slices"]]
        d["curves"] = [
            CandCurve(c["codec"], c["height"], c["fps"], [Point(**p) for p in c["points"]]) for c in d["curves"]
        ]
        return cls(**d)


def video_budget(limit: int, duration: float, audio_kbps: float = 0.0, margin: float = 0.985) -> float:
    """Video kbps the sweep would aim for (same arithmetic as encode.budget_kbps)."""
    bits = limit * 8 * margin / (1 + OVERHEAD) - audio_kbps * 1000 * duration
    return bits / duration / 1000


# -- measurement ---------------------------------------------------------------


def packets(path: Path) -> list[tuple[int, bool, float]]:
    """(size, is_key, pts_seconds) for each video packet."""
    out = ff.run(
        [*ff.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=size,flags,pts_time",
         "-of", "csv=p=0", str(path)],
        capture=True,
    ).stdout  # fmt: skip
    rows = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[1]), "K" in parts[2], float(parts[0])))
        except ValueError:
            continue
    return rows


def key_inter(path: Path) -> tuple[float, float]:
    pk = packets(path)
    keys = [s for s, k, _ in pk if k]
    inter = [s for s, k, _ in pk if not k]
    return (sum(keys) / len(keys) if keys else 0.0, sum(inter) / len(inter) if inter else 0.0)


def timeline(src: Source, work: Path, threads: int) -> list[float]:
    """Mean inter-frame bytes per second of content, at fixed CRF and low resolution."""
    out = work / "timeline.mkv"
    h = min(TIMELINE_HEIGHT, src.height)
    ff.run(
        [*ff.FFMPEG, "-v", "error", "-y", *src.in_args, "-i", str(src.path), "-vf", norm_filter(src, h),
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(TIMELINE_CRF),
         "-g", str(round(float(src.fps) * GOP_SECONDS)), "-threads", str(threads), str(out)],
    )  # fmt: skip
    n = max(1, math.ceil(src.duration))
    sums, counts = [0.0] * n, [0] * n
    for size, key, t in packets(out):
        if key:
            continue
        i = min(n - 1, max(0, int(t)))
        sums[i] += size
        counts[i] += 1
    vals = [s / c if c else 0.0 for s, c in zip(sums, counts)]
    # Seconds with only a keyframe (rare) borrow their neighbour's value.
    for i, v in enumerate(vals):
        if v == 0.0:
            vals[i] = next((x for x in vals[i:] + vals[:i][::-1] if x > 0), 1.0)
    return vals


def pick_slices(src: Source, n: int, length: float) -> list[tuple[float, float]]:
    """n slices of `length` seconds, evenly spread over the range (absolute source times)."""
    length = min(length, src.duration / n)
    gap = (src.duration - n * length) / n
    return [(src.start + gap / 2 + i * (length + gap), length) for i in range(n)]


def build_reel(src: Source, slices: list[tuple[float, float]], work: Path) -> Source:
    """Concatenate the slices, normalized as the reference is, into a near-lossless reel."""
    # mp4, not mkv: Matroska stamps frames to the millisecond (16/17 ms at 60 fps), and the
    # XPSNR filter pairs frames by timestamp, so scoring a reel against itself paired
    # neighbouring frames and read ~35 dB instead of infinity. The track timescale is a
    # multiple of the frame rate, so every frame lands on an exact tick.
    out = work / "reel.mp4"
    fps = src.fps
    timescale = fps.numerator * (1000 if fps.denominator == 1 else 1)
    ins: list[str] = []
    chains = []
    for i, (a, d) in enumerate(slices):
        ins += ["-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", str(src.path)]
        chains.append(f"[{i}:v]{norm_filter(src)},setpts=PTS-STARTPTS[v{i}]")
    lavfi = (
        ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(len(slices))) + f"concat=n={len(slices)}:v=1:a=0[out]"
    )
    ff.run(
        [*ff.FFMPEG, "-v", "error", "-y", *ins, "-filter_complex", lavfi, "-map", "[out]",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p",
         "-video_track_timescale", str(timescale), "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", str(out)],
    )  # fmt: skip
    return probe(out)


def crf_args(cand: Candidate, src: Source, crf: int, threads: int) -> list[str]:
    args = codec_args(cand, src, 1000, final=False, threads=threads)
    i = args.index("-b:v")
    if cand.codec == "x264":
        return args[:i] + ["-crf", str(crf)] + args[i + 2 :]
    return args[:i] + ["-b:v", "0", "-crf", str(crf)] + args[i + 2 :]


def encode_point(reel: Source, cand: Candidate, crf: int, work: Path, threads: int) -> Point:
    ext = "mp4" if cand.codec == "x264" else "webm"
    out = work / f"{cand.label()}.crf{crf}.{ext}"
    vf = norm_filter(reel, cand.height, cand.fps)
    ff.run(
        [
            *ff.FFMPEG,
            "-v",
            "error",
            "-y",
            "-i",
            str(reel.path),
            "-vf",
            vf,
            "-an",
            *crf_args(cand, reel, crf, threads),
            str(out),
        ]
    )
    key, inter = key_inter(out)
    sc = score(reel, out, out.with_suffix(".xpsnr.txt"))
    out.unlink()
    out.with_suffix(".xpsnr.txt").unlink(missing_ok=True)
    return Point(crf, key, inter, sc.xpsnr_yuv, sc.ssim_y)


def run_probe(
    src: Source,
    cands: list[Candidate],
    work: Path,
    *,
    jobs: int = 8,
    n_slices: int = 4,
    slice_len: float = 3.0,
    log: Callable[[str], None] = lambda s: None,
    on_done: Callable[[], None] = lambda: None,
) -> Probe:
    work.mkdir(parents=True, exist_ok=True)
    threads = max(2, (os.cpu_count() or 4) // jobs)
    log("probe: cost timeline")
    tl = timeline(src, work, threads=os.cpu_count() or 4)
    slices = pick_slices(src, n_slices, slice_len)
    reel_c = []
    for a, d in slices:
        i0 = int(a - src.start)
        i1 = max(i0 + 1, math.ceil(a + d - src.start))
        seg = tl[i0:i1]
        reel_c.append(sum(seg) / len(seg))
    log(f"probe: sample reel of {len(slices)} × {slices[0][1]:.1f}s")
    reel = build_reel(src, slices, work)
    jobs_list = [(c, crf) for c in cands for crf in CRFS[c.codec]]
    with ThreadPoolExecutor(max_workers=jobs) as ex:

        def one(job):
            r = encode_point(reel, job[0], job[1], work, threads)
            on_done()
            return r

        pts = list(ex.map(one, jobs_list))
    curves = []
    for c in cands:
        mine = [p for (cc, _), p in zip(jobs_list, pts) if cc == c]
        curves.append(CandCurve(c.codec, c.height, float(c.fps), mine))
    reel.path.unlink(missing_ok=True)
    return Probe(
        start=src.start, duration=src.duration, src_fps=float(src.fps), timeline=tl,
        reel_complexity=sum(reel_c) / len(reel_c), slices=slices, curves=curves,
    )  # fmt: skip


def audio_kbps_for(src: Source, audio: bool) -> dict[str, float]:
    return dict(AUDIO_KBPS) if audio and src.has_audio else {}
