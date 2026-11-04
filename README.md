# fit4

Squeeze a video clip under an upload size limit (4chan's 4 MiB by default) at
the best *measured* quality, instead of bitrate arithmetic plus eyeballing.

```
uv run fit4 clip.mp4                 # -> clip.fit4.mp4|webm + clip.fit4.d/report.html
uv run fit4 clip.mp4 --board wsg     # 6 MiB
uv run fit4 clip.mp4 --scales 1,0.75 --fps-divs 1 --finals 1   # smaller sweep
```

How it works:

1. Probe the source; normalize to limited-range bt709 yuv420p (full-range
   `yuvj420p` game captures are the usual offender).
2. **Screen** codec × height × framerate (x264 mp4, VP9 webm) at fast presets.
   Each is 2-pass encoded at the size budget, then re-encoded with a corrected
   bitrate until it lands in 95–100% of the limit, so candidates are compared at
   equal bytes (libvpx misses targets by ±10%).
3. **Score** each at the source's display size and rate: XPSNR (reported as
   (4Y+U+V)/6) and SSIM, upscaled bicubic and frame-duplicated so low-res and
   low-fps candidates pay for what a viewer would see.
4. **Final** slow-preset encodes of the best candidate and the best of the other
   codec, size-corrected to 97.5–100% of the limit; the higher scorer is written.
5. **Report** (`report.html`): finals, a per-frame XPSNR chart, an A/B flicker
   viewer at each third's worst frame (keys 1–9, space, z), the videos, and the
   full screening table. `results.json` has the numbers.

Long GOPs (~20 s) are always on: these clips are mostly static backgrounds.

Requires ffmpeg ≥ 7 with libx264, libvpx-vp9, libwebp and the xpsnr filter.
Board limits in `boards.py` are community-reported, not official.
