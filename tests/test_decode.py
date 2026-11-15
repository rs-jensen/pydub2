import array
import math
import os
import random
import struct
import subprocess

import pytest

import _pydub2

ff = "/opt/homebrew/bin/ffmpeg"
if not os.path.exists(ff):
    ff = "ffmpeg"
try:
    subprocess.run([ff, "-version"], capture_output=True, check=True)
except (OSError, subprocess.CalledProcessError):
    pytest.skip("no ffmpeg", allow_module_level=True)

AudioSegment = pytest.importorskip("pydub").AudioSegment

RATE = 44100
random.seed(1)
SAMPLES = [0.5 * math.sin(i * 0.06) + 0.1 * random.uniform(-1, 1) for i in range(RATE // 2)]


def make(tmp_path, name, channels, *args):
    raw = b"".join(struct.pack("<f", x * (1 if c == 0 else 0.7)) for x in SAMPLES for c in range(2))
    path = str(tmp_path / name)
    cmd = [ff, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", "-",
           "-ac", str(channels), *args, path]
    subprocess.run(cmd, input=raw, check=True)
    return path


def both(path):
    with open(path, "rb") as f:
        data, rate, channels, width = _pydub2.decode(f.read())
    a = AudioSegment.from_file(path)
    assert (rate, channels, width) == (a.frame_rate, a.channels, a.sample_width)
    return data, a.raw_data, width


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("codec", ["pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"])
def test_wav(tmp_path, codec, channels):
    mine, theirs, width = both(make(tmp_path, "a.wav", channels, "-c:a", codec))
    assert mine == theirs


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("fmt", ["s16", "s32"])
def test_flac(tmp_path, fmt, channels):
    mine, theirs, width = both(make(tmp_path, "a.flac", channels, "-c:a", "flac", "-sample_fmt", fmt))
    assert mine == theirs


def lossy(tmp_path, name, channels, limit, *args, slack=0):
    mine, theirs, width = both(make(tmp_path, name, channels, *args))
    # ffmpeg keeps the last vorbis packet when the granule ends before it
    assert 0 <= len(theirs) - len(mine) <= slack * channels * width
    theirs = theirs[:len(mine)]
    code = "h" if width == 2 else "i"
    a = array.array(code, mine)
    b = array.array(code, theirs)
    worst = max(abs(x - y) for x, y in zip(a, b))
    print(name, channels, len(mine), worst)
    assert worst <= limit * (1 << (8 * width - 16))


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("args", [["-b:a", "128k"], ["-q:a", "4"], ["-b:a", "128k", "-write_xing", "0"]])
def test_mp3(tmp_path, args, channels):
    lossy(tmp_path, "a.mp3", channels, 2, "-c:a", "libmp3lame", *args)


@pytest.mark.parametrize("channels", [1, 2])
def test_vorbis(tmp_path, channels):
    lossy(tmp_path, "a.ogg", channels, 1, "-c:a", "libvorbis", slack=1024)


@pytest.mark.parametrize("channels", [1, 2])
def test_opus(tmp_path, channels):
    lossy(tmp_path, "a.opus", channels, 1, "-c:a", "libopus")


def test_opus_rate(tmp_path):
    with open(make(tmp_path, "a.opus", 2, "-c:a", "libopus"), "rb") as f:
        assert _pydub2.decode(f.read())[1] == 48000


@pytest.mark.parametrize("junk", [b"", b"abc", bytes(range(256)) * 40, b"RIFF\0\0\0\0WAVE" + b"x" * 100,
                                  b"OggS" + b"\0" * 100, b"fLaC" + b"\0" * 100])
def test_garbage(junk):
    with pytest.raises(RuntimeError):
        _pydub2.decode(junk)
