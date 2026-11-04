"""Static HTML report: screening table, finals, and A/B flicker frames."""

from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path

from .metrics import distorted_filter, reference_filter
from .probe import Source
from .search import Trial


def pick_frames(per_frame: list[float], k: int = 3) -> list[int]:
    """0-based index of the worst frame in each of k equal windows."""
    n = len(per_frame)
    out = []
    for i in range(k):
        lo, hi = i * n // k, (i + 1) * n // k
        if hi > lo:
            out.append(min(range(lo, hi), key=lambda j: per_frame[j]))
    return out


def extract(path: Path, filt: str, frames: list[int], dest: Path, name: str) -> list[str]:
    sel = "+".join(f"eq(n\\,{f})" for f in frames)
    pattern = dest / f"{name}-%d.webp"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(path),
            "-vf", f"{filt},select='{sel}'", "-fps_mode", "passthrough",
            "-c:v", "libwebp", "-lossless", "1", "-compression_level", "3", str(pattern),
        ],
        check=True,
    )  # fmt: skip
    return [f"frames/{name}-{i + 1}.webp" for i in range(len(frames))]


def chart(finals: list[Trial], src: Source, width: int = 900, height: int = 160) -> str:
    vals = [v for t in finals for v in t.score.per_frame_y if v != float("inf")]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    hi = hi if hi > lo else lo + 1
    colors = ["#e4572e", "#4c8bf5", "#29a35a", "#a05ad8"]
    lines = []
    for i, t in enumerate(finals):
        pts = t.score.per_frame_y
        n = max(1, len(pts) - 1)
        coords = " ".join(
            f"{j * width / n:.1f},{height - (min(v, hi) - lo) / (hi - lo) * height:.1f}" for j, v in enumerate(pts)
        )
        c = colors[i % len(colors)]
        lines.append(f'<polyline fill="none" stroke="{c}" stroke-width="1" points="{coords}"/>')
        lines.append(f'<text x="4" y="{14 + 14 * i}" fill="{c}" font-size="12">{html.escape(t.cand.label())}</text>')
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="background:var(--panel)">'
        + "".join(lines)
        + f'<text x="{width - 4}" y="12" text-anchor="end" font-size="11" fill="currentColor">{hi:.1f} dB</text>'
        + f'<text x="{width - 4}" y="{height - 4}" text-anchor="end" font-size="11" fill="currentColor">{lo:.1f} dB</text>'
        + f'<text x="{width / 2}" y="{height - 4}" text-anchor="middle" font-size="11" fill="currentColor">'
        f"per-frame XPSNR-Y over {src.duration:.0f}s</text></svg>"
    )


def write(
    work: Path,
    src: Source,
    limit: int,
    screen: list[Trial],
    finals: list[Trial],
    chosen: Trial,
    output: Path,
) -> Path:
    frames_dir = work / "frames"
    frames_dir.mkdir(exist_ok=True)
    idx = pick_frames(chosen.score.per_frame_y)
    views: dict[str, list[str]] = {"source": extract(src.path, reference_filter(src), idx, frames_dir, "source")}
    for t in finals:
        views[t.cand.label()] = extract(t.enc.path, distorted_filter(src), idx, frames_dir, t.cand.label())

    rows = []
    best = max(t.score.primary for t in screen)
    for t in sorted(screen, key=lambda t: t.score.primary, reverse=True):
        w = t.cand.width_for(src)
        rows.append(
            f"<tr><td>{t.cand.codec}</td><td>{w}×{t.cand.height}</td><td>{float(t.cand.fps):g}</td>"
            f"<td>{t.enc.size / 1e6:.3f}</td><td>{t.enc.kbps:.0f}</td>"
            f"<td><b>{t.score.xpsnr_yuv:.2f}</b></td><td>{t.score.xpsnr_yuv - best:+.2f}</td>"
            f"<td>{t.score.xpsnr_y:.2f}</td><td>{t.score.ssim_y:.4f}</td><td>{t.enc.seconds:.0f}s / {len(t.enc.attempts)}</td></tr>"
        )
    frows = []
    for t in finals:
        mark = " ✓ chosen" if t is chosen else ""
        rel = t.enc.path.relative_to(work)
        frows.append(
            f"<tr><td>{html.escape(t.cand.label())}{mark}</td><td>{t.enc.size:,}</td><td>{t.enc.size / limit * 100:.1f}%</td>"
            f"<td><b>{t.score.xpsnr_yuv:.2f}</b></td><td>{t.score.ssim_y:.4f}</td>"
            f'<td>{t.enc.seconds:.0f}s / {len(t.enc.attempts)}</td><td><a href="{rel}">file</a></td></tr>'
        )
    vids = "".join(
        f'<figure><video src="{t.enc.path.relative_to(work)}" controls loop muted preload="metadata"></video>'
        f"<figcaption>{html.escape(t.cand.label())}</figcaption></figure>"
        for t in finals
    )
    times = [f"{i / float(src.fps):.2f}s (frame {i})" for i in idx]
    data = json.dumps({"views": views, "times": times})
    page = TEMPLATE.format(
        title=html.escape(src.path.name),
        src_desc=html.escape(
            f"{src.width}×{src.height} {float(src.fps):g} fps, {src.duration:.1f}s, {src.size / 1e6:.1f} MB"
            f"{', audio' if src.has_audio else ''}"
        ),
        limit=f"{limit:,}",
        output=html.escape(str(output)),
        rows="".join(rows),
        frows="".join(frows),
        vids=vids,
        chart=chart(finals, src),
        data=data,
    )
    path = work / "report.html"
    path.write_text(page)
    return path


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>fit4: {title}</title>
<style>
:root {{ --bg:#fafaf8; --fg:#1d1d1b; --panel:#efefea; --line:#d8d8d0; --acc:#c4441c; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16171a; --fg:#e6e6e2; --panel:#22242a; --line:#33363d; --acc:#ff7a50; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,sans-serif; margin:0 auto; padding:16px; max-width:1400px; }}
h1 {{ font-size:20px; margin:0 0 4px }} h2 {{ font-size:16px; margin:28px 0 8px }}
.muted {{ opacity:.7 }}
table {{ border-collapse:collapse; font-variant-numeric:tabular-nums; }}
td,th {{ padding:3px 10px; border-bottom:1px solid var(--line); text-align:right }}
td:first-child,th:first-child {{ text-align:left }}
.scroll {{ overflow-x:auto }}
.flick {{ display:flex; gap:6px; flex-wrap:wrap; margin:8px 0 }}
.flick button {{ background:var(--panel); color:var(--fg); border:1px solid var(--line); padding:4px 10px; border-radius:4px; cursor:pointer; font:inherit }}
.flick button.on {{ border-color:var(--acc); color:var(--acc) }}
.view {{ overflow:auto; max-height:80vh; border:1px solid var(--line); background:#000 }}
.view img {{ display:block; image-rendering:pixelated; max-width:100% }}
.view img.px {{ max-width:none }}
a {{ color:var(--acc) }}
.vids {{ display:flex; gap:12px; flex-wrap:wrap }} .vids video {{ max-width:100%; width:640px }}
figure {{ margin:0 }}
kbd {{ background:var(--panel); border:1px solid var(--line); border-radius:3px; padding:0 4px }}
</style></head><body>
<h1>{title}</h1>
<div class="muted">source {src_desc} · limit {limit} bytes · output <code>{output}</code></div>

<h2>Finals (slow preset, size-corrected)</h2>
<div class="scroll"><table><tr><th>candidate</th><th>bytes</th><th>of limit</th><th>XPSNR</th><th>SSIM-Y</th><th>time / tries</th><th></th></tr>{frows}</table></div>
{chart}

<h2>A/B flicker at each third's worst frame</h2>
<div class="muted">Keys: <kbd>1</kbd>–<kbd>9</kbd> pick a version, <kbd>space</kbd> toggles the last two, <kbd>z</kbd> fit / 1:1 / 2×, <kbd>←</kbd><kbd>→</kbd> frame.</div>
<div class="flick" id="frames"></div>
<div class="flick" id="vers"></div>
<div class="view" id="view"><img id="img" alt=""></div>

<h2>Videos</h2>
<div class="vids">{vids}</div>

<h2>Screening (fast preset; XPSNR = (4Y+U+V)/6 at source size, dB)</h2>
<div class="scroll"><table><tr><th>codec</th><th>size</th><th>fps</th><th>MB</th><th>kbps</th><th>XPSNR</th><th>Δbest</th><th>XPSNR-Y</th><th>SSIM-Y</th><th>time / tries</th></tr>{rows}</table></div>

<script>
const D = {data};
const names = Object.keys(D.views);
let f = 0, v = 0, prev = 1, zoom = 0;  // 0 = fit width
const img = document.getElementById('img');
function render() {{
  img.src = D.views[names[v]][f];
  sizeImg();
  document.querySelectorAll('#vers button').forEach((b, i) => b.classList.toggle('on', i === v));
  document.querySelectorAll('#frames button').forEach((b, i) => b.classList.toggle('on', i === f));
}}
function sizeImg() {{ img.classList.toggle('px', zoom > 0); img.style.width = zoom ? (img.naturalWidth * zoom) + 'px' : ''; }}
img.onload = sizeImg;
D.times.forEach((t, i) => {{ const b = document.createElement('button'); b.textContent = t; b.onclick = () => {{ f = i; render(); }}; document.getElementById('frames').append(b); }});
names.forEach((n, i) => {{ const b = document.createElement('button'); b.textContent = (i + 1) + ' ' + n; b.onclick = () => {{ if (i !== v) prev = v; v = i; render(); }}; document.getElementById('vers').append(b); }});
addEventListener('keydown', e => {{
  const k = parseInt(e.key);
  if (k >= 1 && k <= names.length) {{ if (k - 1 !== v) prev = v; v = k - 1; }}
  else if (e.key === ' ') {{ [v, prev] = [prev, v]; e.preventDefault(); }}
  else if (e.key === 'z') zoom = (zoom + 1) % 3;
  else if (e.key === 'ArrowRight') f = (f + 1) % D.times.length;
  else if (e.key === 'ArrowLeft') f = (f + D.times.length - 1) % D.times.length;
  else return;
  render();
}});
render();
</script>
</body></html>
"""
