import importlib.metadata
import io
import json
import math
import os
import shutil
import subprocess

import numpy as np
import pytest

from pydub2 import AudioSegment, _ffmpeg, _mp3

pydub = pytest.importorskip("pydub")
has_mp3 = pytest.mark.skipif(not _mp3.available(), reason="pydub2-mp3 not installed")
has_ff = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


def chirp(rate=44100, channels=2, width=2, secs=2.0):
    t = np.arange(int(rate * secs)) / rate
    x = 0.4 * np.sin(2 * np.pi * (200 + 2000 * t) * t) + 0.05 * np.random.default_rng(1).standard_normal(len(t))
    x = np.repeat(x[:, None], channels, 1)
    if channels == 2:
        x[:, 1] *= 0.7
    peak = 2 ** (width * 8 - 1) - 1
    s = (x * peak).astype(np.int32)
    if width == 3:
        raw = s.astype("<i4").tobytes()
        raw = b"".join(raw[i:i + 3] for i in range(0, len(raw), 4))
    else:
        raw = s.astype({1: "i1", 2: "<i2", 4: "<i4"}[width]).tobytes()
    return AudioSegment(raw, frame_rate=rate, channels=channels, sample_width=width)


def oracle(seg):
    return pydub.AudioSegment(seg.raw_data, frame_rate=seg.frame_rate, channels=seg.channels,
                              sample_width=seg.sample_width)


def decode(mp3):
    s = pydub.AudioSegment.from_file(io.BytesIO(mp3), format="mp3")
    return s, np.frombuffer(s.set_sample_width(2).raw_data, np.int16).astype(float)


def snr(a, b):
    assert len(a) == len(b)
    return 10 * math.log10((a ** 2).sum() / max(((a - b) ** 2).sum(), 1e-9))


def ours(seg, **kw):
    return _mp3.encode(seg.raw_data, seg.frame_rate, seg.channels, seg.sample_width, **kw)


cases = [
    ("default", {}, {}),
    ("128k", {}, {"bitrate": "128k"}),
    ("320k", {}, {"bitrate": "320k"}),
    ("b:a", {}, {"parameters": ["-b:a", "192k"]}),
    ("q:a 2", {}, {"parameters": ["-q:a", "2"]}),
    ("qscale:a 0", {}, {"parameters": ["-qscale:a", "0"]}),
    ("mono", {"channels": 1}, {}),
    ("mono 22050", {"channels": 1, "rate": 22050}, {"bitrate": "64k"}),
    ("48000", {"rate": 48000}, {}),
    ("8 bit", {"width": 1}, {}),
    ("24 bit", {"width": 3}, {}),
    ("32 bit", {"width": 4}, {}),
]
# measured on windows-latest against the gyan.dev ffmpeg, which has lame 3.100 too
windows_vbr = {"q:a 2": 50.1, "qscale:a 0": 39.7}


@has_mp3
@has_ff
@pytest.mark.parametrize("name,make,kw", cases, ids=[c[0] for c in cases])
def test_same_as_ffmpeg(name, make, kw, request):
    if os.name == "nt" and name in windows_vbr:
        request.applymarker(pytest.mark.xfail(strict=True, reason="vbr from the MSVC build is %s dB from ffmpeg's gcc build"
                                              % windows_vbr[name]))
    seg = chirp(**make)
    ref, a = decode(oracle(seg).export(format="mp3", **kw).read())
    got, b = decode(ours(seg, **kw))
    assert (got.frame_rate, got.channels) == (ref.frame_rate, ref.channels)
    assert len(b) == len(a) == len(seg.raw_data) // seg.sample_width
    assert snr(a, b) > 60


@has_mp3
def test_versions():
    import pydub2_mp3
    assert pydub2_mp3.lame_version == "3.100"
    assert pydub2_mp3.__version__ == importlib.metadata.version("pydub2-mp3")


@has_mp3
@has_ff
def test_five_seconds_same_as_ffmpeg():
    seg = chirp(secs=5.0)
    _, a = decode(oracle(seg).export(format="mp3", bitrate="192k").read())
    _, b = decode(seg.export(format="mp3", bitrate="192k").read())
    assert snr(a, b) > 60


# resamplers differ per ffmpeg build
conversions = [
    ("ac 1", {}, ["-ac", "1"], 20),
    ("ac 2", {"channels": 1}, ["-ac", "2"], 25),
    ("ar 22050", {}, ["-ar", "22050"], 12),
    ("96k input", {"rate": 96000}, None, 17),
    ("ar with q:a", {"rate": 32000}, ["-ar", "48000", "-q:a", "2"], 20),
]


@has_mp3
@has_ff
@pytest.mark.parametrize("name,make,params,least", conversions, ids=[c[0] for c in conversions])
def test_conversions_like_ffmpeg(name, make, params, least):
    seg = chirp(**make)
    ref, a = decode(oracle(seg).export(format="mp3", parameters=params).read())
    got, b = decode(ours(seg, parameters=params))
    assert (got.frame_rate, got.channels) == (ref.frame_rate, ref.channels)
    # different resamplers, a frame or two either way
    assert abs(len(a) - len(b)) <= 2 * got.channels
    n = min(len(a), len(b))
    # two encodes of a slightly different input differ by the coding noise, so only check it is the same sound
    assert snr(a[:n], b[:n]) > least


# the chirp stays below the new nyquist, so the snr above passes a resampler without a low-pass too
# no ffmpeg needed, so this also runs in the wheel check
@has_mp3
@pytest.mark.parametrize("rate,tone,params", [(44100, 15000, ["-ar", "22050"]), (48000, 15000, ["-ar", "22050"]),
                                              (96000, 40000, None)])
def test_downsampling_filters_out_the_alias(rate, tone, params):
    t = np.arange(rate) / rate
    # 1 khz must come through, the tone above the new nyquist must not
    x = 0.25 * np.sin(2 * np.pi * 1000 * t) + 0.25 * np.sin(2 * np.pi * tone * t)
    seg = AudioSegment((x * 32767).astype("<i2").tobytes(), frame_rate=rate, channels=1, sample_width=2)
    got = AudioSegment.from_file(io.BytesIO(ours(seg, parameters=params)), format="mp3")
    assert got.frame_rate < 2 * tone
    b = np.frombuffer(got.raw_data, np.int16).astype(float)
    spec = np.abs(np.fft.rfft(b * np.hanning(len(b)))) / (len(b) / 4)
    f = np.fft.rfftfreq(len(b), 1 / got.frame_rate)
    kept = spec[abs(f - 1000) < 50].max()
    alias = spec[abs(f - (got.frame_rate - tone)) < 50].max()
    assert kept > 0.5 * 0.25 * 32767
    # without a low-pass the alias is about as loud as the 1 khz tone, with it more than 90 db below
    assert alias < kept * 1e-3


@has_mp3
@pytest.mark.parametrize("v", ["3", "4"])
def test_tags(v):
    tags = {"title": "Tïtle", "artist": "Ärtist 日本", "album": "Alb", "date": "2020", "genre": "Rock",
            "track": "3/9", "comment": "hi", "foo": "bar"}
    out = chirp(secs=0.5).export(format="mp3", tags=tags, id3v2_version=v).read()
    assert out[:3] == b"ID3"
    if shutil.which("ffprobe"):
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", "-"],
                           input=out, capture_output=True, check=True)
        got = json.loads(p.stdout)["format"]["tags"]
        for k, val in tags.items():
            assert got[k] == val


@has_mp3
def test_does_not_use_ffmpeg(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("ffmpeg was called")
    monkeypatch.setattr(_ffmpeg, "from_wav", boom)
    seg = chirp(secs=1)
    out = seg.export(format="mp3", parameters=["-q:a", "2"]).read()
    back = AudioSegment.from_file(io.BytesIO(out), format="mp3")
    assert len(back.raw_data) == len(seg.raw_data)


gapless = [(r, c, b) for r in (8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000) for c in (1, 2)
           for b in (None, "32k", "64k", "128k", "192k")]


@has_mp3
@pytest.mark.parametrize("rate,channels,bitrate", gapless)
def test_length_survives_low_bitrates(rate, channels, bitrate, monkeypatch):
    monkeypatch.setattr(_ffmpeg, "find", lambda converter: None)
    seg = chirp(rate, channels, secs=0.5)
    mp3 = ours(seg, bitrate=bitrate)
    assert len(AudioSegment.from_file(io.BytesIO(mp3), format="mp3").raw_data) == len(seg.raw_data)
    if shutil.which("ffmpeg"):
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "s16le", "-"], input=mp3,
                           capture_output=True, check=True)
        assert len(p.stdout) == len(seg.raw_data)


@has_mp3
def test_empty():
    # only the info frame, ffmpeg cant read its own empty mp3 either
    out = AudioSegment.silent(0, frame_rate=44100).export(format="mp3").read()
    assert out[:2] == b"\xff\xfb" and b"Info" in out


@has_mp3
def test_falls_back_to_ffmpeg():
    seg = chirp(secs=0.2)
    assert ours(AudioSegment.from_mono_audiosegments(*[seg.set_channels(1)] * 6)) is None
    assert ours(seg, parameters=["-compression_level", "2"]) is None
    assert ours(seg, parameters=["-ac", "6"]) is None


def test_error_says_what_to_install(monkeypatch):
    monkeypatch.setattr(_mp3, "available", lambda: False)
    monkeypatch.setattr(_ffmpeg, "find", lambda converter: None)
    with pytest.raises(FileNotFoundError, match="pip install pydub2-mp3"):
        AudioSegment.silent(100).export(format="mp3")
