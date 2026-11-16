import array
import io
import math
import os
import shutil
import subprocess
import wave
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
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_roundtrip(channels):
    data = sine(44100, channels, 2)
    out = _pydub2.encode(data, 44100, channels, 2, "flac")
    assert out[:4] == b"fLaC"
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s16le", "-"], input=out, capture_output=True, check=True)
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
    assert len(high) > len(low) * 1.5


@pytest.mark.parametrize("fmt", ["wav", "flac", "ogg", "opus"])
def test_empty(fmt):
    assert len(_pydub2.encode(b"", 44100, 2, 2, fmt)) > 0


def test_unknown_format():
    with pytest.raises(ValueError):
        _pydub2.encode(sine(), 44100, 1, 2, "mp3")


def pcm24(width=3, channels=1, secs=0.5, rate=44100):
    wide = np.frombuffer(sine(rate, channels, 4, secs), "<i4")
    return wide.astype("<i4").view(np.uint8).reshape(-1, 4)[:, 4 - width:].tobytes()


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_24_bit_like_ffmpeg(channels):
    data = pcm24(3, channels)
    out = _pydub2.encode(data, 44100, channels, 3, "flac")
    assert "s32" in probe(out) or "24" in probe(out)
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s32le", "-"], input=out, capture_output=True, check=True)
    assert np.frombuffer(p.stdout, "<i4").view(np.uint8).reshape(-1, 4)[:, 1:].tobytes() == data


def test_flac_24_bit_header():
    out = _pydub2.encode(pcm24(), 44100, 1, 3, "flac")
    # streaminfo: bits per sample minus one is 23
    assert ((out[20] & 1) << 4 | out[21] >> 4) + 1 == 24


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_8_bit_roundtrip(channels):
    data = sine(8000, channels, 1)
    out = _pydub2.encode(data, 8000, channels, 1, "flac")
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s16le", "-"], input=out, capture_output=True, check=True)
    assert np.frombuffer(p.stdout, "<i2").tolist() == [v * 256 for v in np.frombuffer(data, "i1").tolist()]


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("secs", [0, 0.5, 1 / 8000])
def test_wav_24_bit_like_wave_module(channels, secs):
    data = pcm24(3, channels, secs, 8000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(3)
        w.setframerate(8000)
        w.writeframes(data)
    assert _pydub2.encode(data, 8000, channels, 3, "wav") == buf.getvalue()


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_24_bit_same_samples_as_pydub(channels):
    pydub = pytest.importorskip("pydub")
    data = pcm24(3, channels)
    seg = pydub.AudioSegment(data, frame_rate=44100, channels=channels, sample_width=3)
    cmd = [ff, "-v", "error", "-i", "-", "-f", "s32le", "-"]
    theirs = subprocess.run(cmd, input=seg.export(format="flac").read(), capture_output=True, check=True).stdout
    mine = subprocess.run(cmd, input=_pydub2.encode(data, 44100, channels, 3, "flac"), capture_output=True,
                          check=True).stdout
    assert mine == theirs


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_lossy_24_bit_same_as_16(fmt):
    wide = pcm24(4, 2)
    out = _pydub2.encode(pcm24(3, 2), 44100, 2, 3, fmt)
    ref = _pydub2.encode(wide, 44100, 2, 4, fmt)
    back = ffdecode(out, 44100, 2)
    assert abs(len(back) - len(ffdecode(ref, 44100, 2))) < 2000
    assert best_corr(array.array("h", np.frombuffer(wide, "<i4")[::2] >> 16), back[::2]) > 0.95


@pytest.mark.parametrize("fmt", ["wav", "flac", "ogg", "opus"])
@pytest.mark.parametrize("width", [0, 5, -1, 8])
def test_bad_width(fmt, width):
    with pytest.raises(ValueError):
        _pydub2.encode(b"\0" * 80, 8000, 1, width, fmt)


def music(rate, channels, secs=3):
    rng = np.random.default_rng(5)
    t = np.arange(int(rate * secs)) / rate
    x = sum(np.sin(2 * np.pi * f * t) * (1 + np.sin(2 * np.pi * r * t)) for f, r in [(220, 3), (660, 5), (1500, 7), (3100, 2)])
    x = x / 8 + rng.normal(0, 0.02, len(t))
    y = np.stack([x, np.roll(x, 40)], 1)[:, :channels]
    return (y * 20000).astype("<i2").tobytes()


def pydub_export(data, rate, channels, width, fmt, bitrate=None):
    pydub = pytest.importorskip("pydub")
    seg = pydub.AudioSegment(data, frame_rate=rate, channels=channels, sample_width=width)
    return seg.export(format=fmt, bitrate=bitrate).read()


@needs_ff
@pytest.mark.parametrize("bitrate", [None, "64k", "128k", "192k"])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_lossy_size_like_ffmpeg(fmt, rate, channels, bitrate):
    data = music(rate, channels)
    theirs = len(pydub_export(data, rate, channels, 2, fmt, bitrate))
    mine = len(_pydub2.encode(data, rate, channels, 2, fmt, bitrate=int(bitrate[:-1]) * 1000 if bitrate else 0))
    assert abs(mine / theirs - 1) < 0.06


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_flac_size_like_ffmpeg(rate, channels):
    data = music(rate, channels)
    # ffmpeg adds 8 kB of padding that we leave out
    theirs = len(pydub_export(data, rate, channels, 2, "flac")) - 8192
    assert abs(len(_pydub2.encode(data, rate, channels, 2, "flac")) / theirs - 1) < 0.03


@needs_ff
@pytest.mark.parametrize("width", [1, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_8_and_32_bit_like_pydub(width, channels):
    data = pcm24(4, channels) if width == 4 else sine(8000, channels, 1)
    rate = 44100 if width == 4 else 8000
    cmd = [ff, "-v", "error", "-i", "-", "-f", "s32le", "-"]
    theirs = subprocess.run(cmd, input=pydub_export(data, rate, channels, width, "flac"), capture_output=True,
                            check=True).stdout
    mine = _pydub2.encode(data, rate, channels, width, "flac")
    assert subprocess.run(cmd, input=mine, capture_output=True, check=True).stdout == theirs
    bits = ((mine[20] & 1) << 4 | mine[21] >> 4) + 1
    assert bits == (24 if width == 4 else 16)


@needs_ff
def test_flac_streaminfo_like_ffmpeg():
    data = music(44100, 2)
    mine = _pydub2.encode(data, 44100, 2, 2, "flac")
    theirs = pydub_export(data, 44100, 2, 2, "flac")
    # block sizes, total samples and the md5 are the same
    assert mine[8:10] == theirs[8:10] and mine[10:12] == theirs[10:12]
    assert mine[20:26] == theirs[20:26]
    assert mine[26:42] == theirs[26:42] != bytes(16)


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [16000, 44100, 48000])
@pytest.mark.parametrize("frames", [5000, 12345, 30001])
def test_lossy_length_like_ffmpeg(fmt, rate, channels, frames):
    data = music(rate, channels)[:frames * 2 * channels]
    mine = _pydub2.decode(_pydub2.encode(data, rate, channels, 2, fmt))
    theirs = _pydub2.decode(pydub_export(data, rate, channels, 2, fmt))
    assert mine[1:] == theirs[1:]
    assert fmt == "ogg" or len(mine[0]) == len(theirs[0])


def test_opus_header_and_tags():
    out = _pydub2.encode(sine(48000, 1, 2, 0.1), 48000, 1, 2, "opus")
    assert out[28:36] == b"OpusHead" and int.from_bytes(out[38:40], "little") == 312
    # no padding after the vendor string, like ffmpeg
    assert bytes(64) not in out[:300]


@pytest.mark.parametrize("frames", [1, 5000, 12345, 30001])
def test_vorbis_last_granule_is_the_input_length(frames):
    out = _pydub2.encode(sine(44100, 1, 2, 1)[:frames * 2], 44100, 1, 2, "ogg")
    assert int.from_bytes(out[out.rfind(b"OggS") + 6:][:8], "little") == frames
