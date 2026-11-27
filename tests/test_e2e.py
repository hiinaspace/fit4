"""One real, seconds-long run of the whole pipeline on synthetic clips."""

import json
import subprocess

import pytest

from fit4 import cli, ff


def _ffmpeg_ok() -> str | None:
    try:
        ff.check(["x264", "vp9"], audio=True)
    except (ff.MissingFFmpeg, subprocess.CalledProcessError) as e:
        return str(e)
    return None


why = _ffmpeg_ok()
pytestmark = [pytest.mark.ffmpeg, pytest.mark.skipif(why is not None, reason=f"needs a full ffmpeg: {why}")]


def make_clip(path, *, audio: bool, seconds: int = 3, size: str = "320x180"):
    args = [*ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=24:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    ff.run([*args, "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(path)])


def probe_streams(path):
    out = ff.run([*ff.FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)], capture=True).stdout
    return {s["codec_type"]: s["codec_name"] for s in json.loads(out)["streams"]}


@pytest.mark.parametrize("audio", [True, False])
def test_full_pipeline(tmp_path, capsys, audio):
    clip = tmp_path / "clip.mp4"
    make_clip(clip, audio=audio)
    limit = 120_000
    rc = cli.main(
        [str(clip), "--limit", str(limit), "--scales", "1,0.5", "--fps-divs", "1", "--finals", "2", "--jobs", "2"]
    )
    out, err = capsys.readouterr()
    assert rc == 0, err
    result = tmp_path / out.strip().splitlines()[-1]
    assert result.exists() and 0.9 * limit < result.stat().st_size <= limit
    streams = probe_streams(result)
    assert ("audio" in streams) == audio
    report = (tmp_path / "clip.fit4.d" / "report.html").read_text()
    assert "clip.fit4." in report and str(tmp_path) not in report  # no absolute paths in shared reports
    finals = [r for r in json.loads((tmp_path / "clip.fit4.d" / "results.json").read_text()) if r["stage"] == "final"]
    assert {r["label"].split("-")[0] for r in finals} == {"x264", "vp9"}


def test_already_fits_does_nothing(tmp_path, capsys):
    clip = tmp_path / "small.mp4"
    ff.run([*ff.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1", "-c:v", "libx264", str(clip)])
    assert cli.main([str(clip)]) == 0
    assert "nothing to do" in capsys.readouterr().err
    assert not (tmp_path / "small.fit4.d").exists()


def test_audio_only_exits_2(tmp_path, capsys):
    wav = tmp_path / "tone.wav"
    ff.run([*ff.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=d=1", str(wav)])
    assert cli.main([str(wav)]) == cli.USAGE
    assert "no video stream" in capsys.readouterr().err


def test_too_long_for_limit_exits_2(tmp_path, capsys):
    clip = tmp_path / "long.mp4"
    make_clip(clip, audio=True, seconds=2)
    assert cli.main([str(clip), "--limit", "10KB"]) == cli.USAGE
    assert "--no-audio" in capsys.readouterr().err


def test_probe_prunes_and_writes_its_decisions(tmp_path, capsys):
    clip = tmp_path / "clip.mp4"
    make_clip(clip, audio=False, seconds=12, size="640x360")
    limit = 150_000
    rc = cli.main(
        [str(clip), "--limit", str(limit), "--scales", "1,0.5", "--fps-divs", "1", "--finals", "1",
         "--prune-keep", "1", "--prune-margin", "0", "--screen", "2pass"]
    )  # fmt: skip
    err = capsys.readouterr().err
    assert rc == 0, err
    work = tmp_path / "clip.fit4.d"
    decisions = json.loads((work / "prune.json").read_text())
    assert len(decisions) == 4 and 1 <= sum(d["keep"] for d in decisions) < 4
    assert all(d["reason"] for d in decisions) and (work / "probe.json").exists()
    screens = {r["label"] for r in json.loads((work / "results.json").read_text()) if r["stage"] == "screen"}
    assert screens == {d["label"] for d in decisions if d["keep"]}
    assert "ruled out" in err and "first passes:" in err


def test_full_grid_flag_skips_the_probe(tmp_path, capsys):
    clip = tmp_path / "clip.mp4"
    make_clip(clip, audio=False, seconds=12)
    rc = cli.main([str(clip), "--limit", "150000", "--scales", "1", "--fps-divs", "1", "--finals", "1", "--full-grid"])
    assert rc == 0, capsys.readouterr().err
    assert not (tmp_path / "clip.fit4.d" / "prune.json").exists()


@pytest.mark.parametrize("mode", ["crf", "none"])
def test_screen_modes_deliver_under_the_limit(tmp_path, capsys, mode):
    clip = tmp_path / "clip.mp4"
    make_clip(clip, audio=False, seconds=12, size="640x360")
    limit = 150_000
    rc = cli.main(
        [str(clip), "--limit", str(limit), "--scales", "1,0.5", "--fps-divs", "1", "--finals", "2",
         "--prune-keep", "3", "--screen", mode]
    )  # fmt: skip
    out, err = capsys.readouterr()
    assert rc == 0, err
    result = tmp_path / out.strip().splitlines()[-1]
    assert 0.85 * limit < result.stat().st_size <= limit
    rows = json.loads((tmp_path / "clip.fit4.d" / "results.json").read_text())
    assert any(r["stage"] == "final" for r in rows)
    assert (mode == "none") == (not any(r["stage"] == "screen" for r in rows))
