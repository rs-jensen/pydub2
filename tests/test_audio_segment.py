import io
import math
import os
import shutil
import subprocess
import wave

import pytest

pydub = pytest.importorskip("pydub")
import pydub2

ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
need_ffmpeg = pytest.mark.skipif(not os.path.exists(ff), reason="no ffmpeg")


def make_wav(width, channels, rate=8000, secs=1.0):
    peak = 2 ** (8 * width - 1) - 1
    buf = bytearray()
    for i in range(int(rate * secs)):
        for c in range(channels):
            v = int(peak * 0.6 * math.sin(i * 0.05 * (c + 1)) + peak * 0.3 * math.sin(i * 0.31))
            if width == 1:
                buf += bytes([(v + 128) & 0xff])
            else:
                buf += v.to_bytes(width, "little", signed=True)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    return out.getvalue()


def same(a, b):
    assert a.raw_data == b.raw_data
    assert len(a) == len(b)
    assert (a.frame_rate, a.channels, a.sample_width) == (b.frame_rate, b.channels, b.sample_width)
    assert (a.dBFS, a.max, a.rms, a.max_dBFS) == (b.dBFS, b.max, b.rms, b.max_dBFS)


formats = [(w, c) for w in (1, 2, 4) for c in (1, 2)]

chains = {
    "slice": lambda S, s: s[13:577],
    "slice_neg": lambda S, s: s[-333:-17],
    "slice_open": lambda S, s: s[999:],
    "index": lambda S, s: s[501],
    "add": lambda S, s: s[:300] + s[400:701],
    "gain": lambda S, s: s + 3.5,
    "sub": lambda S, s: s - 7,
    "times": lambda S, s: s[:123] * 3,
    "overlay": lambda S, s: s.overlay(s.reverse()[:211], position=77),
    "overlay_loop": lambda S, s: s.overlay(s[:97], loop=True),
    "overlay_times": lambda S, s: s.overlay(s[:97] - 6, times=3, gain_during_overlay=-4),
    "mul_seg": lambda S, s: s * s[:150],
    "fade_in": lambda S, s: s.fade_in(250),
    "fade_out": lambda S, s: s.fade_out(57),
    "fade": lambda S, s: s.fade(to_gain=-20, start=100, end=400),
    "apply_gain": lambda S, s: s.apply_gain(-12.3),
    "reverse": lambda S, s: s.reverse(),
    "rate_up": lambda S, s: s.set_frame_rate(11025),
    "rate_down": lambda S, s: s.set_frame_rate(5000),
    "mono": lambda S, s: s.set_channels(1),
    "stereo": lambda S, s: s.set_channels(2),
    "width1": lambda S, s: s.set_sample_width(1),
    "width2": lambda S, s: s.set_sample_width(2),
    "width4": lambda S, s: s.set_sample_width(4),
    "split": lambda S, s: s.split_to_mono()[-1],
    "crossfade": lambda S, s: s[:600].append(s[300:], crossfade=120),
    "append": lambda S, s: s[:600].append(S.silent(200, s.frame_rate)),
    "sum": lambda S, s: sum([s[:100], s[200:300]]),
    "silent": lambda S, s: S.silent(333, 22050) + s,
    "empty": lambda S, s: S.empty() + s,
    "raw": lambda S, s: S(s.raw_data, sample_width=s.sample_width, frame_rate=s.frame_rate, channels=s.channels),
    "array": lambda S, s: S(s.get_array_of_samples(), sample_width=s.sample_width, frame_rate=s.frame_rate,
                            channels=s.channels),
    "from_raw": lambda S, s: S.from_raw(io.BytesIO(s.raw_data), sample_width=s.sample_width,
                                        frame_rate=s.frame_rate, channels=s.channels),
    "wav_roundtrip": lambda S, s: S.from_file(s.export(format="wav"), format="wav"),
    "wav_start": lambda S, s: S.from_file(s.export(format="wav"), format="wav", start_second=0.25, duration=0.5),
}


@pytest.mark.parametrize("width,channels", formats)
@pytest.mark.parametrize("name", list(chains))
def test_chain(width, channels, name):
    wav = make_wav(width, channels)
    a = chains[name](pydub.AudioSegment, pydub.AudioSegment(wav))
    b = chains[name](pydub2.AudioSegment, pydub2.AudioSegment(wav))
    same(a, b)


@pytest.mark.parametrize("width,channels", formats)
def test_basics(width, channels):
    wav = make_wav(width, channels)
    a = pydub.AudioSegment(wav)
    b = pydub2.AudioSegment(wav)
    same(a, b)
    same(pydub.AudioSegment(io.BytesIO(wav)), pydub2.AudioSegment(io.BytesIO(wav)))
    assert a.get_array_of_samples() == b.get_array_of_samples()
    assert a.get_array_of_samples().typecode == b.get_array_of_samples().typecode
    assert a.export(format="wav").read() == b.export(format="wav").read()
    assert a.export(format="raw").read() == b.export(format="raw").read()
    assert len(list(a[:20])) == len(list(b[:20]))
    assert b == pydub2.AudioSegment(wav)
    assert b != b[:10]
    assert hash(b) == hash(pydub2.AudioSegment(wav))


def test_resample_quality():
    s = pydub2.AudioSegment(make_wav(2, 2))
    assert s.set_frame_rate(16000).raw_data == pydub.AudioSegment(make_wav(2, 2)).set_frame_rate(16000).raw_data
    hq = s.set_frame_rate(16000, resample_quality="high")
    assert hq.frame_rate == 16000 and abs(len(hq) - len(s)) <= 1
    assert hq.raw_data != s.set_frame_rate(16000).raw_data


def test_loudness():
    s = pydub2.AudioSegment(make_wav(2, 2, rate=48000, secs=2))
    assert -30 < s.loudness() < 0
    assert abs((s - 6).loudness() - (s.loudness() - 6)) < 0.1


@need_ffmpeg
@pytest.mark.parametrize("ext", ["wav", "flac", "mp3", "ogg"])
def test_from_file(tmp_path, ext):
    src = tmp_path / "src.wav"
    src.write_bytes(make_wav(2, 2, rate=44100))
    path = tmp_path / ("x." + ext)
    subprocess.run([ff, "-y", "-v", "quiet", "-i", str(src), str(path)], check=True)
    a = pydub.AudioSegment.from_file(str(path))
    for b in (pydub2.AudioSegment.from_file(str(path)), pydub2.AudioSegment.from_file(path),
              pydub2.AudioSegment.from_file(io.BytesIO(path.read_bytes()), format=ext)):
        assert (a.frame_rate, a.channels, a.sample_width) == (b.frame_rate, b.channels, b.sample_width)
        if ext in ("wav", "flac"):
            same(a, b)
        else:
            assert abs(len(a) - len(b)) <= 30
            assert abs(a.dBFS - b.dBFS) < 0.5


@need_ffmpeg
def test_from_file_ffmpeg_args(tmp_path):
    path = tmp_path / "x.mp3"
    pydub.AudioSegment(make_wav(2, 2, rate=44100)).export(str(path), format="mp3")
    a = pydub.AudioSegment.from_file(str(path), start_second=0.2, duration=0.3)
    b = pydub2.AudioSegment.from_file(str(path), start_second=0.2, duration=0.3)
    same(a, b)


@need_ffmpeg
def test_export_mp3(tmp_path):
    wav = make_wav(2, 2, rate=44100)
    a = pydub.AudioSegment(wav).export(format="mp3", bitrate="128k").read()
    b = pydub2.AudioSegment(wav).export(format="mp3", bitrate="128k").read()
    assert a == b
    p = tmp_path / "x.mp3"
    pydub2.AudioSegment(wav).export(str(p), format="mp3", tags={"title": "x"})
    assert abs(len(pydub2.AudioSegment.from_file(str(p))) - 1000) < 60


@pytest.mark.parametrize("fmt", ["flac", "ogg", "opus"])
def test_export_native(fmt):
    s = pydub2.AudioSegment(make_wav(2, 2, rate=48000))
    out = pydub2.AudioSegment.from_file(s.export(format=fmt, bitrate="96k"), format=fmt)
    assert out.channels == 2 and abs(len(out) - len(s)) <= 30
    if fmt == "flac":
        same(s, out)


def test_export_bitrate_units():
    s = pydub2.AudioSegment(make_wav(2, 2, rate=48000))
    sizes = [len(s.export(format="opus", bitrate=b).read()) for b in ("0.064M", "64K", 64000, None)]
    assert sizes[0] == sizes[1] == sizes[2] < sizes[3]


def test_from_file_bytes_path(tmp_path):
    path = tmp_path / "x.flac"
    s = pydub2.AudioSegment(make_wav(2, 2))
    s.export(str(path), format="flac").close()
    same(s, pydub2.AudioSegment.from_file(str(path).encode()))


def test_from_file_rewinds_wav():
    wav = make_wav(2, 2)
    f = io.BytesIO(wav)
    f.seek(10)
    same(pydub.AudioSegment.from_file(io.BytesIO(wav), format="wav"), pydub2.AudioSegment.from_file(f, format="wav"))
