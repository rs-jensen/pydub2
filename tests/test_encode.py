import array
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

import _pydub2

ff = "/opt/homebrew/bin/ffmpeg"
if not os.path.exists(ff):
    ff = shutil.which("ffmpeg")
needs_ff = pytest.mark.skipif(ff is None, reason="no ffmpeg")

codes = {1: "b", 2: "h", 4: "i"}


def sine(rate=44100, channels=1, width=2, secs=1.0, freq=440):
    peak = 2 ** (width * 8 - 1) - 1
    n = int(rate * secs)
    s = [int(peak * 0.5 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
    return array.array(codes[width], [v for v in s for _ in range(channels)]).tobytes()


def ffdecode(data, rate, channels):
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-"],
                       input=data, capture_output=True, check=True)
    return array.array("h", p.stdout)


def probe(data):
    p = subprocess.run([ff, "-i", "-"], input=data, capture_output=True)
    return p.stderr.decode()


def best_corr(x, y):
    # encoders add a little delay at the start, so try a few lags
    a = np.array(x[:8000], float)
    b = np.array(y[:12000], float)
    c = np.correlate(b, a, "valid")
    return c.max() / np.sqrt((a * a).sum() * (b[c.argmax():c.argmax() + 8000] ** 2).sum())


@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("secs", [0, 0.5, 1 / 8000])
def test_wav_like_pydub(width, channels, secs):
    pydub = pytest.importorskip("pydub")
    data = sine(8000, channels, width, secs)
    if len(data) % 2 and sys.version_info < (3, 15):
        pytest.skip("wave only pads odd data from 3.15")
    seg = pydub.AudioSegment(data, frame_rate=8000, channels=channels, sample_width=width)
    assert _pydub2.encode(data, 8000, channels, width, "wav") == seg.export(format="wav").read()


@needs_ff
@pytest.mark.parametrize("width,fmt", [(2, "s16le"), (4, "s32le")])
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_roundtrip(width, fmt, channels):
    data = sine(44100, channels, width)
    out = _pydub2.encode(data, 44100, channels, width, "flac")
    assert out[:4] == b"fLaC"
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", fmt, "-"], input=out, capture_output=True, check=True)
    assert p.stdout == data


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [44100, 22050])
def test_lossy(fmt, channels, rate):
    data = sine(rate, channels)
    out = _pydub2.encode(data, rate, channels, 2, fmt)
    info = probe(out)
    assert ("vorbis" if fmt == "ogg" else "opus") in info
    # opus always decodes at 48k
    assert "%d Hz, %s" % (rate if fmt == "ogg" else 48000, ["mono", "stereo"][channels - 1]) in info
    back = ffdecode(out, rate, channels)
    assert abs(len(back) - len(data) // 2) < rate * channels // 20
    assert best_corr(array.array("h", data)[::channels], back[::channels]) > 0.95


@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_bitrate_changes_size(fmt):
    data = sine(48000, 2, secs=2, freq=3000)
    low = _pydub2.encode(data, 48000, 2, 2, fmt, bitrate=48000)
    high = _pydub2.encode(data, 48000, 2, 2, fmt, bitrate=256000)
    assert len(high) > len(low) * 2


@pytest.mark.parametrize("fmt", ["wav", "flac", "ogg", "opus"])
def test_empty(fmt):
    assert len(_pydub2.encode(b"", 44100, 2, 2, fmt)) > 0


def test_unknown_format():
    with pytest.raises(ValueError):
        _pydub2.encode(sine(), 44100, 1, 2, "mp3")
