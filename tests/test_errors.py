import io
import os
import shutil
import subprocess
import sys
import threading

import pytest

import pydub2
import pydub2.generators
from pydub2.exceptions import CouldntEncodeError

needs_ff = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


@pytest.mark.parametrize("bitrate", ["k", "1e400k", "1e400", "abc", "infk"])
@pytest.mark.parametrize("format", ["ogg", "opus"])
def test_bad_bitrate_native(format, bitrate):
    with pytest.raises(CouldntEncodeError):
        pydub2.AudioSegment.silent(500).export(format=format, bitrate=bitrate)


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


@pytest.mark.parametrize("kw", COVERS)
def test_cover_errors_like_pydub(kw):
    def call(AS):
        AS.silent(100).export(io.BytesIO(), **kw)
    got = bad_call("pydub2", call)
    assert got is not None and got == bad_call("pydub", call)


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
            results.append(type(e).__name__)
    assert results[0] == results[1]


@pytest.mark.parametrize("thresh", ["x", None, [1], "-20", 3j])
def test_detect_silence_threshold_type(thresh):
    def call(AS):
        pydub = pytest.importorskip("pydub")
        mod = pydub.silence if AS.__module__.startswith("pydub.") else pydub2.silence
        return mod.detect_silence(AS.silent(1000), 100, thresh)
    assert bad_call("pydub2", call) == bad_call("pydub", call)
