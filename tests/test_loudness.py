import io
import math
import re
import shutil
import struct
import subprocess
import wave

import pytest

import _pydub2

ff = shutil.which("ffmpeg") or "ffmpeg"


def sine(amp, rate, channels, width, secs=3.0):
    top = 2 ** (8 * width - 1) - 1
    n = int(rate * secs)
    vals = [int(amp * top * math.sin(2 * math.pi * 1000 * i / rate)) for i in range(n)]
    fmt = {1: "b", 2: "h", 4: "i"}[width]
    frame = [v for v in vals for _ in range(channels)]
    return struct.pack("<%d%s" % (len(frame), fmt), *frame)


def wav(raw, rate, channels, width):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(raw)
    return buf.getvalue()


def ffmpeg_lufs(tmp_path, raw, rate, channels, width):
    path = tmp_path / "x.wav"
    path.write_bytes(wav(raw, rate, channels, width))
    res = subprocess.run([ff, "-nostats", "-i", str(path), "-filter_complex", "ebur128",
                          "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"Integrated loudness:\s+I:\s+(-?\d+\.\d+) LUFS", res.stderr).group(1))


needs_ffmpeg = pytest.mark.skipif(shutil.which(ff) is None, reason="no ffmpeg")


@needs_ffmpeg
@pytest.mark.parametrize("amp", [0.1, 0.5])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("width", [2, 4])
@pytest.mark.parametrize("rate", [44100, 48000])
def test_matches_ffmpeg(tmp_path, amp, channels, width, rate):
    raw = sine(amp, rate, channels, width)
    assert abs(_pydub2.loudness(raw, rate, channels, width)
               - ffmpeg_lufs(tmp_path, raw, rate, channels, width)) < 0.1


def test_silence_is_minus_inf():
    raw = b"\0" * (44100 * 2 * 2)
    assert _pydub2.loudness(raw, 44100, 2, 2) == -math.inf


def test_short_is_minus_inf():
    raw = sine(0.5, 44100, 1, 2, secs=0.3)
    assert _pydub2.loudness(raw, 44100, 1, 2) == -math.inf


def test_halving_amplitude_drops_6db():
    loud = _pydub2.loudness(sine(0.5, 44100, 2, 2), 44100, 2, 2)
    quiet = _pydub2.loudness(sine(0.25, 44100, 2, 2), 44100, 2, 2)
    assert abs((loud - quiet) - 6.02) < 0.05


def test_8bit_close_to_16bit():
    loud = _pydub2.loudness(sine(0.5, 44100, 1, 1), 44100, 1, 1)
    ref = _pydub2.loudness(sine(0.5, 44100, 1, 2), 44100, 1, 2)
    assert abs(loud - ref) < 0.3
