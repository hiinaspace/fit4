import argparse
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from fit4 import cli
from fit4.boards import BOARDS, MIB
from fit4.probe import Source

BASE = Source(
    path=Path("x.mp4"), width=1280, height=720, fps=Fraction(30), duration=20.0, nb_frames=600,
    full_range=False, colorspace="bt709", has_audio=False, size=3_000_000, video_codec="h264",
)  # fmt: skip


@pytest.mark.parametrize(
    "text,n",
    [("4194304", 4 * MIB), ("4MiB", 4 * MIB), ("4 mib", 4 * MIB), ("10MB", 10_000_000), ("512KiB", 512 * 1024),
     ("1.5MiB", int(1.5 * MIB)), ("100kb", 100_000)],
)  # fmt: skip
def test_parse_size(text, n):
    assert cli.parse_size(text) == n


@pytest.mark.parametrize("text", ["", "4M", "four", "-1", "0", "4 MiBs"])
def test_parse_size_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_size(text)


def test_list_args_validate():
    assert cli.parse_codecs("x264, vp9") == ["x264", "vp9"]
    assert cli.parse_scales("1,0.5") == [1.0, 0.5]
    for parse, bad in [(cli.parse_codecs, "av1"), (cli.parse_scales, "1.5"), (cli.parse_scales, "0"),
                       (cli.parse_divs, "0"), (cli.parse_divs, "1.5"), (cli.parse_codecs, ",")]:  # fmt: skip
        with pytest.raises(argparse.ArgumentTypeError):
            parse(bad)


def test_bad_arguments_exit_2(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["x.mp4", "--codecs", "av1"])
    assert e.value.code == 2


def test_missing_input_exit_2(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope.mp4")]) == cli.USAGE
    assert "no such file" in capsys.readouterr().err


def test_help_mentions_time_and_exit_codes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "exit codes" in out and "minutes" in out and "--limit" in out


def test_already_fits():
    b = BOARDS["4chan"]
    assert cli.already_fits(BASE, b, 4 * MIB, audio=True)
    assert not cli.already_fits(replace(BASE, size=5 * MIB), b, 4 * MIB, audio=True)
    assert not cli.already_fits(replace(BASE, video_codec="hevc"), b, 4 * MIB, audio=True)
    assert not cli.already_fits(replace(BASE, path=Path("x.mov")), b, 4 * MIB, audio=True)
    assert not cli.already_fits(replace(BASE, width=3840, height=2160), b, 4 * MIB, audio=True)
    aac = replace(BASE, has_audio=True, audio_codec="aac", audio_channels=2)
    assert cli.already_fits(aac, b, 4 * MIB, audio=True)
    assert not cli.already_fits(aac, b, 4 * MIB, audio=False)  # dropping audio needs a re-encode
    assert not cli.already_fits(replace(aac, audio_codec="mp3"), b, 4 * MIB, audio=True)
    webm = replace(BASE, path=Path("x.webm"), video_codec="vp9", has_audio=True, audio_codec="opus")
    assert cli.already_fits(webm, b, 4 * MIB, audio=True)


def test_output_suffix_picks_codec():
    ap = cli.build_parser()
    b = BOARDS["4chan"]
    assert cli.codecs_for(ap.parse_args(["a.mkv", "-o", "b.webm"]), b) == ["vp9"]
    assert cli.codecs_for(ap.parse_args(["a.mkv"]), b) == ["x264", "vp9"]
    with pytest.raises(cli.BadInput):
        cli.codecs_for(ap.parse_args(["a.mkv", "-o", "b.mp4", "--codecs", "vp9"]), b)
    with pytest.raises(cli.BadInput):
        cli.codecs_for(ap.parse_args(["a.mkv", "-o", "b.mkv"]), b)
