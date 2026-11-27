# Independent verification of sweep-fast (held-out gate)

Verdict: **FAIL.** On the held-out screencast w3-ardour the new default delivers 53.12 dB against the oracle's 57.68 dB (4.56 dB worse; the gate allows 0.3). The other two held-out windows pass with a small gain, every delivered file is under the limit, and the time is well inside the budget (34% of the oracle's).

Not checked: nobody watched the encoded clips; each window ran once; w3's frame count (959 frames scored) was not compared with the oracle's because the oracle dump does not record it.

## Table

Commit verified: `8be8d13` (sweep-fast). Each run was `fit4 <local copy of source> --start S --duration D --no-audio --work <dir>` with defaults (4 MiB = 4,194,304 B), one window per hold of `flock /tmp/fit4-heavy.lock local-job --`. Wall time runs from the start of the command inside the lock to its exit, and includes the probe. XPSNR was measured again on the delivered file with `fit4.metrics.score` against `trim(probe(src), start, duration)`, which is the oracle's metric at source size and frame rate.

| window | split | oracle winner | oracle dB | oracle s | new winner | new dB (re-measured) | new s | delivered bytes | under limit | dB vs oracle | pass |
|---|---|---|---|---|---|---|---|---|---|---|---|
| w1-mechvr | heldout | vp9-1080p60 | 37.10 | 386.2 | vp9-1080p60 | 37.154 | 205.4 | 4,160,131 | yes | +0.05 | yes |
| w2-dragonfist | heldout | vp9-720p60 | 30.23 | 630.2 | vp9-720p60 | 30.260 | 131.3 | 4,178,674 | yes | +0.03 | yes |
| w3-ardour | heldout | vp9-720p30.0002 | 57.68 | 139.3 | vp9-720p60.0004 | 53.119 | 56.9 | 3,308,964 | yes | **-4.56** | **no** |
| w4-quake | tune (control) | vp9-900p60 | 32.00 | 749.2 | vp9-900p60 | 32.055 | 208.9 | 4,185,179 | yes | +0.06 | (control) |

Held-out total: new 393.6 s against the oracle's 1155.7 s, which is **34.1%** (the gate allows 40%). Size: all at or under the limit. Quality: fails on w3.

The re-measured XPSNR matched fit4's own printed figure to 0.01 dB on all four windows. Output geometry was as labelled: w1 1920x1080 at 60 fps, w2 1280x720 at 60, w3 1280x720 at 60, w4 1600x900 at 60.

## Why w3 fails

1. **The prediction extrapolates without a cap.** On this easy screencast the budget (about 2050 kbps) is above the reel bitrate even at CRF 10, the lowest CRF the probe allows. `Probe.predict` then follows the slope of the last segment, from CRF 15 to CRF 10, past its last point. It predicts 67.25 dB for vp9-720p60, which measures 53.1. The probe's measured points for 720p60 were 57.37 (CRF 10), 54.62 (CRF 15) and 32.41 (CRF 63).
2. **The inflated figure prunes the true winner.** The judder rule compares vp9-720p30's estimate of 56.8 dB (a correct estimate: the oracle measured 57.7) with the inflated 67.3, so it skips every 30 fps candidate. With `--screen none` and a 0.5 dB finals margin only vp9-720p60 gets a final. The oracle's own vp9-720p60 screen scored 53.09, so the new run delivered exactly the candidate it picked. The pick was the problem.
3. **The final missed its size band as well.** It ran 7 encodes in 3 rounds and kept 3.31 MB (79% of the limit), because size did not follow bitrate on this content: 3273 kbps gave 3.31 MB and 4025 kbps gave 4.45 MB. Even with the right candidate, this content would have left about 20% of the budget unused.

## Diff review (gui-sketches...sweep-fast)

- **Size limit:** no path delivers over the limit. `run_finals` keeps only results of at most `limit` bytes, `encode_to_size` keeps `best` of at most `limit`, and the chosen final is copied byte for byte.
- **Finals are real encodes:** every delivered file comes from a real 2-pass encode fitted to the size (`encode_once` with `final=True`). `--screen none` skips screening only.
- **Metric:** the same `metrics.score` with the same `distorted_filter` and `reference_filter` against the trimmed `Source`. The only change is an `ssim=False` option, which only the probe uses.
- **First-pass reuse:** the key is a hash of the full ffmpeg command without `-b:v`, and that command includes `-vf` (height and fps), threads, all codec flags and the source path, start and duration. Different geometry cannot share stats. One side effect costs only speed: `run_finals` changes the thread count between rounds, which changes the key, so later rounds sometimes rerun pass 1.
- **Held-out leakage:** no held-out id or path appears in `src`, `tests` or `scripts`, and `eval/analysis.py` excludes the held-out split. One constant may have been tuned with held-out data. The comment on `FIRST_ROUND` (search.py:172-174) cites "64 first tries on the eval windows, median 0.976". Tune-only oracle data gives 40 first tries under the limit (median 0.978), and all 8 windows give 65 (median 0.974). The cited figures match the all-windows set more closely, so held-out windows were probably included. The effect is small, since the multipliers are generic (1.0 and 1.02), but the data source should be stated.
- **Silent empty pruning:** this does not happen. An empty kept set falls back to the full grid with a log line, and a probe failure does too.
- **Dead fallback:** cli.py:429-434 is unreachable. `if not finals and mode == "none": ... screening the rest of the grid` never runs, because `run_final_stage` raises `RuntimeError` when no final fits, even after the cpu-used 2 retry. With `--screen none`, a run whose one or two finals cannot fit therefore exits 1 instead of screening the other candidates. The README describes the opposite order ("the rest of the grid is screened; if still none, VP9 is retried with -cpu-used 2").
- **Preflight JSON and GUI drift:** `preflight.VP9_FLOOR` changed from 1.6 to 2.0, but `sketches/shared/predict.js` still has `VP9_FLOOR = 1.6`, so the GUI and the CLI disagree about which VP9 geometries are feasible. The floor at 2.0 also prunes candidates that do fit: on w2, vp9-900p60 was skipped as "would not fit", but the oracle fitted it on its second try at 30.11 dB, 0.12 dB behind the winner. `build_reel` now writes mp4, which changes predicted values (30 fps predictions shift by about 3 dB) for anyone using `preflight.run_probe`, but the JSON schema is unchanged. A `probe.json` written by `fastprobe` has curves with a single point (CRF 63 only) for geometries that cannot fit. They are harmless because `predict` returns None below the floor, but the file is not shaped like `run_probe`'s output.
- **Tests and lint:** `uv run pytest` passed 92 of 92 (45 s, under the lock). `ruff check src tests scripts` is clean. `ruff check` on the whole repo reports 59 errors, all in `eval/*.py` (driver scripts). `ty check` reports 1 error: `eval/sheet.py` imports PIL, which is not a dependency.

## What S would notice running it by hand

- **No progress bar on the default path.** Neither the probe (12-54 s) nor the finals (`run_finals`) has one. The old default showed screening and final bars. Each run prints only log lines.
- **Log noise:** 16 per-candidate prune lines and 4 judder lines on every run. The line "keeping 6 of 16 candidates" also appears when `--screen none` will encode only 1 or 2, which is misleading.
- **Changed defaults:** `--screen none` (no screening), `--jobs` now defaults to the CPU budget, finals use a 97-100% band, a second final runs only within 0.5 dB, and clips under 10 s skip the probe.
- **Report:** with `--screen none`, `screen` is empty, so the report's screening table has no rows (report.py now defaults the max over it to 0.0). I did not open a report to check how this looks.

## Fix list

1. Cap `Probe.predict` (and `predict.js`) at the highest measured point, or at most a small slope-limited step past it. When the budget is above the lowest-CRF bitrate, add a point at a lower CRF, or treat the candidate as "predicted at the ceiling" and do not rank it on that figure.
2. Do not let the judder rule prune a lower frame rate when the best full-rate figure was extrapolated beyond the measured points. In that case, also run a final for the best 30 fps candidate.
3. For finals, when a candidate stays under the band after 3 rounds (w3: 79%), add rounds or use a ln-size secant with a wider step, so easy content uses the budget.
4. Make the fallback for `--screen none` reachable: catch the `RuntimeError` from `run_final_stage` and screen the rest of the grid, or fix the README.
5. Set `VP9_FLOOR` the same in `predict.js` and in Python, and recheck 2.0 against w2's vp9-900p60, which was pruned but did fit.
6. Add a progress bar for the probe and the finals on the default path.
7. State the data source for `FIRST_ROUND`, using tune windows only.
8. Re-run this gate on the held-out windows only after fixes 1 and 2. Expect w3's time to rise somewhat; there is about 70 s of headroom under the 40% budget (462 s allowed, 394 s used).

Raw run records (JSON, logs, delivered files) are in the verifier's session scratchpad under `v/`, which is not kept.

# Second verification, after the repair round (commit 571933a)

Verdict: **PASS.** On all three held-out windows the delivered file is under 4 MiB and within 0.3 dB of the oracle winner (w3 now matches it: 57.68 dB, the same candidate). Held-out wall time is 416.6 s against the oracle's 1155.7 s, **36.0%** (the gate allows 40%).

Not checked: nobody watched the clips; each window ran once; the progress bars were not seen on a terminal (my runs captured stderr to a file, where rich draws nothing). w3 is no longer a clean held-out test: the first verdict described its failure in detail, and the repair (the frame-rate hedge) was written knowing it.

## Table

Each run was `fit4 <local copy of source> --start S --duration D --no-audio --work <dir>` with no other flags, one window per hold of `flock /tmp/fit4-heavy.lock local-job --` (the libvpx benchmark job shared the lock; waits are not counted). Wall time runs from the start of the command inside the lock to its exit, probe included. XPSNR was re-measured on the delivered file with `fit4.metrics.score` (ssim on, the oracle's filter graph) against `trim(probe(src), start, duration)`, at source size and frame rate (frame counts 1200 / 1440 / 959 / 1560 = source frames).

| window | split | oracle winner | oracle dB | oracle s | new winner | new dB (re-measured) | new s | delivered bytes | under 4,194,304 | dB vs oracle | pass |
|---|---|---|---|---|---|---|---|---|---|---|---|
| w1-mechvr | heldout | vp9-1080p60 | 37.10 | 386.2 | vp9-1080p60 | 37.154 | 205.8 | 4,160,131 | yes | +0.05 | yes |
| w2-dragonfist | heldout | vp9-720p60 | 30.23 | 630.2 | vp9-720p60 | 30.260 | 131.7 | 4,178,674 | yes | +0.03 | yes |
| w3-ardour | heldout | vp9-720p30.0002 | 57.68 | 139.3 | vp9-720p30.0002 | 57.677 | 79.2 | 4,176,220 | yes | -0.00 | yes |
| w4-quake | tune (control) | vp9-900p60 | 32.00 | 749.2 | vp9-900p60 | 32.055 | 206.5 | 4,185,179 | yes | +0.06 | (control) |

Held-out total 416.6 s of 1155.7 s = 36.0% (limit 462.3 s). w1, w2 and w4 delivered byte-identical files to the first verification; w3 changed (3,308,964 B at 53.12 dB before). Output geometry as labelled (w3 1280x720 at 30 fps).

## How w3 was won

The probe still ranks it wrong. With the cap, vp9-720p60 is predicted 58.27 dB "(at ceiling)" and vp9-720p30 53.16; measured they are 53.35 and 57.68. Pruning kept the best of each frame rate because the leader was capped, and `hedge_finals` added vp9-720p30 as a second final, where `pick_by_prediction` alone (0.5 dB margin) would have dropped it. The real finals chose. The extra size rounds also worked: both finals reached the 97-100% band in 4 rounds (12 encodes, 79 s). So on easy content the safety comes from the hedge, not from the prediction; a clip where the winner is a lower-height 30 fps candidate (not the best-ranked 30 fps one) would still be missed. Odd but harmless: vp9-600p60/600p30 were predicted 35.6 dB "at ceiling" next to 58 for 720p.

## Diff review (repair commit, on top of the first review)

- **Size limit:** unchanged paths; extra rounds only add tries, and the chosen file is still the largest result at or under the limit.
- **Finals:** still real 2-pass size-fitted encodes. The fallback for `--screen none` is now reachable (the `RuntimeError` is caught, the rest of the grid is screened, then finals).
- **Metric:** unchanged from the first review; same code as the oracle's baseline worktree except the probe-only `ssim=False` option.
- **Held-out leakage:** no held-out id, path or oracle figure in `src`, `tests`, `scripts` or `sketches`. `EXTRAP_MAX_DB` cites w6 (tune), the `FIRST_ROUND` comment now cites tune-only data, `VP9_FLOOR` stayed 2.0 with tune evidence, and `eval/new-fix` holds only w4 and w6. The design of the hedge was informed by the first verdict's w3 description (see "Not checked").
- **Stats-file reuse:** unchanged; key includes geometry.
- **Preflight JSON / GUI:** schema unchanged; `prune.json` gained a `ceiling` field. `predict.js` now matches Python (`VP9_FLOOR = 2.0`, the 0.5 dB cap, `atCeiling`), so the GUI's predictions change: VP9 geometries between 1.6x and 2.0x of the CRF-63 bitrate now show as infeasible, and easy clips stop showing extrapolated figures. Merging into gui-sketches is clean (`git merge-tree`). The comment above the floor check in `preflight.predict` still says "~1.6x".
- **Tests and lint:** `uv run pytest` 95 passed (45.6 s, under the lock). `ruff check src tests scripts` clean; `ruff format --check` wants to reformat `scripts/measure_cpuused.py`; `ty check src tests scripts` clean. Whole-repo `ruff check` still has 59 errors and `ty check` 1 (all in `eval/`, PIL in `eval/sheet.py`), which the pre-commit hooks will hit if they run on those files.

## What S would notice running it by hand

- Log is shorter: survivors only plus "N ruled out (reasons in .../prune.json)". On easy clips every survivor says "(at ceiling)" and a predicted figure that can be 5 dB off, which reads as more confident than it is.
- Frame-rate labels like `vp9-720p30.0002` / `720p60.0004` for a 60.0004 fps source (pre-existing).
- The header says "+audio" with `--no-audio` (it describes the source; pre-existing).
- Defaults changed from the old sweep, as in the first review: probe always runs (clips of 10 s or more), `--screen none`, `--jobs` from the CPU budget, finals band 97-100%, plus now up to two extra final rounds and a hedge final on easy clips.

## Minor fix list (not gate items)

1. Run `ruff format` on `scripts/measure_cpuused.py`; exclude `eval/` from ruff/ty or fix it.
2. Update the "~1.6x" comment in `preflight.predict`.
3. Consider printing "at ceiling (capped guess)" figures differently, or not ranking on them, since the hedge is what saves easy clips.

Raw run records are in the verifier's session scratchpad under `v/runs/`, which is not kept.
