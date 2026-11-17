import array
import gc
import io
import os
import pathlib
import re
import struct
import subprocess
import sys
import threading

import pytest

import _pydub2
import pydub2
from pydub2 import AudioSegment, utils
from pydub2.exceptions import CouldntDecodeError

ff = "/opt/homebrew/bin/ffmpeg"
if not os.path.exists(ff):
    ff = "ffmpeg"
try:
    subprocess.run([ff, "-version"], capture_output=True, check=True)
except (OSError, subprocess.CalledProcessError):
    pytest.skip("no ffmpeg", allow_module_level=True)

ARGS = {"wav": [], "flac": [], "mp3": ["-c:a", "libmp3lame"], "ogg": ["-c:a", "libvorbis"], "opus": ["-c:a", "libopus"]}
# how far pydub2 may be from ffmpeg, see test_decode.py
LIMIT = {"wav": 0, "flac": 0, "mp3": 1, "ogg": 1024, "opus": 1024}
if re.search(r"version n?8\.0", subprocess.run([ff, "-version"], capture_output=True, text=True).stdout):
    LIMIT["opus"] = 1 << 19  # ffmpeg 8.0 opus tail differs
# a part of a vorbis file comes from ffmpeg, the others are cut from the same decoder
SAME = {"wav": 0, "flac": 0, "mp3": 0, "ogg": 1024, "opus": 0}
CASES = [(1, 2), (0.5, None), (None, 1.5), (2.00001, 1.99999), (4.435, 1.865535), (6.9, None), (7.2, 5), (8, 1),
         (1 / 3, 1 / 7), (0.140370232, None), (3.81, 3.446263)]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("x") / "Jørgen 音楽 sång"
    d.mkdir()
    out = {}
    for ext, args in ARGS.items():
        out[ext] = str(d / ("sång." + ext))
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=a=0.3:r=44100:seed=5", "-t", "7.3",
                        "-ac", "2", *args, out[ext]], check=True)
    return out


def close(a, b, limit):
    assert (a.frame_rate, a.channels, a.sample_width) == (b.frame_rate, b.channels, b.sample_width)
    assert len(a.raw_data) == len(b.raw_data)
    x, y = a.get_array_of_samples(), b.get_array_of_samples()
    assert max([abs(i - j) for i, j in zip(x, y)] or [0]) <= limit


@pytest.mark.parametrize("ext", ARGS)
def test_partial_like_pydub(files, ext):
    pydub = pytest.importorskip("pydub")
    for ss, t in CASES:
        close(AudioSegment.from_file(files[ext], start_second=ss, duration=t),
              pydub.AudioSegment.from_file(files[ext], start_second=ss, duration=t), LIMIT[ext])


@pytest.mark.parametrize("ext", ARGS)
def test_partial_is_a_slice(files, ext):
    full = AudioSegment.from_file(files[ext])
    part = AudioSegment.from_file(files[ext], start_second=1.5, duration=2)
    assert len(part) == 2000
    close(part, full[1500:3500], SAME[ext])


@pytest.mark.parametrize("ext", ARGS)
def test_stream(files, ext):
    full = AudioSegment.from_file(files[ext])
    chunks = list(AudioSegment.stream(files[ext], chunk_ms=700))
    assert all(c.frame_count() == int(700 * full.frame_rate / 1000) for c in chunks[:-1])
    assert b"".join(c.raw_data for c in chunks) == full.raw_data
    for ss, t in CASES:
        part = AudioSegment.from_file(files[ext], start_second=ss, duration=t)
        close(part._spawn(b"".join(c.raw_data for c in AudioSegment.stream(files[ext], 300, ss, t))), part, SAME[ext])


@pytest.mark.parametrize("ext", ARGS)
def test_path_kinds(files, ext):
    p = files[ext]
    a = AudioSegment.from_file(p)
    with open(p, "rb") as f:
        assert AudioSegment.from_file(f) == a
    assert AudioSegment.from_file(pathlib.Path(p)) == a
    assert AudioSegment.from_file(os.fsencode(p)) == a
    assert sum(AudioSegment.stream(pathlib.Path(p), 2000)) == a


def test_stream_needs_a_bundled_format(tmp_path):
    p = tmp_path / "a.m4a"
    p.write_bytes(b"not audio at all")
    with pytest.raises(CouldntDecodeError):
        AudioSegment.stream(p)


def test_raw_named_wav(files, tmp_path):
    wav = AudioSegment.from_file(files["wav"])
    kw = dict(sample_width=1, frame_rate=8000, channels=1)
    # pydub reads a .wav as wav even when told raw, but not a stream it cant name
    assert AudioSegment.from_raw(files["wav"], **kw) == wav
    assert AudioSegment.from_file(files["wav"], format="raw", **kw) == wav
    with open(files["wav"], "rb") as f:
        data = f.read()
    assert len(AudioSegment.from_raw(io.BytesIO(data), **kw).raw_data) == len(data)


def test_wav_ignores_parameters(files):
    wav = AudioSegment.from_file(files["wav"])
    assert AudioSegment.from_file(files["wav"], parameters=["-ac", "1"]) == wav
    part = AudioSegment.from_file(files["wav"], codec="pcm_s16le", parameters=["-ar", "8000"], start_second=1, duration=1)
    assert part == wav[1000:2000]


@pytest.mark.skipif(sys.platform == "win32", reason="needs a sparse file")
@pytest.mark.parametrize("kind", ["rf64", "pipe"])
def test_over_4gb(tmp_path, kind):
    n, rate = (5 << 30) // 4, 48000
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, rate, rate * 4, 4, 16)
    p = str(tmp_path / "big.wav")
    with open(p, "wb") as f:
        if kind == "rf64":
            f.write(b"RF64" + struct.pack("<I", 0xffffffff) + b"WAVE" + b"ds64" + struct.pack("<IQQQI", 28, 48 + n * 4, n * 4, n, 0))
        else:
            f.write(b"RIFF" + struct.pack("<I", 0xffffffff) + b"WAVE")
        f.write(fmt + b"data" + struct.pack("<I", 0xffffffff))
        head = f.tell()
        mark = n - rate * 5
        f.seek(head + mark * 4)
        f.write(b"\1\2\3\4")
        f.truncate(head + n * 4)
    try:
        part = AudioSegment.from_file(p, start_second=mark / rate - 1, duration=2)
        assert len(part) == 2000
        assert part.raw_data.index(b"\1\2\3\4") == rate * 4
        tail = b"".join(c.raw_data for c in AudioSegment.stream(p, start_second=mark / rate - 3))
        assert tail.index(b"\1\2\3\4") == rate * 3 * 4
        # 1342177280 frames are 27962027 ms and pydub fills the last 16 frames with silence
        assert len(tail) == (384000 + 16) * 4
        assert next(AudioSegment.stream(p)).frame_count() == rate * 10
    finally:
        os.remove(p)


@pytest.mark.parametrize("ext", ["mp3", "ogg", "opus"])
def test_one_decoder_two_threads(files, ext):
    code = """
import sys, threading
import _pydub2
d = _pydub2.Decoder(sys.argv[1])
def work(k):
    for i in range(30):
        d.seek((i * 7919 + k) % d.frames)
        d.read(5000)
t = [threading.Thread(target=work, args=(k,)) for k in range(4)]
[x.start() for x in t]
[x.join() for x in t]
"""
    r = subprocess.run([sys.executable, "-c", code, files[ext]], capture_output=True, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert r.returncode == 0, r.stderr[-300:]


def held(path):
    # counting the open files is disturbed by other threads, so look for this one file
    if sys.platform == "win32":
        # a file the C runtime has open cant be renamed there
        try:
            os.rename(path, path + ".x")
        except PermissionError:
            return True
        os.rename(path + ".x", path)
        return False
    want = os.stat(path)
    for fd in range(4096):
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        if (st.st_dev, st.st_ino) == (want.st_dev, want.st_ino):
            return True
    return False


@pytest.fixture
def wav(tmp_path):
    # the file has to go when the code says so, not when the cycle collector gets to it
    path = str(tmp_path / "a.wav")
    AudioSegment.silent(3000, 8000).export(path, "wav").close()
    was = gc.isenabled()
    gc.disable()
    yield path
    if was:
        gc.enable()
    # on windows a file that is still open cant be removed
    os.remove(path)


def test_held_sees_an_open_file(wav):
    d = _pydub2.Decoder(wav)
    assert held(wav)
    del d
    assert not held(wav)


@pytest.mark.parametrize("how", ["unstarted", "partly", "all", "dropped", "unstarted dropped", "all closed"])
def test_the_file_is_let_go_with_the_generator(wav, how):
    gen = AudioSegment.stream(wav, chunk_ms=500)
    assert held(wav)
    if not how.startswith("unstarted"):
        next(gen)
    if how.startswith("all"):
        assert len(list(gen)) == 5
    if how.endswith("dropped"):
        gen = None
    elif how != "all":
        gen.close()
    assert not held(wav)


def test_the_file_is_let_go_when_the_caller_keeps_the_error(wav, monkeypatch):
    # the traceback holds the frames and all they opened
    with pytest.raises(ValueError) as at_call:
        AudioSegment.stream(wav, start_second=-1)
    assert not held(wav)
    gen = AudioSegment.stream(wav, chunk_ms=500)
    with pytest.raises(RuntimeError) as thrown:
        gen.throw(RuntimeError("stop"))
    assert not held(wav)
    monkeypatch.setattr(pydub2, "max_duration", 1)
    gen = AudioSegment.stream(wav, chunk_ms=500)
    with pytest.raises(CouldntDecodeError) as in_stream:
        list(gen)
    assert not held(wav)
    with pytest.raises(CouldntDecodeError) as in_load:
        AudioSegment.from_file(wav)
    assert in_load.value.reason == "too_long"
    assert not held(wav)
    monkeypatch.setattr(utils, "read_capped", lambda d, n: 1 / 0)
    empty = wav + ".empty.wav"
    AudioSegment.empty().export(empty, "wav").close()
    with pytest.raises(ZeroDivisionError) as in_info:
        utils._native_info(io.BytesIO(), empty)
    assert not held(empty)
    os.remove(empty)
    del at_call, thrown, in_stream, in_load, in_info


def test_decoder_close(wav):
    d = _pydub2.Decoder(wav)
    assert len(d.read(100)) == 200
    d.close()
    assert not held(wav)
    d.close()
    for f in (d.read, lambda: d.read(10), lambda: d.seek(0)):
        with pytest.raises(ValueError, match="closed"):
            f()
    assert (d.rate, d.channels, d.width, d.frames, d.chained) == (8000, 1, 2, 24000, False)
    with open(wav, "rb") as f:
        d = _pydub2.Decoder(f.read())
    d.close()
    with pytest.raises(ValueError, match="closed"):
        d.read()


def test_close_while_another_thread_reads(wav):
    d = _pydub2.Decoder(wav)
    out = []

    def work():
        try:
            while True:
                d.seek(0)
                d.read(2000)
        except ValueError as e:
            out.append(str(e))
    t = [threading.Thread(target=work) for _ in range(4)]
    [x.start() for x in t]
    d.close()
    [x.join() for x in t]
    assert out == ["the decoder is closed"] * 4 and not held(wav)
