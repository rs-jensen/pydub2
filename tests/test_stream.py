import array
import gc
import io
import os
import pathlib
import re
import struct
import subprocess
import sys

import pytest

from pydub2 import AudioSegment
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


def open_files():
    out = 0
    for fd in range(256):
        try:
            os.fstat(fd)
            out += 1
        except OSError:
            pass
    return out


@pytest.mark.parametrize("how", ["unstarted", "partly", "all", "dropped", "unstarted dropped", "all closed"])
def test_the_file_is_let_go_with_the_generator(tmp_path, how):
    path = tmp_path / "a.wav"
    AudioSegment.silent(3000, 8000).export(str(path), "wav")
    before = open_files()
    # the file has to go when the last reference does, not when the cycle collector gets to it
    gc.disable()
    try:
        gen = AudioSegment.stream(str(path), chunk_ms=500)
        if not how.startswith("unstarted"):
            next(gen)
        if how.startswith("all"):
            assert len(list(gen)) == 5
        if how.endswith("dropped"):
            gen = None
        elif how != "all":
            gen.close()
        assert open_files() == before
    finally:
        gc.enable()
    # on windows a file that is still open cant be removed
    os.remove(path)
