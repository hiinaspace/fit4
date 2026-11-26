# /// script
# dependencies = ["numpy"]
# ///
"""Audio contour for the sketches: mono mix of the SOURCE recording, per 0.05 s window.

Usage: uv run --with numpy python sketches/tools/make_audio.py SRC.mp4 OUT/audio.json

Output {step, rms, peak, onset}:
  rms, peak : dBFS per window (floor -100), rounded to 1 decimal
  onset     : positive spectral flux of the log-magnitude spectrum between consecutive windows,
              scaled so the 99.5th percentile is 100 (clipped to 0..150), rounded to 1 decimal
"""
import json, subprocess, sys
import numpy as np

STEP = 0.05
RATE = 48000

def main(src, out):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
        check=True, stdout=subprocess.PIPE).stdout
    x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    w = int(round(STEP * RATE))
    n = len(x) // w + (1 if len(x) % w else 0)
    x = np.pad(x, (0, n * w - len(x))).reshape(n, w)
    db = lambda v: 20 * np.log10(np.maximum(v, 1e-5))   # -100 dBFS floor
    rms = db(np.sqrt((x ** 2).mean(axis=1)))
    peak = db(np.abs(x).max(axis=1))
    mag = np.log1p(np.abs(np.fft.rfft(x * np.hanning(w), axis=1)) / 10.0)
    flux = np.maximum(mag[1:] - mag[:-1], 0).sum(axis=1)
    onset = np.concatenate([[0.0], flux])
    scale = np.percentile(onset, 99.5) or 1.0
    onset = np.clip(onset / scale * 100, 0, 150)
    r = lambda a: [round(float(v), 1) for v in a]
    json.dump({"step": STEP, "rms": r(rms), "peak": r(peak), "onset": r(onset)}, open(out, "w"), separators=(",", ":"))
    print(f"{n} windows ({n * STEP:.2f} s) -> {out}")

main(sys.argv[1], sys.argv[2])
