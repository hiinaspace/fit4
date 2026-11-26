"""Running ffmpeg/ffprobe, and checking the local build has what fit4 needs."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Sequence

from . import dump

# Every ffmpeg call gets -nostdin and stdin=DEVNULL. Otherwise ffmpeg reads the
# terminal for its interactive keys and can swallow a caller's heredoc or piped
# script. ffprobe has no -nostdin option, so it only gets the DEVNULL.
FFMPEG = ("ffmpeg", "-nostdin", "-hide_banner")
FFPROBE = ("ffprobe", "-hide_banner")

VIDEO_ENCODERS = {"x264": "libx264", "vp9": "libvpx-vp9"}
AUDIO_ENCODERS = {"x264": "aac", "vp9": "libopus"}
# fps/scale/format/select are core filters; listed so a stripped build fails
# here instead of halfway through a sweep.
FILTERS = ("xpsnr", "ssim", "setparams", "scale", "fps", "format", "select", "split")
REPORT_ENCODER = "libwebp"  # lossless frame grabs for the A/B viewer


class MissingFFmpeg(RuntimeError):
    """ffmpeg/ffprobe absent, or the build lacks an encoder or filter."""


def run(args: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    if args[0] == "ffmpeg" and "-nostdin" not in args:
        raise AssertionError("ffmpeg call without -nostdin")
    if not dump.enabled():
        return subprocess.run(list(args), stdin=subprocess.DEVNULL, check=True, capture_output=capture, text=True)
    return _run_accounted(list(args), capture)


def _run_accounted(args: list[str], capture: bool) -> subprocess.CompletedProcess[str]:
    """subprocess.run, plus the child's CPU seconds (rusage) added to this thread's dump.tls.cpu."""
    out = tempfile.TemporaryFile() if capture else None
    err = tempfile.TemporaryFile() if capture else None
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
    _, status, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    dump.tls.cpu = getattr(dump.tls, "cpu", 0.0) + ru.ru_utime + ru.ru_stime
    texts = []
    for f in (out, err):
        if f is None:
            texts.append(None)
            continue
        f.seek(0)
        texts.append(f.read().decode(errors="replace"))
        f.close()
    if p.returncode:
        raise subprocess.CalledProcessError(p.returncode, args, texts[0], texts[1])
    return subprocess.CompletedProcess(args, 0, texts[0], texts[1])


def parse_listing(text: str) -> set[str]:
    """Names from `ffmpeg -encoders` or `ffmpeg -filters` output.

    Entry lines are `<flags> <name> <description>`; legend lines are
    `<flags> = <meaning>` and the separator is a lone `------`.
    """
    names = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] != "=":
            names.add(parts[1])
    return names


def missing_features(encoders: set[str], filters: set[str], codecs: Iterable[str], audio: bool) -> list[str]:
    need = []
    for c in codecs:
        need.append(("encoder", VIDEO_ENCODERS[c]))
        if audio:
            need.append(("encoder", AUDIO_ENCODERS[c]))
    need.append(("encoder", REPORT_ENCODER))
    need += [("filter", f) for f in FILTERS]
    out = []
    for kind, name in dict.fromkeys(need):
        have = encoders if kind == "encoder" else filters
        if name not in have:
            out.append(f"{name} {kind}")
    return out


def require_binaries() -> None:
    absent = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if absent:
        raise MissingFFmpeg(
            f"{' and '.join(absent)} not found on PATH. fit4 needs FFmpeg 7.1 or newer "
            "built with libx264, libvpx and libwebp (most distro and static builds are)."
        )


def check(codecs: Iterable[str], audio: bool) -> None:
    """Raise MissingFFmpeg naming every encoder/filter the sweep would need but lacks."""
    require_binaries()
    words = run([*FFMPEG, "-version"], capture=True).stdout.split()
    version = f"ffmpeg {words[2]}" if len(words) > 2 else "this ffmpeg"
    enc = parse_listing(run([*FFMPEG, "-encoders"], capture=True).stdout)
    filt = parse_listing(run([*FFMPEG, "-filters"], capture=True).stdout)
    missing = missing_features(enc, filt, codecs, audio)
    if missing:
        hints = []
        if "xpsnr filter" in missing:
            hints.append("the xpsnr filter arrived in FFmpeg 7.1")
        if any(m.startswith(("libx264", "libvpx")) for m in missing):
            hints.append("--codecs limits the sweep to the encoders you have")
        if any(m.startswith(("aac", "libopus")) for m in missing):
            hints.append("--no-audio drops the audio track")
        hint = f" ({'; '.join(hints)})" if hints else ""
        raise MissingFFmpeg(f"{version} lacks: {', '.join(missing)}{hint}.")
