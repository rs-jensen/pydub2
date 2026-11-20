import array
import io
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tracemalloc

import pytest

import pydub2
import pydub2.generators
from pydub2 import AudioSegment, _ffmpeg, utils
from pydub2.__main__ import main
from pydub2.exceptions import CouldntDecodeError

ffmpeg = shutil.which("ffmpeg")
need_ffmpeg = pytest.mark.skipif(ffmpeg is None or shutil.which("ffprobe") is None, reason="no ffmpeg")
pydub = pytest.importorskip("pydub")
RNG = random.Random(7)
NOISE = RNG.randbytes(40000)


@pytest.fixture
def noffmpeg(monkeypatch, tmp_path):
    # nothing on PATH and no ffmpeg from imageio either, so only the built in reader can work
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(AudioSegment, "converter", "ffmpeg")
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)


def ext80(rate):
    if rate == 0:
        return bytes(10)
    m, e = math.frexp(rate)
    return struct.pack(">HQ", e - 1 + 16383, int(m * 2 ** 64))


def chunk(name, body, pad=True):
    return name + struct.pack(">I", len(body)) + body + (b"\0" if len(body) % 2 and pad else b"")


def comm(channels, frames, bits, rate, kind=None, text=b"x"):
    body = struct.pack(">HIh", channels, frames, bits) + (ext80(rate) if isinstance(rate, (int, float)) else rate)
    if kind is not None:
        body += kind + bytes([len(text)]) + text + b"\0" * (1 - len(text) % 2)
    return chunk(b"COMM", body)


def ssnd(pcm, offset=0, block=0):
    return chunk(b"SSND", struct.pack(">II", offset, block) + bytes(offset) + pcm)


def form(body, kind=b"AIFF", size=None):
    return b"FORM" + struct.pack(">I", 4 + len(body) if size is None else size) + kind + body


def aiff(pcm, channels=1, bits=16, rate=44100):
    return form(comm(channels, len(pcm) // ((bits + 7) // 8 * channels), bits, rate) + ssnd(pcm))


def aifc(pcm, kind, channels=1, bits=16, rate=44100):
    return form(comm(channels, len(pcm) // ((bits + 7) // 8 * channels), bits, rate, kind) + ssnd(pcm), b"AIFC")


def write(tmp_path, data, name="a.aiff"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def sig(seg):
    return seg.frame_rate, seg.channels, seg.sample_width, int(seg.frame_count()), seg.raw_data


def ours(tmp_path, data, **kw):
    return sig(AudioSegment.from_file(write(tmp_path, data), **kw))


def theirs(tmp_path, data, **kw):
    # pydub with ffmpeg. A failure is any error, pydub hands some of them out as JSON errors
    try:
        return sig(pydub.AudioSegment.from_file(write(tmp_path, data, "ref.aiff"), **kw))
    except Exception:
        return None


def same_as_pydub(tmp_path, data, **kw):
    want = theirs(tmp_path, data, **kw)
    if want is None:
        with pytest.raises(CouldntDecodeError):
            ours(tmp_path, data, **kw)
    else:
        assert ours(tmp_path, data, **kw) == want


def widen(samples, size):
    # what pydub gives for a 24 bit sample: 32 bit with the low byte 00, or ff for a negative one
    out = b""
    for i in range(0, len(samples), size):
        s = samples[i:i + size]
        out += (b"\xff" if s[0] & 0x80 else b"\0") + s[::-1] if size == 3 else s
    return out


def swap(samples, size):
    return b"".join(samples[i:i + size][::-1] for i in range(0, len(samples), size))


# the file formats of the built in reader without ffmpeg


@pytest.mark.parametrize("bits", [8, 16, 24, 32])
@pytest.mark.parametrize("channels", [1, 2, 3, 6])
def test_pcm_loads_without_ffmpeg(noffmpeg, tmp_path, bits, channels):
    size = bits // 8
    pcm = NOISE[:size * channels * 100]
    got = ours(tmp_path, aiff(pcm, channels, bits, 22050))
    want = pcm if size == 1 else widen(pcm, 3) if size == 3 else swap(pcm, size)
    assert got == (22050, channels, 4 if size == 3 else size, 100, want)


@pytest.mark.parametrize("bits, width", [(1, 1), (7, 1), (12, 2), (15, 2), (17, 4), (20, 4), (25, 4)])
def test_odd_bit_depths_take_the_next_whole_byte_count(noffmpeg, tmp_path, bits, width):
    size = (bits + 7) // 8
    got = ours(tmp_path, aiff(NOISE[:size * 50], 1, bits))
    assert got[2] == width and got[3] == 50


@pytest.mark.parametrize("rate", [8000, 11025, 22050, 44100, 48000, 96000, 192000, 1, 3, 12345.6, 99999.5])
def test_rate_is_read_from_the_80_bit_float(noffmpeg, tmp_path, rate):
    assert ours(tmp_path, aiff(NOISE[:200], 1, 16, rate))[0] == round(rate)


@pytest.mark.parametrize("rate", [0, 0.4, 2 ** 31, 3e9, 2 ** 40])
def test_absurd_rates_are_refused(noffmpeg, tmp_path, rate):
    with pytest.raises(CouldntDecodeError):
        ours(tmp_path, aiff(NOISE[:200], 1, 16, rate))


def test_rate_exponent_out_of_range_is_refused(noffmpeg, tmp_path):
    for exponent in (0, 0x3ffe, 0x405d, 0x7fff, 0xffff):
        with pytest.raises(CouldntDecodeError):
            ours(tmp_path, aiff(NOISE[:200], 1, 16, struct.pack(">HQ", exponent, 1 << 63)))


def test_rate_wraps_in_32_bits_like_ffmpeg(noffmpeg, tmp_path):
    assert ours(tmp_path, aiff(NOISE[:200], 1, 16, struct.pack(">HQ", 16446, (1 << 32) + 5)))[0] == 5


def test_chunks_in_any_order_with_odd_sizes_and_unknown_chunks(noffmpeg, tmp_path):
    pcm = NOISE[:200]
    want = (8000, 1, 2, 100, swap(pcm, 2))
    c = comm(1, 100, 16, 8000)
    for body in (c + ssnd(pcm), ssnd(pcm) + c, chunk(b"ANNO", b"abc") + c + ssnd(pcm), c + chunk(b"JUNK", b"x" * 7) + ssnd(pcm),
                 c + ssnd(pcm) + chunk(b"ANNO", b"abc"), chunk(b"NAME", b"a") + chunk(b"AUTH", b"bb") + c + ssnd(pcm),
                 chunk(b"\xff\x00zz", b"") + c + ssnd(pcm), c + ssnd(pcm) + b"\0\0\0"):
        assert ours(tmp_path, form(body)) == want


def test_ssnd_offset_and_block_size(noffmpeg, tmp_path):
    pcm = NOISE[:200]
    c = comm(1, 100, 16, 8000)
    for offset, block in ((0, 0), (2, 0), (10, 0), (300, 0), (0, 7), (4, 4096)):
        # the offset bytes are skipped, so the samples that follow are the same
        assert ours(tmp_path, form(c + ssnd(pcm, offset, block)))[4] == swap(pcm, 2)
    # an offset that skips real samples
    body = struct.pack(">II", 20, 0) + pcm
    assert ours(tmp_path, form(c + chunk(b"SSND", body)))[4] == swap(pcm[20:], 2)


def test_odd_sized_sound_chunk_drops_the_stray_byte(noffmpeg, tmp_path):
    assert ours(tmp_path, aiff(NOISE[:203]))[3] == 101


def test_the_comm_chunk_is_not_padded_like_in_ffmpeg(noffmpeg, tmp_path):
    # ffmpeg does not skip a pad byte after COMM, so this file has no sound for it either
    body = struct.pack(">HIh", 1, 100, 16) + ext80(8000) + b"\0"
    assert ours(tmp_path, form(chunk(b"COMM", body) + ssnd(NOISE[:200])))[3] == 0


def test_frame_count_in_comm_is_not_trusted(noffmpeg, tmp_path):
    pcm = NOISE[:200]
    for frames in (0, 10, 10 ** 9):
        assert ours(tmp_path, form(comm(1, frames, 16, 8000) + ssnd(pcm)))[3] == 100


def test_the_second_comm_and_the_second_ssnd_win(noffmpeg, tmp_path):
    got = ours(tmp_path, form(comm(1, 100, 16, 8000) + comm(2, 50, 16, 16000) + ssnd(NOISE[:100]) + ssnd(NOISE[100:300])))
    assert got[:3] == (16000, 2, 2) and got[4] == swap(NOISE[100:300], 2)


# aiff-c


@pytest.mark.parametrize("kind", [b"NONE", b"twos"])
@pytest.mark.parametrize("bits", [8, 16, 24, 32])
def test_aifc_big_endian_pcm(noffmpeg, tmp_path, kind, bits):
    size = bits // 8
    pcm = NOISE[:size * 2 * 40]
    got = ours(tmp_path, aifc(pcm, kind, 2, bits))
    assert got[3] == 40 and got[4] == (pcm if size == 1 else widen(pcm, 3) if size == 3 else swap(pcm, size))


@pytest.mark.parametrize("bits", [8, 16, 24, 32])
def test_aifc_sowt_is_little_endian_16_bit_whatever_the_bits_say(noffmpeg, tmp_path, bits):
    pcm = NOISE[:400]
    assert ours(tmp_path, aifc(pcm, b"sowt", 2, bits)) == (44100, 2, 2, 100, pcm)


@pytest.mark.parametrize("kind", [b"fl32", b"FL32"])
def test_aifc_float32(noffmpeg, tmp_path, kind):
    values = [0, 0.5, -0.5, 0.25, -1.0, 0.999, 0.123456, -0.7]
    got = ours(tmp_path, aifc(struct.pack(">8f", *values), kind, 1, 32))
    # 32 bit ints like for a float wav
    stored = struct.unpack(">8f", struct.pack(">8f", *values))
    want = [max(-2 ** 31, min(2 ** 31 - 1, round(v * 2 ** 31))) for v in stored]
    assert got[2:4] == (4, 8) and list(array.array("i", got[4])) == want


@pytest.mark.parametrize("kind", [b"fl64", b"FL64"])
def test_aifc_double_is_64_bit_ints(noffmpeg, tmp_path, kind):
    values = [0, 0.5, -0.5, 0.25, -1.0, 0.123456]
    got = ours(tmp_path, aifc(struct.pack(">6d", *values), kind, 1, 64))
    assert got[2:4] == (8, 6) and list(array.array("q", got[4])) == [round(v * 2 ** 63) for v in values]


def test_the_wav_reader_gives_the_same_width_for_float(noffmpeg, tmp_path):
    f32 = struct.pack("<4f", 0.5, -0.5, 0.25, 0)
    head = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + 16, b"WAVE", b"fmt ", 16, 3, 1, 8000, 32000, 4, 32, b"data", 16)
    wav = AudioSegment.from_file(io.BytesIO(head + f32))
    seg = AudioSegment.from_file(io.BytesIO(aifc(struct.pack(">4f", 0.5, -0.5, 0.25, 0), b"fl32", 1, 32, 8000)))
    assert (wav.sample_width, wav.raw_data) == (seg.sample_width, seg.raw_data)


@pytest.mark.parametrize("kind", [b"ulaw", b"alaw", b"ULAW", b"ALAW", b"raw ", b"in24", b"in32", b"xxxx"])
def test_compressed_aifc_is_left_to_ffmpeg(noffmpeg, tmp_path, kind):
    path = write(tmp_path, aifc(NOISE[:200], kind, 1, 8))
    with pytest.raises(Exception, match="ffmpeg"):
        AudioSegment.from_file(path)
    assert utils.needs_ffmpeg(open(path, "rb"), open(path, "rb").read(4096))


@need_ffmpeg
@pytest.mark.parametrize("kind", [b"ulaw", b"alaw", b"raw ", b"in24", b"in32"])
def test_compressed_aifc_loads_through_ffmpeg_when_it_is_there(tmp_path, kind):
    data = aifc(NOISE[:200], kind, 2, 8)
    assert ours(tmp_path, data) == theirs(tmp_path, data)


@pytest.mark.parametrize("data", [aifc(NOISE[:200], b"42ni", 1, 16), aifc(NOISE[:200], b"xxxx", 1, 16)])
def test_aifc_codecs_ffmpeg_refuses_are_refused_with_ffmpeg_too(tmp_path, data):
    if ffmpeg is None:
        pytest.skip("no ffmpeg")
    with pytest.raises(CouldntDecodeError):
        ours(tmp_path, data)


# finding the file


def test_detected_by_content_with_any_name_or_none(noffmpeg, tmp_path):
    data = aiff(NOISE[:200])
    want = (44100, 1, 2, 100, swap(NOISE[:200], 2))
    for name in ("a.aiff", "a.aif", "a.aifc", "a.AIFF", "a.wav", "a.mp3", "a.bin", "a", "a.flac"):
        assert ours(tmp_path, data) == want
        assert sig(AudioSegment.from_file(write(tmp_path, data, name))) == want


def test_path_open_file_bytesio_and_pathlike(noffmpeg, tmp_path):
    data = aiff(NOISE[:200])
    want = (44100, 1, 2, 100, swap(NOISE[:200], 2))
    path = write(tmp_path, data)
    assert sig(AudioSegment.from_file(path, format="aiff")) == want
    assert sig(AudioSegment.from_file(tmp_path / "a.aiff")) == want
    with open(path, "rb") as f:
        assert sig(AudioSegment.from_file(f)) == want
    assert sig(AudioSegment.from_file(io.BytesIO(data))) == want
    assert sig(AudioSegment.from_file(io.BytesIO(data), "aiff")) == want
    assert sig(AudioSegment.from_file(io.BytesIO(data), format="aiff", start_second=0.001, duration=0.001))[3] == 44


def test_what_and_native_format_name_it(tmp_path):
    head = aiff(NOISE[:200])[:4096]
    assert utils.sniff(head) == "aiff"
    assert utils.what(io.BytesIO(head), head) == "aiff"
    assert utils.native_format(head) == "aiff"
    assert utils.native_format(aifc(NOISE[:20], b"NONE")[:100]) == "aiff"


def test_doctor_says_it_loads_built_in(capsys, tmp_path, monkeypatch):
    for name, data in (("a.aiff", aiff(NOISE[:2000], 2, 16, 8000)), ("b.dat", aifc(NOISE[:2000], b"sowt", 2, 16, 8000))):
        monkeypatch.setenv("PATH", str(tmp_path / "none"))
        code = main(["doctor", write(tmp_path, data, name)])
        out = capsys.readouterr().out
        assert code == 0 and "aiff" in out and "natively" in out and "ffmpeg" not in out.replace("through ffmpeg", "")


def test_doctor_json_names_the_format(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "none"))
    main(["doctor", "--json", write(tmp_path, aiff(NOISE[:200]))])
    r = json.loads(capsys.readouterr().out)
    r = r.get("files", [r])[0] if isinstance(r, dict) else r[0]
    assert r["format"] == "aiff" and r["loads"] is True and r["how"] == "natively"


def test_doctor_report_lists_aiff_as_read_natively(capsys):
    main(["doctor", "--json"])
    assert "aiff" in json.loads(capsys.readouterr().out)["read_natively"]


def test_doctor_without_ffmpeg_for_a_compressed_one(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "none"))
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    main(["doctor", write(tmp_path, aifc(NOISE[:200], b"ulaw", 1, 8))])
    assert "ffmpeg" in capsys.readouterr().out


# strict mode


@need_ffmpeg
def test_strict_mode_uses_ffmpeg(tmp_path, monkeypatch):
    data = aiff(NOISE[:2000], 2, 16, 8000)
    monkeypatch.setattr(pydub2, "strict", True)
    used = []
    token = _ffmpeg._seen.set(used)
    try:
        seg = AudioSegment.from_file(write(tmp_path, data))
    finally:
        _ffmpeg._seen.reset(token)
    assert used and sig(seg) == theirs(tmp_path, data)


def test_strict_mode_without_ffmpeg_does_not_read_it_built_in(noffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(pydub2, "strict", True)
    with pytest.raises(Exception):
        AudioSegment.from_file(write(tmp_path, aiff(NOISE[:200])))


# pieces, streaming, info and the limits


def pieces(tmp_path):
    return aiff(NOISE[:6000], 2, 16, 8000), 1500


@pytest.mark.parametrize("start, duration", [(None, 0.01), (0.01, None), (0.005, 0.01), (0, 0.02), (0.0123, 0.0071), (0.024, 0.002)])
def test_start_second_and_duration_cut_the_whole_file(noffmpeg, tmp_path, start, duration):
    data = aiff(NOISE[:8000], 2, 16, 8000)
    whole = AudioSegment.from_file(io.BytesIO(data))
    got = ours(tmp_path, data, start_second=start, duration=duration)
    frames = 2000 - round((start or 0) * 8000) if duration is None else round(duration * 8000)
    # within a frame of what the times say, and a whole number of frames cut out of the file
    assert got[:3] == (8000, 2, 2) and abs(got[3] - frames) <= 1
    assert got[4] in whole.raw_data and whole.raw_data.index(got[4]) % 4 == 0


@need_ffmpeg
@pytest.mark.parametrize("start, duration", [(None, 0.1), (0.1, None), (0.05, 0.1), (0, 0.2), (0.3, 5), (0.0123, 0.0071), (0.124, 0.002),
                                              (1 / 3, 1 / 7), (0.5, 0.25), (0, 1)])
@pytest.mark.parametrize("bits", [8, 16, 24, 32])
def test_start_second_and_duration_like_pydub(tmp_path, start, duration, bits):
    # 1000 frames a second, so every cut here holds samples and a 24 bit file is not empty, which pydub fails on
    data = aiff(NOISE[:bits // 8 * 2 * 1000], 2, bits, 1000)
    same_as_pydub(tmp_path, data, start_second=start, duration=duration)


def test_stream_gives_the_whole_file_in_pieces(noffmpeg, tmp_path):
    data = aiff(NOISE[:8000], 2, 16, 8000)
    path = write(tmp_path, data)
    got = list(AudioSegment.stream(path, chunk_ms=100))
    assert b"".join(s.raw_data for s in got) == swap(NOISE[:8000], 2)
    assert all(s.frame_rate == 8000 and s.channels == 2 for s in got)
    assert len(got) == 3 and len(got[0]) == 100
    part = list(AudioSegment.stream(io.BytesIO(NOISE and aiff(NOISE[:32000], 2, 16, 8000)), chunk_ms=100, start_second=0.1, duration=0.2))
    assert sum(s.frame_count() for s in part) == 1600 and part[0].frame_count() == 800


def test_stream_of_a_file_that_is_not_readable_says_so(noffmpeg, tmp_path):
    path = write(tmp_path, aiff(NOISE[:200])[:20], "a.aiff")
    with pytest.raises(CouldntDecodeError, match="cant read this file"):
        list(AudioSegment.stream(path))


def test_mediainfo_without_ffprobe(noffmpeg, tmp_path):
    info = utils.mediainfo(write(tmp_path, form(chunk(b"NAME", b"Song") + chunk(b"AUTH", b"Me") + comm(2, 100, 24, 48000) + ssnd(NOISE[:600]))))
    assert info["codec_name"] == "pcm_s24be" and info["sample_rate"] == "48000" and info["channels"] == "2"
    assert info["bits_per_sample"] == "24" and info["format_name"] == "aiff" and info["sample_fmt"] == "s32"
    assert info["TAG"] == {"title": "Song", "author": "Me"} and info["duration"] == "0.002083"


@need_ffmpeg
@pytest.mark.parametrize("name, data", [
    ("8", aiff(NOISE[:800], 2, 8, 8000)), ("16", aiff(NOISE[:800], 2, 16, 8000)), ("24", aiff(NOISE[:900], 1, 24, 44100)),
    ("32", aiff(NOISE[:800], 1, 32, 8000)), ("sowt", aifc(NOISE[:800], b"sowt", 2, 16)), ("none", aifc(NOISE[:800], b"NONE", 2, 16)),
    ("twos", aifc(NOISE[:800], b"twos", 2, 8)), ("fl32", aifc(NOISE[:800], b"FL32", 1, 32)), ("fl64", aifc(NOISE[:800], b"fl64", 1, 64)),
    ("tags", form(chunk(b"NAME", b"Song") + chunk(b"AUTH", b"Me") + chunk(b"(c) ", b"C") + chunk(b"ANNO", b"One") + comm(1, 100, 16, 8000) + ssnd(NOISE[:200])))])
def test_mediainfo_like_ffprobe(tmp_path, monkeypatch, name, data):
    path = write(tmp_path, data)
    want = utils.mediainfo(path)
    monkeypatch.setenv("PATH", str(tmp_path / "none"))
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    got = utils.mediainfo(path)
    assert got and all(want.get(k) == v for k, v in got.items() if k not in ("duration", "TAG"))
    assert float(got["duration"]) == pytest.approx(float(want["duration"]), abs=1e-6)
    assert got.get("TAG") == want.get("TAG")


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(pydub2, "max_duration", None)
    monkeypatch.setattr(pydub2, "max_bytes", None)
    monkeypatch.setattr(pydub2, "max_file_size", None)
    return lambda **kw: [monkeypatch.setattr(pydub2, k, v) for k, v in kw.items()]


def test_max_duration_max_bytes_and_max_file_size(noffmpeg, tmp_path, limits):
    path = write(tmp_path, aiff(NOISE[:32000], 2, 16, 8000))  # one second
    limits(max_duration=1.5)
    assert AudioSegment.from_file(path).frame_count() == 8000
    limits(max_duration=0.2)
    with pytest.raises(CouldntDecodeError, match="max_duration"):
        AudioSegment.from_file(path)
    limits(max_duration=None, max_bytes=100000)
    assert AudioSegment.from_file(path).frame_count() == 8000
    limits(max_bytes=1000)
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(path)
    limits(max_bytes=None, max_file_size=os.path.getsize(path))
    assert AudioSegment.from_file(path).frame_count() == 8000
    limits(max_file_size=os.path.getsize(path) - 1)
    with pytest.raises(CouldntDecodeError, match="max_file_size"):
        AudioSegment.from_file(path)
    with pytest.raises(CouldntDecodeError, match="max_file_size"):
        AudioSegment.from_file(io.BytesIO(open(path, "rb").read()))
    with pytest.raises(CouldntDecodeError):
        list(AudioSegment.stream(path))


def test_limits_apply_to_a_piece_that_is_cut_out(noffmpeg, tmp_path, limits):
    path = write(tmp_path, aiff(NOISE[:32000], 2, 16, 8000))
    limits(max_duration=0.2)
    assert AudioSegment.from_file(path, start_second=0.5, duration=0.1).frame_count() == 800
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(path, start_second=0.5)


@pytest.mark.parametrize("claim", [0x7fffffff, 0xffffffff, 0x80000000, 10 ** 9])
def test_a_header_that_claims_far_more_than_the_file_holds_does_not_allocate(noffmpeg, tmp_path, claim):
    pcm = NOISE[:400]
    body = comm(2, 0x7fffffff, 16, 8000) + b"SSND" + struct.pack(">I", claim) + bytes(8) + pcm
    data = form(body, size=claim)
    path = write(tmp_path, data)
    tracemalloc.start()
    try:
        got = AudioSegment.from_file(path)
        got2 = AudioSegment.from_file(io.BytesIO(data))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert got.frame_count() == got2.frame_count() == 100 and peak < 4 << 20


def test_the_decoder_does_not_trust_the_frame_count(tmp_path):
    import _pydub2
    d = _pydub2.Decoder(aiff(NOISE[:400], 2, 16, 8000))
    assert d.frames == 100 and d.rate == 8000 and d.channels == 2 and d.width == 2
    assert len(d.read(10 ** 9)) == 400
    d.seek(25)
    assert d.read(10) == swap(NOISE[100:140], 2)
    d.seek(10 ** 12)
    assert d.read(10) == b""


# damaged files, each next to what pydub and ffmpeg do


def damaged():
    pcm = NOISE[:200]
    c, s = comm(1, 100, 16, 8000), ssnd(NOISE[:200])
    good = aiff(pcm, 1, 16, 8000)
    cases = {
        "truncated sound": good[:-50], "truncated a byte": good[:-1], "no sound bytes": good[:46], "cut in comm": good[:30],
        "cut in the sound header": good[:42], "cut in the form header": good[:10], "only the form header": form(b""),
        "form and nothing else": b"FORM", "no comm": form(s), "no ssnd": form(c), "comm after ssnd": form(s + c),
        "zero channels": form(comm(0, 100, 16, 8000) + s), "zero rate": form(comm(1, 100, 16, 0) + s),
        "huge rate": form(comm(1, 100, 16, 3e9) + s), "negative rate": form(comm(1, 100, 16, struct.pack(">HQ", 0xc00c, 1 << 63)) + s),
        "form size too big": form(c + s, size=10 ** 6), "form size ffffffff": form(c + s, size=0xffffffff),
        "form size too small": form(c + s, size=20), "form size ends in comm": form(c + s, size=4 + len(c)),
        "ssnd size 0": form(c + b"SSND" + struct.pack(">I", 0) + bytes(8) + pcm),
        "ssnd size 4": form(c + b"SSND" + struct.pack(">I", 4) + bytes(8) + pcm),
        "ssnd size ffffffff": form(c + b"SSND" + struct.pack(">I", 0xffffffff) + bytes(8) + pcm),
        "ssnd offset past the end": form(c + chunk(b"SSND", struct.pack(">II", 10 ** 6, 0) + pcm)),
        "unknown chunk too big": form(c + b"JUNK" + struct.pack(">I", 0x7fffffff) + s),
        "unknown chunk negative": form(c + b"JUNK" + struct.pack(">I", 0xffffffff) + s),
        "comm without pad": form(chunk(b"ANNO", b"abc", pad=False) + c + s),
        "comm too short": form(chunk(b"COMM", struct.pack(">HIh", 1, 100, 16) + ext80(8000)[:6]) + s),
        "32 bit chunk sizes of 18": form(b"COMM" + struct.pack(">I", 18) + bytes(18) + s),
        "bits 64": form(comm(1, 100, 64, 8000) + ssnd(NOISE[:800])), "bits 0": form(comm(1, 100, 0, 8000) + ssnd(NOISE[:100])),
        "wrong form tag": b"FORX" + good[4:], "wrong kind": good[:8] + b"AIFX" + good[12:], "many tiny chunks": form(c + chunk(b"JUNK", b"") * 3000 + s),
        "2000 channels": form(comm(2000, 100, 16, 8000) + ssnd(NOISE[:6000])), "65535 channels": form(comm(65535, 1, 8, 8000) + ssnd(NOISE[:70000])),
        "second comm is bad": form(c + comm(0, 100, 16, 16000) + s), "fver with other version": form(chunk(b"FVER", bytes(4)) + comm(1, 100, 16, 8000, b"sowt") + s, b"AIFC"),
        "aifc without a tag": form(c + s, b"AIFC"), "aiff with a tag": form(comm(1, 100, 16, 8000, b"ulaw") + s),
        "chan chunk too short": form(chunk(b"CHAN", b"abc") + c + s),
    }
    return cases


@need_ffmpeg
@pytest.mark.parametrize("name", list(damaged()))
def test_damaged_files_like_pydub(tmp_path, name):
    # pydub with ffmpeg loads what it can or fails, here the same
    same_as_pydub(tmp_path, damaged()[name])


@pytest.mark.parametrize("name", list(damaged()))
def test_damaged_files_without_ffmpeg_load_or_say_so(noffmpeg, tmp_path, name):
    try:
        got = ours(tmp_path, damaged()[name])
    except CouldntDecodeError:
        return
    except Exception as e:
        # only a file ffmpeg reads is handed on, and it says that
        assert "ffmpeg" in str(e)
        return
    assert got[3] * got[1] * got[2] == len(got[4])


def test_damaged_files_that_load_give_what_is_there(noffmpeg, tmp_path):
    assert ours(tmp_path, damaged()["truncated sound"])[3] == 75
    assert ours(tmp_path, damaged()["no ssnd"])[3] == 0
    assert ours(tmp_path, damaged()["form size too big"])[3] == 100
    assert ours(tmp_path, damaged()["form size too small"])[3] == 0
    for name in ("no comm", "only the form header", "zero channels", "zero rate", "comm after ssnd" if False else "bits 64"):
        with pytest.raises(CouldntDecodeError):
            ours(tmp_path, damaged()[name])


def test_comm_after_ssnd_is_read(noffmpeg, tmp_path):
    assert ours(tmp_path, damaged()["comm after ssnd"])[3] == 100


def test_a_thousand_mutated_files_never_crash_or_hang(noffmpeg, tmp_path):
    r = random.Random(3)
    bases = [aiff(NOISE[:300], 2, 16, 8000), form(chunk(b"ANNO", b"hi") + comm(1, 40, 24, 44100) + ssnd(NOISE[:120]) + chunk(b"APPL", b"abcd")),
             aifc(NOISE[:200], b"sowt", 2, 16), aifc(struct.pack(">10f", *[r.uniform(-1, 1) for _ in range(10)]), b"fl32", 1, 32),
             form(ssnd(NOISE[:100], 2, 4) + comm(1, 50, 16, 8000)), aifc(NOISE[:100], b"ulaw", 1, 8)]
    loaded = 0
    for i in range(1000):
        b = bytearray(r.choice(bases))
        for _ in range(r.choice((1, 1, 2, 3))):
            t = r.random()
            if t < 0.5:
                b[r.randrange(len(b))] = r.choice((0, 1, 0x7f, 0x80, 0xff, r.randrange(256)))
            elif t < 0.65:
                del b[r.randrange(len(b) + 1):]
            elif t < 0.8:
                j = r.randrange(len(b) + 1)
                b[j:j] = r.randbytes(r.randrange(1, 6))
            else:
                j = r.randrange(max(1, len(b) - 4))
                b[j:j + 4] = struct.pack(">I", r.choice((0, 1, 8, 18, 22, 0x7fffffff, 0x80000000, 0xffffffff)))
        try:
            seg = AudioSegment.from_file(io.BytesIO(bytes(b)))
            assert len(seg.raw_data) == seg.frame_count() * seg.frame_width
            loaded += 1
        except CouldntDecodeError:
            pass
        except Exception as e:
            assert "ffmpeg" in str(e), e
    assert loaded > 100


# export


@need_ffmpeg
@pytest.mark.parametrize("channels, rate, frames", [(1, 44100, 10), (2, 8000, 5), (1, 44100, 0), (3, 22050, 7), (6, 48000, 100), (1, 96000, 1),
                                                    (2, 11025, 1000), (1, 8000, 3), (1, 1, 5), (1, 192000, 4), (1, 12345, 9)])
def test_16_bit_export_is_the_bytes_ffmpeg_writes(tmp_path, channels, rate, frames):
    seg = AudioSegment(NOISE[:frames * channels * 2], sample_width=2, frame_rate=rate, channels=channels)
    mine = seg.export(format="aiff").read()
    theirs = pydub.AudioSegment(NOISE[:frames * channels * 2], sample_width=2, frame_rate=rate, channels=channels).export(format="aiff").read()
    assert mine == theirs


def test_16_bit_export_needs_no_ffmpeg_and_reads_back(noffmpeg, tmp_path):
    seg = AudioSegment(NOISE[:800], sample_width=2, frame_rate=8000, channels=2)
    out = seg.export(format="aiff").read()
    assert out[:4] == b"FORM" and out[8:12] == b"AIFF"
    assert AudioSegment.from_file(io.BytesIO(out)).raw_data == seg.raw_data
    path = str(tmp_path / "o.aiff")
    seg.export(path, format="aiff")
    assert AudioSegment.from_file(path).raw_data == seg.raw_data


@need_ffmpeg
@pytest.mark.parametrize("width", [1, 4])
def test_other_widths_are_exported_by_ffmpeg(tmp_path, width):
    seg = AudioSegment(NOISE[:width * 100], sample_width=width, frame_rate=8000, channels=1)
    ref = pydub.AudioSegment(NOISE[:width * 100], sample_width=width, frame_rate=8000, channels=1).export(format="aiff").read()
    assert seg.export(format="aiff").read() == ref


@need_ffmpeg
def test_exports_with_tags_or_a_codec_go_to_ffmpeg(tmp_path):
    seg = AudioSegment(NOISE[:200], sample_width=2, frame_rate=8000, channels=1)
    out = seg.export(format="aiff", tags={"title": "Song"}).read()
    assert b"NAME" in out
    assert seg.export(format="aiff", codec="pcm_s16be").read()[:4] == b"FORM"


@need_ffmpeg
@pytest.mark.parametrize("name, data", [
    ("8", aiff(NOISE[:400], 2, 8, 8000)), ("16", aiff(NOISE[:400], 2, 16, 8000)), ("24", aiff(NOISE[:600], 2, 24, 8000)),
    ("32", aiff(NOISE[:800], 1, 32, 8000)), ("sowt", aifc(NOISE[:400], b"sowt", 2, 16)), ("twos8", aifc(NOISE[:400], b"twos", 2, 8)),
    ("fl32", aifc(struct.pack(">64f", *[math.sin(i / 3) * 0.9 for i in range(64)]), b"fl32", 2, 32)),
    ("fl64", aifc(struct.pack(">32d", *[math.sin(i / 3) * 0.9 for i in range(32)]), b"fl64", 1, 64))])
def test_temporary_files_variant_narrows_like_ffmpeg(tmp_path, name, data):
    path = write(tmp_path, data)
    got = sig(AudioSegment.from_file_using_temporary_files(path))
    want = sig(pydub.AudioSegment.from_file_using_temporary_files(path))
    assert got[:4] == want[:4]
    # a float source is rounded to 16 bit by ffmpeg, the sums of one count of rounding are what the wav reader allows too
    assert got[4] == want[4] or max(abs(a - b) for a, b in zip(array.array("h", got[4]), array.array("h", want[4]))) <= 1


# the other formats are not slowed down


def test_wav_still_loads_and_is_untouched(tmp_path):
    seg = pydub2.generators.Sine(440, sample_rate=16000).to_audio_segment(duration=500)
    wav = seg.export(format="wav").read()
    assert sig(AudioSegment.from_file(io.BytesIO(wav)))[4] == seg.raw_data
