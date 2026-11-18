import decimal
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
from pydub2 import effects, silence, utils
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


def test_raw_24_bit_with_stray_bytes():
    seg = pydub2.AudioSegment.from_file(io.BytesIO(bytes(range(74))), format="raw", sample_width=3, frame_rate=96000,
                                        channels=2)
    assert len(seg.raw_data) == 96 and seg.sample_width == 4


def partial_frame():
    # 70 whole frames of 3 channels and 2 stray bytes
    mono = pydub2.AudioSegment(struct.pack("<h", 10) * 210, sample_width=2, frame_rate=1000, channels=1)
    return mono._spawn(struct.pack("<h", 10) * 212, {"channels": 3, "frame_width": 6})


def test_partial_frame_set_channels():
    assert len(partial_frame().set_channels(1).raw_data) == 140


@pytest.mark.parametrize("name", ["low_pass_filter", "high_pass_filter"])
def test_partial_frame_filter(name):
    seg = partial_frame()
    # the function, not the method: importing scipy_effects replaces the methods
    out = getattr(effects, name)(seg, 100)
    assert len(out.raw_data) == 424 and out.raw_data[-2:] == seg.raw_data[-2:]


def test_partial_frame_compress():
    assert len(partial_frame().compress_dynamic_range().raw_data) == 420


def test_partial_frame_detect_silence():
    assert silence.detect_nonsilent(partial_frame(), min_silence_len=10, silence_thresh=-90) == [[0, 70]]


def test_compress_nothing_with_odd_width():
    seg = pydub2.AudioSegment(b"", sample_width=5, frame_rate=8000, channels=1)
    assert seg.compress_dynamic_range().raw_data == b""


@pytest.mark.parametrize("step", [2 ** 31, 2 ** 40, 2 ** 63])
def test_silence_seek_step_past_the_end(step):
    seg = pydub2.AudioSegment(bytes(2000) + b"\x10\x20" * 500, sample_width=2, frame_rate=8000, channels=1)
    assert silence.detect_silence(seg, 10, -16, step) == silence.detect_silence(seg, 10, -16, 1000)
    assert silence.detect_silence(seg, 10, -16, step) == [[0, 10]]
    assert silence.detect_nonsilent(seg, 10, -16, step) == [[10, len(seg)]]


@pytest.mark.parametrize("n", [2 ** 31, 2 ** 63, -2 ** 31 - 1])
def test_huge_rate_and_width_raise_overflow(n):
    seg = tone(100)
    with pytest.raises(OverflowError):
        seg.set_frame_rate(n)
    with pytest.raises(OverflowError):
        seg.set_sample_width(n)


def test_eq_with_nan_gain():
    pytest.importorskip("scipy")
    from pydub2 import scipy_effects
    seg = tone(200, channels=2)
    for mode in ("L+R", "M+S"):
        with pytest.raises(ValueError):
            scipy_effects.eq(seg, 1000, 100, mode, "peak", float("nan"))


def wav_bytes(before_data=b"", rate=8000, pcm=bytes(range(256)) * 4):
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = b"WAVEfmt " + struct.pack("<I", 16) + fmt + before_data + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body, pcm


@pytest.mark.parametrize("size", [1, 3, 5])
def test_wav_chunk_without_pad_byte_with_format(size):
    wav, pcm = wav_bytes(b"junk" + struct.pack("<I", size) + b"x" * size)
    seg = pydub2.AudioSegment.from_file(io.BytesIO(wav), format="wav")
    assert (seg.raw_data, seg.frame_rate) == (pcm, 8000)


def test_wav_chunk_without_pad_byte_with_format_and_part():
    wav, pcm = wav_bytes(b"junk" + struct.pack("<I", 3) + b"xxx", rate=1000)
    seg = pydub2.AudioSegment.from_file(io.BytesIO(wav), format="wav", start_second=0.25, duration=0.25)
    assert seg.raw_data == pcm[500:1000]


def test_wav_chunk_without_pad_byte_with_limit(monkeypatch):
    wav, pcm = wav_bytes(b"junk" + struct.pack("<I", 3) + b"xxx")
    monkeypatch.setattr(pydub2, "max_bytes", 100)
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        pydub2.AudioSegment.from_file(io.BytesIO(wav), format="wav")


PCM_GUID = b"\x00\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"


def extensible_wav(tag, bits, channels=3, rate=8000, before_data=b"", frames=100):
    # what ffmpeg writes for more than 2 channels, a-law and mu-law as 8 bit, float as 32 bit
    pcm = bytes((i * 7) % 256 for i in range(frames * channels * bits // 8))
    width = channels * bits // 8
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * width, width, bits, 22, bits, 7)
    fmt += struct.pack("<H", tag) + PCM_GUID[2:]
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + before_data + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body, pcm


NO_PAD = b"junk" + struct.pack("<I", 3) + b"xxx"
NOT_PCM = [pytest.param(6, 8, b"", id="alaw"), pytest.param(7, 8, b"", id="mulaw"),
           pytest.param(6, 8, NO_PAD, id="alaw-no-pad"), pytest.param(7, 8, NO_PAD, id="mulaw-no-pad"),
           pytest.param(3, 32, NO_PAD, id="float-no-pad")]


@pytest.mark.parametrize("tag, bits, before_data", NOT_PCM)
@pytest.mark.parametrize("how", ["path", "path-wav", "bytesio-wav"])
def test_extensible_wav_that_is_not_pcm_is_not_read_as_pcm(tmp_path, monkeypatch, tag, bits, before_data, how):
    wav, pcm = extensible_wav(tag, bits, before_data=before_data)
    path = tmp_path / "x.wav"
    path.write_bytes(wav)
    monkeypatch.setattr(pydub2.AudioSegment, "converter", str(tmp_path / "missing"))
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        if how == "bytesio-wav":
            pydub2.AudioSegment.from_file(io.BytesIO(wav), format="wav")
        else:
            pydub2.AudioSegment.from_file(path, format="wav" if how == "path-wav" else None)


@needs_ff
@pytest.mark.parametrize("tag, bits", [(6, 8), (7, 8)])
@pytest.mark.parametrize("format", [None, "wav"])
def test_extensible_alaw_and_mulaw_wav_from_a_path_is_decoded(tmp_path, tag, bits, format):
    wav, pcm = extensible_wav(tag, bits)
    path = tmp_path / "x.wav"
    path.write_bytes(wav)
    seg = pydub2.AudioSegment.from_file(path, format=format)
    assert seg.raw_data != pcm
    assert seg.raw_data == pydub2.AudioSegment.from_file(io.BytesIO(wav)).raw_data


@pytest.mark.parametrize("channels", [1, 3])
def test_extensible_pcm_wav_with_a_chunk_without_pad_byte_from_a_path(tmp_path, channels):
    wav, pcm = extensible_wav(1, 16, channels=channels, before_data=NO_PAD)
    path = tmp_path / "x.wav"
    path.write_bytes(wav)
    seg = pydub2.AudioSegment.from_file(path)
    assert (seg.raw_data, seg.channels, seg.frame_rate) == (pcm, channels, 8000)


@needs_ff
def test_float_wav_at_a_rate_the_decoder_refuses_from_ffmpeg_is_not_read_as_pcm():
    wav, pcm = wav_bytes(rate=1332770)
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        pydub2.AudioSegment.from_file(io.BytesIO(wav), parameters=["-acodec", "pcm_f32le"])


@needs_ff
def test_wav_at_a_rate_the_decoder_refuses_from_ffmpeg():
    wav, pcm = wav_bytes(rate=1332770)
    seg = pydub2.AudioSegment.from_file(io.BytesIO(wav))
    assert (seg.raw_data, seg.frame_rate) == (pcm, 1332770)


@pytest.mark.parametrize("kw", [{"start_second": 2 ** 64}, {"start_second": 1e300}, {"duration": 2 ** 64},
                                 {"duration": 1e300}, {"start_second": 1e12}, {"start_second": -1},
                                 {"duration": float("nan")}, {"duration": float("inf")}])
def test_stream_start_and_duration_out_of_range(kw):
    wav, pcm = wav_bytes()
    with pytest.raises(ValueError):
        list(pydub2.AudioSegment.stream(io.BytesIO(wav), **kw))


def test_stream_start_and_duration_just_under_the_limit():
    wav, pcm = wav_bytes()
    assert list(pydub2.AudioSegment.stream(io.BytesIO(wav), start_second=1e11)) == []
    assert [len(c.raw_data) for c in pydub2.AudioSegment.stream(io.BytesIO(wav), duration=1e11)] == [1024]


def test_empty_segment_takes_a_huge_rate():
    seg = pydub2.AudioSegment.empty().set_frame_rate(2 ** 40)
    assert (seg.frame_rate, len(seg)) == (2 ** 40, 0)


def test_wav_chunk_without_pad_byte_with_24_bit_limit(monkeypatch):
    # 24 bit data is 4 bytes a sample once it is read, 24000 bytes become 32000
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 24000, 3, 24)
    body = b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"junk" + struct.pack("<I", 3) + b"xxx"
    body += b"data" + struct.pack("<I", 24000) + bytes(range(240)) * 100
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    assert len(pydub2.AudioSegment.from_file(io.BytesIO(wav), format="wav").raw_data) == 32000
    monkeypatch.setattr(pydub2, "max_bytes", 28000)
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        pydub2.AudioSegment.from_file(io.BytesIO(wav), format="wav")


@pytest.mark.parametrize("value", [decimal.Decimal("0.5"), decimal.Decimal("1")])
def test_stream_start_as_a_decimal(value):
    wav, pcm = wav_bytes(rate=1000, pcm=bytes(range(256)) * 8)
    plain = list(pydub2.AudioSegment.stream(io.BytesIO(wav), start_second=float(value)))
    got = list(pydub2.AudioSegment.stream(io.BytesIO(wav), start_second=value))
    assert [c.raw_data for c in got] == [c.raw_data for c in plain] != []
