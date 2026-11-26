Files in folder: 304 entries; ffprobe-readable videos: 268; unreadable (mode 0600 on the NFS share, permission denied for this agent): 27 mp4/webm/mkv.
Finished clips (readable, <= 4 MiB, < 120 s): 137; long or large recordings: 130.

| metric | min | p10 | p25 | median | p75 | p90 | max |
|---|---|---|---|---|---|---|---|
| duration s | 3.4 | 9.2 | 12.0 | 17.0 | 26.9 | 42.9 | 71.8 |
| size MiB | 0.82 | 1.82 | 2.71 | 3.32 | 3.73 | 3.85 | 3.98 |
| total kbps | 423 | 581 | 913 | 1332 | 2115 | 2684 | 3893 |
| height px | 225 | 480 | 720 | 720 | 1024 | 1080 | 1916 |

- fps: {'60': 98, '30': 39}
- video codec: {'vp8': 38, 'vp9': 31, 'h264': 68}
- audio present: 11 of 137
- resolution: 1280x720 x42, 1024x1024 x26, 1920x1080 x23, 720x720 x14, 640x360 x7, 476x540 x6, 424x480 x5, 636x720 x3
- share under 15 s: 43%; 15-30 s: 37%; over 30 s: 20%
- mean size 3.12 MiB; share within 0.5 MiB of the cap: 43%
- unreadable named clips: 18 with a time range in the name, durations p10/median/p90 = 6.5/12.4/52.9 s (consistent with the readable set)

Long recordings (readable, >= 120 s or > 4 MiB, excluding cut-downs and duplicates), grouped:

| group | files | total min | resolution / fps | typical size |
|---|---|---|---|---|
| other single recordings | 30 | 300.9 | 1024x1024@60, 1280x720@60, 1684x1872@60, 1916x1916@60, 1920x1080@60, 360x360@60 | 379 MiB avg |
| NeosVR / VRChat social | 3 | 177.8 | 1280x720@60 | 2188 MiB avg |
| VR mech cockpit replays (2024-12-13) | 9 | 44.7 | 1920x1080@60 | 685 MiB avg |
| dragon fist VR fighter | 6 | 16.9 | 1920x1080@60 | 636 MiB avg |
| Mushi Lantern dev captures (Sept 2026) | 18 | 16.3 | 1280x720@60, 1920x1080@30, 1920x1080@60, 3840x2160@60 | 27 MiB avg |
| Electronauts VR | 3 | 16.0 | 1280x720@60 | 198 MiB avg |
| Demeo VR tabletop | 3 | 14.1 | 1280x720@60 | 214 MiB avg |
| osc-grimoire Unity | 14 | 10.5 | 1024x1024@30, 1024x1024@60, 1920x1080@60, 720x720@30 | 19 MiB avg |
| VRChat | 5 | 9.2 | 1280x720@60, 1916x1916@60 | 60 MiB avg |
| Ardour DAW screencast | 2 | 7.2 | 1280x720@60 | 133 MiB avg |
| Quake / 2026-11-25 sessions | 7 | 7.1 | 1920x1080@60 | 104 MiB avg |
| sneeditorium | 7 | 6.3 | 1000x1000@60, 1024x1024@60, 500x500@60, 720x720@60 | 33 MiB avg |
| 1908x2162 VR dev captures (Sept 2026) | 9 | 6.1 | 1908x2162@60, 3840x2160@60, 424x480@60 | 21 MiB avg |
| oversaber | 3 | 5.7 | 1024x1024@60 | 184 MiB avg |
| bikeheadvr | 3 | 4.9 | 1280x720@60, 1920x1080@60 | 309 MiB avg |
| multibox VRChat world | 4 | 3.8 | 1280x720@60, 854x480@60 | 18 MiB avg |
| VRChat street 2026 | 2 | 3.2 | 1024x1024@60, 1920x1080@60 | 371 MiB avg |
| beanstalker | 2 | 2.6 | 1280x720@60 | 46 MiB avg |
