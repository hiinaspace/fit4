# Sweep speed-up notes (branch `sweep-fast`)

All timings were taken inside `flock /tmp/fit4-heavy.lock local-job --`.
Clips are 30 s excerpts of local gameplay captures (A30 1080p60 game footage, B30 noisy ~40 Mbps, C30 not yet run).

## What changed

| commit | change |
|---|---|
| 34ee514 | First-pass reuse: `FirstPassCache` keyed by the pass-1 inputs; VP9 reuses at any bitrate, x264 only within +-15% of the bitrate the stats were made at. |
| 7616d8a | `layout.py`: `plan_layout` (VP9 ~4 threads side by side, x264 `cpus//4`, backfill), `run_weighted` scheduler, cgroup-aware `available_cpus()`. `--jobs N` sets the budget, `--flat-layout` is the old one-thread-per-job layout. |
| a470ef5 | Floor fallback: a VP9 candidate still over the limit retries with `-cpu-used 2` (`FLOOR_FIX`); two over-limit tries whose size did not follow the bitrate skip straight to it (`stalled`). `results.json` rows carry `floor_fix`. |
| 121f587 | Pre-flight: `fastprobe.py` (cheap probe on a 6 s reel), `prune.py` (floor rule + margin), `--full-grid`, `--prune-margin` (1.5), `--prune-keep` (4), `--probe-only`. `probe.json` and `prune.json` are written to the work dir and the decisions are logged. `preflight.py` public API untouched. |
| 1de8dce | Reel written as mp4 (see below); every floor-feasible VP9 geometry is probed. |

## Measurements

1. **First-pass byte identity.** `scripts/measure_firstpass.py`. libvpx stats are bitrate independent: a second pass from stats made at 2x the bitrate has the same packet md5 (whole-file hashes differ by the random segment UID). Test: `tests/test_encode.py` (250 and 1000 kbps). VP9 pass 1 is ~12% of a 2-pass encode (1.2 s of 8.7 s at 540p). x264 stats depend on the bitrate: 2x apart cost 0.18-0.27 dB and 2-3% bytes, 1.33x apart ~0.4% bytes, hence the +-15% band.
2. **Floor** (`scripts/measure_floor.py`). libvpx 2-pass floors on noisy footage at cpu-used 3/4 (490-760 kbps). B30 720p60 target 200: base 707 kbps, cpu4 720, cpu2 290, cpu1 280, aq-mode 0 406, cpu2+aq0 265. Target 500: base 754, cpu2 506. B30 540p30 target 80: base 264 vs cpu2 121. A30 720p60 target 600: base 634 kbps 33.22 dB vs cpu2 600 kbps 33.25 dB. Wall time equal or better with cpu2, so `FLOOR_FIX = {"-cpu-used": "2"}`.
3. **Probe point cost** (`scripts/profile_probe_point.py`, 6 s reel): decode+scale 0.4 s, VP9 good cpu3 3.9 s, cpu5 3.75 s (no gain), x264 slow 2.2 s / medium 1.7 s / veryfast 1.4 s, XPSNR 2.7 s. VP9 realtime 3x faster but 1.5-1.9 dB off (sd ~1). x264 medium vs slow +0.20 dB (sd 0.12), veryfast +1.2 dB (sd ~0.8). `scripts/measure_probe_fast.py` collects and analyses.
4. **Negative result: frame-sampled XPSNR.** Scoring every Nth / random / blocks of frames gave errors from -4.8 to +2.9 dB. XPSNR is dominated by high-MSE frames and stride sampling aliases with the GOP. Score all frames.
5. **Reel timestamp bug (found by the A30 check).** Scoring the reel against itself gave 35.09 dB, not inf. Matroska stamps 60 fps frames to 1 ms (16/17 ms), and the XPSNR filter pairs frames by timestamp. mp4 with `-video_track_timescale` a multiple of the frame rate fixes it (inf). This had made 30 fps predictions ~3 dB too high. Regression test `test_reel_scores_lossless_against_itself`. `preflight.build_reel` changed (internal; public Probe API unchanged).
6. **A30 end to end** (`fit4 A30.mp4 --no-audio --finals 1`): full grid, new layout 495 s, chosen vp9-1080p60 4,138,884 B XPSNR 36.68. Full grid, `--flat-layout` 485 s, same pick. **The layout gives nothing measurable here** (16 candidates fill 16 threads either way); its value is on machines or grids where jobs are fewer than cores. First-pass reuse: 16 run / 8 reused. First probe version (VP9 ranked by x264 rank): total 342 s but it pruned the true best, because VP9 gains much more from resolution than x264 does. Fixed by probing every feasible VP9 geometry.
7. **Probe accuracy after the fixes** (`fit4 A30.mp4 --no-audio --probe-only`), predicted vs the full-grid score: vp9-1080p60 36.22 vs 36.68, vp9-900p60 35.61 vs 35.98, vp9-720p60 34.65 vs 35.04, vp9-540p60 33.21 vs 33.63 (a steady -0.4, now `OFFSET_DB["vp9"] = 0.4`); x264-540p60 32.49 vs 32.52, x264-720p60 32.70 vs 32.23. Kept the true best and 3 more; pruned 12 of 16. 30 fps candidates are still predicted 1-3 dB high (30.65 vs ~28) but are 5+ dB behind and pruned regardless. **Probe cost 115 s** (timeline 2, reel 1, reference points 36, x264 points 29, VP9 points 47), above the 60-90 s goal.
8. Not run: B30 and C30 end to end, and a post-offset probe re-run, because the heavy lock was held by the other benchmark for long stretches (one wait cost 18 min).

## Open questions for the next builder

- **Probe cost.** 115 s vs the 90 s goal. Biggest lines: reference points (36 s, 16 encodes + 16 scores of the reel) and VP9 points (47 s). Candidates: 2 slices instead of 3, 1.5 s slices, skipping the x264 reference for geometries that are clearly dominated, scoring on the reduced resolution. Slice count and length were not re-tuned after the reel fix.
- **One-pass CRF screening** was deliberately not done (left for oracle data).
- **fps/judder.** 30 fps candidates are predicted too high; the reel scores against the 60 fps reference, so the real clip's frame-rate penalty is not seen. Harmless while the margin prunes them, but not safe for clips where 30 fps wins.
- **cpu-used 2 floor rescue.** The floor rule (budget under 1.6x the CRF-63 bitrate) was calibrated at cpu3; cpu2's floor is ~0.6x, so some VP9 geometries pruned as infeasible could be screened at cpu2.
- **x264 reuse tolerance** (+-15%) from two data points. Offsets (x264 0.2, vp9 0.4) come from one clip; check on B30 and C30.
- Optional `widen(layout, counts)` to give x264 finals more threads when few jobs are in flight.
- `ruff check` on the whole repo fails on the other agent's `eval/`; use `src tests scripts`.
