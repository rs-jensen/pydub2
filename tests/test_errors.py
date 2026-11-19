import io
import os
import re
import shutil
import subprocess
import sys
import threading

import pytest

import pydub2
import pydub2.generators
from pydub2 import _mp3
from pydub2.exceptions import CouldntEncodeError

needs_ff = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


@pytest.mark.parametrize("bitrate", ["k", "1e400k", "1e400", "abc", "infk"])
@pytest.mark.parametrize("format", ["ogg", "opus"])
def test_bad_bitrate_native(format, bitrate):
    with pytest.raises(CouldntEncodeError):
        pydub2.AudioSegment.silent(500).export(format=format, bitrate=bitrate)


BAD_BITRATES = ["", " ", "k", "1e30k", "-64k", "nan", "inf", "64kk", "1e400k", "6_4k", "64k;", -64, 10 ** 30]


@pytest.fixture(params=["no ffmpeg", "ffmpeg"])
def converter(request, monkeypatch, tmp_path):
    if request.param == "no ffmpeg":
        monkeypatch.setattr(pydub2.AudioSegment, "converter", str(tmp_path / "missing"))
    elif shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg")


@pytest.mark.parametrize("format", ["ogg", "opus", "mp3", "flac"])
@pytest.mark.parametrize("bitrate", BAD_BITRATES)
def test_bad_bitrate_is_an_encode_error(converter, format, bitrate):
    with pytest.raises(CouldntEncodeError):
        pydub2.AudioSegment.silent(500, frame_rate=44100).export(io.BytesIO(), format=format, bitrate=bitrate)


@pytest.mark.parametrize("format", ["ogg", "opus", "flac"])
@pytest.mark.parametrize("bitrate", ["64 k", "6.4e1k", "+64k", "64K", " 64k\n", "0x40k", b"64k", 64000])
def test_bitrate_in_other_forms_is_64k(converter, format, bitrate):
    seg = pydub2.AudioSegment.silent(500, frame_rate=44100)
    want = io.BytesIO()
    seg.export(want, format=format, bitrate="64k")
    got = io.BytesIO()
    seg.export(got, format=format, bitrate=bitrate)
    # opus has a random stream serial, the length is the same
    assert len(got.getvalue()) == len(want.getvalue())


@pytest.mark.parametrize("format", ["ogg", "opus", "flac"])
@pytest.mark.parametrize("bitrate", ["0", "0k", "-0", "64m"])
def test_bitrate_of_nothing_is_the_default(converter, format, bitrate):
    seg = pydub2.AudioSegment.silent(500, frame_rate=44100)
    want = io.BytesIO()
    seg.export(want, format=format)
    got = io.BytesIO()
    seg.export(got, format=format, bitrate=bitrate)
    # opus has a random stream serial, the length is the same
    assert len(got.getvalue()) == len(want.getvalue())


@pytest.mark.parametrize("format", ["ogg", "opus", "flac"])
def test_bitrate_with_a_null_byte_is_a_value_error(format):
    with pytest.raises(ValueError):
        pydub2.AudioSegment.silent(500).export(io.BytesIO(), format=format, bitrate="64\0k")


@pytest.mark.parametrize("channels, bitrate, good", [(1, "499", False), (1, "500", True), (1, "256000", True),
                                                      (1, "256001", False), (2, "512000", True), (2, "512001", False),
                                                      (2, "1", False), (2, "1e6", False)])
def test_opus_bitrate_range(converter, channels, bitrate, good):
    seg = pydub2.AudioSegment.silent(500, frame_rate=48000).set_channels(channels)
    if good:
        seg.export(io.BytesIO(), format="opus", bitrate=bitrate)
    else:
        with pytest.raises(CouldntEncodeError):
            seg.export(io.BytesIO(), format="opus", bitrate=bitrate)


@pytest.mark.parametrize("text, bps", [("64k", 64000), ("1.5k", 1500), ("64Ki", 65536), ("64kB", 512000), ("0x40k", 64000),
                                       ("6.4e1k", 64000), (" 6 4\tk ", 64000), ("64m", 0), ("1M", 1000000), ("--64k", 64000),
                                       ("9223372036854775808", 2 ** 63), (b"64k", 64000), (128000, 128000), ("-0", 0)])
def test_rate_of(text, bps):
    assert _mp3.rate_of(text) == bps


@pytest.mark.parametrize("text", ["", " ", "k", "1e", "nan", "inf", "-64k", "+-64k", "64kk", "6_4k", "\u0666\u0664k", "0x", "1e30k",
                                  "9.3e18", "64E", "64mi", "1,5k"])
def test_rate_of_refuses(text):
    with pytest.raises(ValueError):
        _mp3.rate_of(text)


@needs_ff
@pytest.mark.parametrize("bitrate", ["k", "1e400k", "1e400", "abc", "infk"])
def test_bad_bitrate_mp3(bitrate):
    with pytest.raises(CouldntEncodeError):
        pydub2.AudioSegment.silent(500).export(format="mp3", bitrate=bitrate)


class Plain(float):
    # ffmpeg does not read 5e-05, this is the same number written without an exponent
    def __str__(self):
        return format(self, "f")


@needs_ff
@pytest.mark.parametrize("kind", ["start_second", "duration"])
@pytest.mark.parametrize("value", [1e-5, 5e-5, 0.00012, 0.0005])
def test_stream_small_floats(tmp_path, kind, value):
    pydub = pytest.importorskip("pydub")
    seg = pydub2.generators.Sine(440, sample_rate=48000).to_audio_segment(duration=500)
    seg.export(str(tmp_path / "a.flac"), format="flac")
    want = pydub.AudioSegment.from_file(str(tmp_path / "a.flac"), **{kind: Plain(value)}).raw_data
    got = b"".join(c.raw_data for c in pydub2.AudioSegment.stream(str(tmp_path / "a.flac"), **{kind: value}))
    assert got == want
    if "e" in str(value):
        # pydub passes 1e-05 on to ffmpeg as it is
        with pytest.raises(pydub.exceptions.CouldntDecodeError):
            pydub.AudioSegment.from_file(str(tmp_path / "a.flac"), **{kind: value})


@pytest.mark.skipif(sys.platform == "win32", reason="needs a shell script as converter")
@pytest.mark.parametrize("format", ["mp3", "m4a", "ogg"])
def test_converter_writes_nothing(tmp_path, monkeypatch, format):
    script = tmp_path / "quiet"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    seg = pydub2.AudioSegment.silent(100)
    monkeypatch.setattr(pydub2.AudioSegment, "converter", str(script))
    assert seg.export(format=format, codec="x").read() == b""


@pytest.mark.skipif(sys.platform == "win32", reason="needs a shell script as converter")
@pytest.mark.parametrize("format", ["mp3", "m4a", "ogg"])
def test_converter_writes_nothing_to_a_path(tmp_path, monkeypatch, format):
    pydub = pytest.importorskip("pydub")
    script = tmp_path / "quiet"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    left = {}
    for name, lib in (("new", pydub2), ("old", pydub)):
        monkeypatch.setattr(lib.AudioSegment, "converter", str(script))
        path = tmp_path / name
        path.write_bytes(b"x" * 1000)
        lib.AudioSegment.silent(100).export(str(path), format=format, codec="x")
        left[name] = path.read_bytes()
    assert left["new"] == left["old"] == b""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs fifos")
@pytest.mark.parametrize("format", ["flac", "ogg", "opus"])
def test_load_from_fifo(tmp_path, format):
    seg = pydub2.generators.Sine(440, sample_rate=48000).to_audio_segment(duration=300)
    data = seg.export(format=format).read()
    fifo = str(tmp_path / ("pipe." + format))
    os.mkfifo(fifo)

    def feed():
        with open(fifo, "wb") as f:
            f.write(data)
    threading.Thread(target=feed, daemon=True).start()
    # the fifo holds the file once, a second open by name waits for ever
    code = "import pydub2; print(len(pydub2.AudioSegment.from_file(%r)))" % fifo
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == str(len(pydub2.AudioSegment.from_file(io.BytesIO(data)))), out.stderr


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs fifos")
@pytest.mark.parametrize("format", ["wav", "flac", "mp3", "ogg", "opus"])
@pytest.mark.parametrize("kw", [{}, {"start_second": 0.1, "duration": 0.15}])
def test_stream_from_fifo(tmp_path, format, kw):
    seg = pydub2.generators.Sine(440, sample_rate=48000).to_audio_segment(duration=300)
    data = seg.export(format=format).read()
    plain = tmp_path / ("plain." + format)
    plain.write_bytes(data)
    fifo = str(tmp_path / ("pipe." + format))
    os.mkfifo(fifo)

    def feed():
        with open(fifo, "wb") as f:
            f.write(data)
    threading.Thread(target=feed, daemon=True).start()
    # the fifo holds the file once, a second open by name waits for ever, so a child process that can be killed reads it
    code = "import sys, pydub2\nfor c in pydub2.AudioSegment.stream(sys.argv[1], chunk_ms=100, **%r): sys.stdout.buffer.write(c.raw_data)" % kw
    out = subprocess.run([sys.executable, "-c", code, fifo], capture_output=True, timeout=30)
    want = b"".join(c.raw_data for c in pydub2.AudioSegment.stream(str(plain), chunk_ms=100, **kw))
    assert out.stdout == want, out.stderr


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs fifos")
@pytest.mark.parametrize("format", ["flac", "ogg"])
def test_fifo_over_max_bytes_like_a_file(tmp_path, monkeypatch, format):
    seg = pydub2.generators.Sine(440, sample_rate=48000).to_audio_segment(duration=1000)
    data = seg.export(format=format).read()
    plain = tmp_path / ("plain." + format)
    plain.write_bytes(data)
    fifo = tmp_path / ("pipe." + format)
    os.mkfifo(fifo)

    def feed():
        with open(fifo, "wb") as f:
            f.write(data)
    threading.Thread(target=feed, daemon=True).start()
    monkeypatch.setattr(pydub2, "max_bytes", 10000)
    with pytest.raises(pydub2.exceptions.CouldntDecodeError, match="^this file is larger than max_bytes$"):
        pydub2.AudioSegment.from_file(str(plain))
    with pytest.raises(pydub2.exceptions.CouldntDecodeError, match="^this file is larger than max_bytes$"):
        pydub2.AudioSegment.from_file(str(fifo))


def bad_call(module, call):
    pydub = pytest.importorskip("pydub")
    lib = pydub if module == "pydub" else pydub2
    try:
        call(lib.AudioSegment)
    except Exception as e:
        return type(e).__name__, str(e)


COVERS = [dict(format="ogg", cover="x.jpg"), dict(format="mp3", cover="x.txt"), dict(format="mp3", cover=5),
          dict(format="flac", cover="x.png"), dict(format="mp3", cover="x.JPG.gif")]
COVERS += [dict(format=f, cover=c) for f in ("mp3", "ogg", "opus", "flac", "m4a") for c in (5, 1.5, b"x", b"x.png", [])]


@pytest.mark.parametrize("kw", COVERS)
def test_cover_errors_like_pydub(kw):
    def call(AS):
        AS.silent(100).export(io.BytesIO(), **kw)
    got = bad_call("pydub2", call)
    assert got is not None and got == bad_call("pydub", call)


ARGS = [dict(bitrate=1.5), dict(bitrate=["64k"]), dict(codec=5), dict(codec=["x"]), dict(parameters=5),
        dict(parameters=["-ac", 1]), dict(parameters=[None]), dict(parameters=0)]


@pytest.mark.parametrize("kw", ARGS)
@pytest.mark.parametrize("format", ["mp3", "wav", "flac", "ogg", "opus"])
def test_export_argument_types_like_pydub(format, kw):
    def call(AS):
        AS.silent(100).export(io.BytesIO(), format=format, **kw)
    assert bad_call("pydub2", call) == bad_call("pydub", call)


@pytest.mark.parametrize("kw", ARGS[:2] + ARGS[2:4])
def test_export_argument_types_raise(kw):
    with pytest.raises(TypeError):
        pydub2.AudioSegment.silent(100).export(io.BytesIO(), format="flac", **kw)


@pytest.mark.parametrize("arg", [None, 5, [1], 1.5])
def test_mediainfo_of_a_non_path_like_pydub(arg):
    pydub = pytest.importorskip("pydub")
    import pydub.utils
    got = bad_call("pydub2", lambda AS: pydub2.utils.mediainfo(arg))
    assert got is not None and got[0] == "TypeError"
    assert got == bad_call("pydub", lambda AS: pydub.utils.mediainfo(arg))


def test_missing_attribute_like_pydub():
    def call(AS):
        AS.silent(100).nope
    got = bad_call("pydub2", call)
    assert got == ("AttributeError", "'AudioSegment' object has no attribute 'nope'")
    assert got == bad_call("pydub", call)


def test_property_error_is_kept():
    class Sub(pydub2.AudioSegment):
        @property
        def broken(self):
            return self.missing_thing
    with pytest.raises(AttributeError, match="no attribute 'missing_thing'"):
        Sub.silent(10).broken


@pytest.mark.parametrize("times", ["x", [1], b"2", object(), [], "", 3j, 2.5])
@pytest.mark.parametrize("length", [100, 1000, 3000])
def test_overlay_times_not_a_number(times, length):
    pydub = pytest.importorskip("pydub")
    results = []
    for lib in (pydub, pydub2):
        base = lib.AudioSegment.silent(2000)
        try:
            results.append(base.overlay(lib.AudioSegment.silent(length), times=times).raw_data)
        except Exception as e:
            results.append((type(e).__name__, str(e)))
    assert results[0] == results[1]


@pytest.mark.parametrize("loop", [False, True])
@pytest.mark.parametrize("times", [0, 1, 2, None])
def test_overlay_times_like_pydub(times, loop):
    pydub = pytest.importorskip("pydub")
    results = []
    for lib in (pydub, pydub2):
        base = lib.AudioSegment(b"\x01\x00" * 8000, sample_width=2, frame_rate=8000, channels=1)
        tone = lib.AudioSegment(b"\x10\x20" * 400, sample_width=2, frame_rate=8000, channels=1)
        results.append(base.overlay(tone, times=times, loop=loop).raw_data)
    assert results[0] == results[1]
    assert (results[0] == base.raw_data) == (times == 0 and not loop)


@pytest.mark.parametrize("thresh", ["x", None, [1], "-20", 3j])
def test_detect_silence_threshold_type(thresh):
    def call(AS):
        pydub = pytest.importorskip("pydub")
        mod = pydub.silence if AS.__module__.startswith("pydub.") else pydub2.silence
        return mod.detect_silence(AS.silent(1000), 100, thresh)
    assert bad_call("pydub2", call) == bad_call("pydub", call)


FLOAT_RATES = [22050.0, 22050.5, "22050", None]


def not_an_int(v):
    t = type(v)
    name = t.__qualname__ if t.__module__ == "builtins" else t.__module__ + "." + t.__qualname__
    return "^'%s' object cannot be interpreted as an integer$" % name


def float_rates():
    np = pytest.importorskip("numpy")
    return FLOAT_RATES + [np.float64(22050), np.float32(22050)]


@pytest.mark.parametrize("quality", [None, "high"])
def test_set_frame_rate_needs_an_int_like_pydub(quality):
    seg = pydub2.AudioSegment.silent(100, frame_rate=44100)
    for rate in float_rates():
        # audioop.ratecv in pydub raises this, the bindings of the core gave their signature
        with pytest.raises(TypeError, match=not_an_int(rate)):
            seg.set_frame_rate(rate, resample_quality=quality)
        if isinstance(rate, float) or type(rate).__module__ == "numpy":
            with pytest.raises(TypeError, match=not_an_int(rate)):
                pydub2.AudioSegment.silent(100, frame_rate=rate).set_frame_rate(8000, quality)
    # a float rate on one side of overlay and append goes through set_frame_rate in pydub too
    with pytest.raises(TypeError, match="^'float' object cannot be interpreted as an integer$"):
        seg.overlay(pydub2.AudioSegment.silent(100, frame_rate=22050.0))
    with pytest.raises(TypeError, match="^'float' object cannot be interpreted as an integer$"):
        seg.append(pydub2.AudioSegment.silent(100, frame_rate=48000.0))


def test_set_frame_rate_float_like_pydub():
    pydub = pytest.importorskip("pydub")
    seg = pydub2.AudioSegment.silent(100, frame_rate=44100)
    old = pydub.AudioSegment.silent(100, frame_rate=44100)
    for rate in float_rates():
        with pytest.raises(TypeError) as theirs:
            old.set_frame_rate(rate)
        with pytest.raises(TypeError) as mine:
            seg.set_frame_rate(rate)
        assert str(mine.value) == str(theirs.value)


def test_set_frame_rate_takes_numpy_ints():
    np = pytest.importorskip("numpy")
    seg = pydub2.AudioSegment.silent(100, frame_rate=44100)
    for kind in (np.int16, np.int32, np.int64, np.uint16):
        for quality in (None, "high"):
            assert seg.set_frame_rate(kind(22050), quality) == seg.set_frame_rate(22050, quality)


@pytest.mark.parametrize("bitrate, bits", [(64, 64), ("192", 192), (192, 192), ("32k", 32000)])
def test_ogg_bitrate_libvorbis_cannot_use_says_so(bitrate, bits):
    seg = pydub2.AudioSegment.silent(500, frame_rate=44100).set_channels(2)
    text = "bad bitrate %r, libvorbis cant encode 44100 Hz with 2 channel(s) at %d bit/s" % (bitrate, bits)
    if bits < 1000:
        text += ", did you mean '%dk'" % bits
    with pytest.raises(CouldntEncodeError, match="^%s$" % re.escape(text)) as e:
        seg.export(io.BytesIO(), format="ogg", bitrate=bitrate)
    assert (e.value.format, e.value.reason) == ("ogg", "cant_encode")


@needs_ff
@pytest.mark.parametrize("bitrate", ["192", "32k"])
def test_ogg_bitrate_libvorbis_cannot_use_like_pydub(bitrate):
    # ffmpeg reads "192" as 192 bit/s, which libvorbis refuses, so pydub fails too
    pydub = pytest.importorskip("pydub")
    with pytest.raises(pydub.exceptions.CouldntEncodeError):
        pydub.AudioSegment.silent(500, frame_rate=44100).set_channels(2).export(io.BytesIO(), format="ogg", bitrate=bitrate)
    with pytest.raises(CouldntEncodeError):
        pydub2.AudioSegment.silent(500, frame_rate=44100).set_channels(2).export(io.BytesIO(), format="ogg", bitrate=bitrate)
