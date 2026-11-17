import io
import pathlib
import shutil
import struct
import warnings
import zlib

import numpy as np
import pytest

import _pydub2
import pydub2
from pydub2 import silence, utils
from pydub2.exceptions import CouldntEncodeError
from pydub2.generators import Sine

ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
needs_ff = pytest.mark.skipif(not pathlib.Path(ff).exists(), reason="no ffmpeg")


def tone(ms=1000, channels=1, rate=8000):
    return Sine(440, sample_rate=rate).to_audio_segment(duration=ms).set_channels(channels)


def test_mid_side():
    seg = pydub2.AudioSegment(struct.pack("<8h", 1000, 300, -2000, 500, 3000, -3000, 0, 100) * 20,
                              sample_width=2, frame_rate=8000, channels=2)
    ms = utils.stereo_to_ms(seg)
    assert ms.channels == 2 and len(ms) == len(seg)
    assert list(ms.get_array_of_samples())[:8] == [1300, 700, -1500, -2500, 0, 6000, 100, -100]
    assert list(utils.ms_to_stereo(ms).get_array_of_samples())[:8] == [1415, 424, -2832, 707, 4247, -4248, 0, 141]


@needs_ff
def test_int_bitrate():
    out = tone().export(format="mp3", codec="libmp3lame", bitrate=128000)
    assert len(out.read()) > 1000


def test_format_from_extension(tmp_path):
    p = tmp_path / "x.flac"
    tone().export(p)
    assert p.read_bytes()[:4] == b"fLaC"
    p = tmp_path / "x.wav"
    tone().export(str(p))
    assert p.read_bytes()[:4] == b"RIFF"


@needs_ff
def test_format_default_is_still_mp3(tmp_path):
    p = tmp_path / "x.unknown"
    tone().export(p).close()
    assert p.read_bytes()[:3] == b"ID3" or p.read_bytes()[0] == 0xFF
    data = tone().export(io.BytesIO()).read()
    assert data[:3] == b"ID3" or data[0] == 0xFF


def png():
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))


@needs_ff
def test_cover_path(tmp_path):
    cover = tmp_path / "c.png"
    cover.write_bytes(png())
    out = tone().export(format="mp3", cover=cover).read()
    assert b"APIC" in out


def test_export_pcm():
    seg = tone(100)
    assert seg.export(format="pcm").read() == seg.raw_data


def test_no_syntax_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(pathlib.Path(utils.__file__).read_text(), utils.__file__, "exec")


def test_speedup_same_as_pydub(monkeypatch):
    old = pytest.importorskip("pydub")
    for rate, ch, width in [(8000, 1, 2), (11025, 2, 2), (8000, 1, 1)]:
        seg = tone(3000, ch, rate).set_sample_width(width)
        ref = old.AudioSegment(seg.raw_data, sample_width=width, frame_rate=rate, channels=ch)
        for args in [(1.5, 150, 25), (1.1, 150, 25), (2.0, 150, 25), (3.0, 150, 25), (1.5, 150, 0),
                     (1.5, 37, 13), (1.5, 150, 100)]:
            assert seg.speedup(*args).raw_data == ref.speedup(*args).raw_data

    def boom(*a, **k):
        raise AssertionError("speedup must not chain append")
    monkeypatch.setattr(pydub2.AudioSegment, "append", boom)
    tone(3000).speedup()


def test_from_mono_fill_up():
    a, b = tone(100), tone(150)
    with pytest.raises(ValueError):
        pydub2.AudioSegment.from_mono_audiosegments(a, b)
    out = pydub2.AudioSegment.from_mono_audiosegments(a, b, fill_up=True)
    assert out.channels == 2 and len(out) == 150
    left, right = out.split_to_mono()
    assert left.raw_data[:len(a.raw_data)] == a.raw_data
    assert set(left.raw_data[len(a.raw_data):]) == {0}
    assert right.raw_data == b.raw_data


@needs_ff
@pytest.mark.parametrize("ext,magic", [("m4a", b"ftyp"), ("aac", b"\xff\xf1")])
def test_export_m4a_aac(tmp_path, ext, magic):
    p = tmp_path / ("x." + ext)
    tone(500, 2, 44100).export(p)
    assert magic in p.read_bytes()[:12]


def test_wav_too_long(monkeypatch):
    def too_long(*a):
        raise RuntimeError("too long for wav")
    monkeypatch.setattr(_pydub2, "encode", too_long)
    monkeypatch.setattr(_pydub2, "wav_head", too_long)
    with pytest.raises(CouldntEncodeError):
        tone(100).export(format="wav")


@pytest.mark.parametrize("kw", [dict(), dict(keep_silence=0)])
def test_split_return_ranges(kw):
    seg = tone(300) + AudioSilence(600) + tone(300)
    plain = silence.split_on_silence(seg, min_silence_len=300, silence_thresh=-30, **kw)
    both = silence.split_on_silence(seg, min_silence_len=300, silence_thresh=-30, return_ranges=True, **kw)
    assert [c for c, _ in both] == plain and len(both) == 2
    for chunk, (start, end) in both:
        assert seg[start:end] == chunk


def AudioSilence(ms):
    return pydub2.AudioSegment.silent(ms, 8000)


def test_split_on_speech_return_ranges(monkeypatch):
    monkeypatch.setattr(silence, "detect_speech", lambda *a: [[100, 200], [400, 500]])
    seg = tone(600)
    out = silence.split_on_speech(seg, keep_silence=0, return_ranges=True)
    assert [r for _, r in out] == [(100, 200), (400, 500)]
    assert out[0][0] == seg[100:200]


def test_numpy_round_trip():
    seg = tone(100, 2)
    arr = seg.to_numpy()
    assert arr.shape == (800, 2) and arr.dtype == np.int16
    back = pydub2.AudioSegment.from_numpy(arr, 8000)
    assert back.raw_data == seg.raw_data and back.channels == 2 and back.frame_rate == 8000
    assert pydub2.AudioSegment.from_numpy(arr[:, 0], 8000).channels == 1
    assert pydub2.AudioSegment.from_numpy(arr.astype(np.int32), 8000).sample_width == 4
    assert pydub2.AudioSegment.from_numpy(arr.astype(np.int8), 8000).sample_width == 1


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_numpy_float(dtype):
    seg = pydub2.AudioSegment.from_numpy(np.array([0, 0.5, -1, 3, -3], dtype=dtype), 8000)
    assert seg.sample_width == 2
    assert list(seg.get_array_of_samples()) == [0, 16384, -32767, 32767, -32767]
