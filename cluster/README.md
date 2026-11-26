# Cluster ground-truth oracle

Runs the baseline fit4 sweep (no audio, default limit, default `--jobs 4`) on a list of windows in batch pods and
records, per window, every candidate's tries, final size, XPSNR/SSIM and seconds, plus CPU seconds per encode.
The goal is a (window, candidate) -> size / quality / time data set.

Files: `windows.json` (48 windows: 40 new from 40 different recordings, plus the 8 earlier ones, flagged `"rerun"`),
`choose_windows.py` (deterministic choice, seed inside), `make_shards.py`, `shards/shardN.txt`, `run_shard.sh`,
`run_window.py` (one window), `fit4_runner.py`, `compare_oracle.py`. The sweep code is the `gui-sketches` code plus a
logging-only patch (the `dump` module) and rusage accounting around each ffmpeg call.

Paths: sources are read from `/videos/<file>` (read-only), results go to `/videos-out/cluster/`:

- `<id>.json` the result (same schema as the local oracle: `window`, `exit`, `stderr_tail`, `dump.meta`,
  `dump.candidates[]`), plus `host`, `copy_s`, `wall_s`, `cpu_s` (children user/sys and cgroup usage) and per
  candidate `enc_cpu_s`, `score_cpu_s` and per try `cpu_s`.
- `<id>.error.json` a failed window (the shard carries on). It is replaced by `<id>.json` when a rerun succeeds.
- `<id>/winner` file, `<id>/log.txt` the full fit4 log.

Each window copies its source to the pod's local disk once (the next window of the same recording reuses the copy),
runs there, and deletes its work directory. Nothing big is written to the share.

## Progress

    ssh <batch host> cc-job ls                          # jobs f4c0 .. f4c7 (pod names start with ccj-f4c)
    ls /mnt/pool/fit4-sweep-eval/cluster/*.json | grep -vc error   # finished windows (48 expected, 47 in the batch)
    ls /mnt/pool/fit4-sweep-eval/cluster/*.error.json
    cc-job logs f4c3                                   # "=== id time" per window, "id ok <seconds>"

## Stop and resume

    cc-job rm f4c0 f4c1 ...        # stops the pods; a window in flight is lost, finished ones stay

Resume: rerun the same command for a shard. `run_window.py` skips any window whose `<id>.json` exists, so a rerun of
all shards only does the missing windows:

    cc-job run --cpu 3 --mem 8Gi --disk 12Gi --timeout 23h --videos --flake . --detach --name f4c0 -- \
        sh cluster/run_shard.sh cluster/shards/shard0.txt

Pods must fit a node's free CPU (about 6.5 to 7.4 of 12 were free), so an 8-CPU pod never schedules; the batch uses
3-CPU pods. Pass `--ncpu N` to `run_window.py` only to change the reference setting (below).

## Determinism

fit4 gives each of its 4 parallel encodes `max(2, os.cpu_count() // jobs)` threads, and x264/vp9 output depends on
the thread count. A pod sees its host's core count, not its limit, so `fit4_runner.py` pins `os.cpu_count()` to 16,
the value of the local reference run (4 threads per encode). The number of CPUs given to the pod therefore changes
only the speed.

Smoke test (window w1-mechvr, 6-CPU pod) against the local run:

- Same winner (vp9-1080p60, identical file size 4,100,063 B) and the same two finalists. The screening ranking by
  XPSNR is identical across all 16 candidates.
- All 8 vp9 candidates match exactly (size, XPSNR, SSIM). 3 of 8 x264 candidates match exactly; the others differ by
  a few hundred to a few thousand bytes and up to 0.01 dB. x264 is not bit-reproducible across these two machines
  (different CPU model and build), so expect this noise in x264 sizes.
- One x264 candidate (720p60) lands 100 KB lower: the first try came out just over the limit on the cluster, so it
  took a second try (4,091,669 B, XPSNR -0.106 dB) where the local run kept the first (4,193,745 B). Retry counts for
  candidates near the limit are therefore not stable.
- Time: 1646 s versus 401 s locally (4.1x), at 5.6 of 6 CPUs busy, 9225 CPU-seconds.

## Estimate

About 300,000 CPU-seconds for the 47 remaining windows (scaled by pixel rate x seconds from the smoke test), on about
18 usable CPUs: roughly 4.5 to 6 hours, longer if other cluster work competes.
