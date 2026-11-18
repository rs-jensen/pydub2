import io
import os
import shutil
import subprocess
import sys
import threading
import tracemalloc

import pytest

import pydub2
import pydub2.generators
from pydub2 import AudioSegment, utils
from pydub2.exceptions import CouldntDecodeError

ffmpeg = shutil.which("ffmpeg")
need_ffmpeg = pytest.mark.skipif(ffmpeg is None or shutil.which("ffprobe") is None, reason="no ffmpeg")
need_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs fifos")
FORMATS = ["wav", "flac", "ogg", "opus", "mp3", pytest.param("m4a", marks=need_ffmpeg)]
TOO_BIG = "^this file is larger than max_file_size$"


def audio(format):
    if format == "m4a":
        return subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                               "-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-f", "ipod", "-"], capture_output=True, check=True).stdout
    seg = pydub2.generators.Sine(440, sample_rate=16000).to_audio_segment(duration=1000)
    return seg.export(format=format).read()


class Counted(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.taken = 0

    def read(self, *a):
        b = super().read(*a)
        self.taken += len(b)
        return b


class Pipe(io.RawIOBase):
    """A socket or a pipe, it cant seek. An endless one gives zeros after the file for as long as anyone reads."""

    def __init__(self, data, endless=False):
        self.data, self.endless, self.taken = data, endless, 0

    def readable(self):
        return True

    def readinto(self, b):
        # far past any limit here, a load that gets this far would read for ever
        assert self.taken < 64 << 20, "read on without a stop"
        head = self.data[self.taken:self.taken + len(b)]
        n = len(b) if self.endless else len(head)
        b[:n] = head + b"\0" * (n - len(head))
        self.taken += n
        return n


def feed(fifo, data):
    def run():
        try:
            with open(fifo, "wb") as f:
                f.write(data)
        except BrokenPipeError:
            pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


@pytest.fixture
def cap(monkeypatch):
    monkeypatch.setattr(pydub2, "max_duration", None)
    monkeypatch.setattr(pydub2, "max_bytes", None)
    return lambda n: monkeypatch.setattr(pydub2, "max_file_size", n)


def test_off_by_default():
    assert pydub2.max_file_size is None


@pytest.mark.parametrize("format", FORMATS)
def test_a_file_object_over_the_limit_is_not_read(cap, format):
    data = audio(format)
    cap(len(data) - 1)
    f = Counted(data)
    with pytest.raises(CouldntDecodeError, match=TOO_BIG) as e:
        AudioSegment.from_file(f, format)
    assert e.value.reason == "too_big"
    assert f.taken == 0 and f.tell() == 0
    if format != "m4a":
        with pytest.raises(CouldntDecodeError, match=TOO_BIG):
            AudioSegment.stream(f)
        assert f.taken == 0


@pytest.mark.parametrize("format", FORMATS)
def test_an_endless_stream_stops_at_the_limit(cap, format):
    cap(1 << 20)
    if format != "wav":
        # pydub rewinds a wav, which a pipe cant do
        f = Pipe(audio(format), True)
        with pytest.raises(CouldntDecodeError, match=TOO_BIG):
            AudioSegment.from_file(f, format)
        assert f.taken == (1 << 20) + 1
    if format != "m4a":
        f = Pipe(audio(format), True)
        with pytest.raises(CouldntDecodeError, match=TOO_BIG):
            AudioSegment.stream(f)
        assert f.taken == (1 << 20) + 1


class Grows(Counted):
    """Says it is small when asked for its size, then gives more."""

    def __init__(self, data, said):
        super().__init__(data)
        self.said = said

    def seek(self, pos, whence=0):
        at = super().seek(pos, whence)
        return min(at, self.said) if whence == 2 else at


@pytest.mark.parametrize("format", ["flac", "ogg"])
def test_a_file_that_grows_after_its_size_is_read_stops(cap, format):
    data = audio(format) * 3
    cap(len(data) // 3)
    for load in (lambda f: AudioSegment.from_file(f, format), AudioSegment.stream):
        f = Grows(data, len(data) // 3)
        with pytest.raises(CouldntDecodeError, match=TOO_BIG):
            load(f)
        assert f.taken == len(data) // 3 + 1


@pytest.mark.parametrize("prober", [False, pytest.param(True, marks=need_ffmpeg)])
@pytest.mark.parametrize("probe", [utils.mediainfo, utils.mediainfo_json])
def test_mediainfo_refuses_a_file_object_over_the_limit(cap, monkeypatch, prober, probe):
    if not prober:
        monkeypatch.setattr(utils, "_has_prober", lambda: None)
    data = audio("flac")
    cap(len(data) - 1)
    f = Counted(data)
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        probe(f)
    assert f.taken == 0
    cap(len(data))
    assert probe(Counted(data))


def test_data_and_spawn_refuse_a_file_object_over_the_limit(cap):
    data = audio("wav")
    seg = AudioSegment.from_file(io.BytesIO(data), "wav")
    cap(len(data) - 1)
    f = Counted(data)
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        AudioSegment(data=f)
    assert f.taken == 0
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        seg._spawn(f)
    assert f.taken == 0
    cap(len(data))
    assert AudioSegment(data=Counted(data)).raw_data == seg.raw_data


class Idle(io.RawIOBase):
    """A non-blocking file with nothing to give yet."""

    def readable(self):
        return True

    def read(self, *a):
        return None


@pytest.mark.parametrize("limit", [None, 1 << 20])
def test_a_read_that_gives_none_is_no_data(cap, limit):
    cap(limit)
    # pydub hands ffmpeg nothing and raises CouldntDecodeError
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(Idle(), "flac")
    with pytest.raises(CouldntDecodeError):
        list(AudioSegment.stream(Idle()))


def test_a_float_limit_stops_a_pipe(cap):
    cap(1e6)
    f = Pipe(audio("flac"), True)
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        AudioSegment.from_file(f, "flac")
    assert f.taken == 1000001


def test_endless_raw_input_stops_at_the_limit(cap):
    cap(1 << 20)
    f = Pipe(b"", True)
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        AudioSegment.from_file(f, format="raw", sample_width=2, frame_rate=8000, channels=1)
    assert f.taken == (1 << 20) + 1


def test_an_endless_stream_takes_little_memory(cap):
    # with max_bytes alone this read on to the 64 MB stop above and peaked at 64 MB, now it peaks at 3 MB
    cap(1 << 20)
    pydub2.max_bytes = 1 << 20
    f = Pipe(audio("flac"), True)
    tracemalloc.start()
    try:
        with pytest.raises(CouldntDecodeError, match=TOO_BIG):
            AudioSegment.from_file(f, "flac")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 4 << 20


@need_fifo
@pytest.mark.parametrize("format", ["flac", "ogg", pytest.param("m4a", marks=need_ffmpeg)])
def test_a_fifo_over_the_limit_stops(tmp_path, cap, format):
    data = audio(format)
    fifo = str(tmp_path / ("pipe." + format))
    os.mkfifo(fifo)
    # far more than the limit, the writer is cut off when the load stops
    t = feed(fifo, data + b"\0" * (32 << 20))
    cap(1 << 20)
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        AudioSegment.from_file(fifo)
    t.join(30)
    assert not t.is_alive()


@pytest.mark.parametrize("format", FORMATS)
def test_a_file_on_disk_over_the_limit_is_not_read(tmp_path, cap, format):
    data = audio(format)
    path = tmp_path / ("a." + format)
    path.write_bytes(data)
    cap(len(data) - 1)
    with open(path, "rb") as f:
        with pytest.raises(CouldntDecodeError, match=TOO_BIG):
            AudioSegment.from_file(f, format)
        assert f.tell() == 0
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        AudioSegment.from_file(str(path))
    if format != "m4a":
        with pytest.raises(CouldntDecodeError, match=TOO_BIG):
            AudioSegment.stream(str(path))


@need_fifo
@pytest.mark.parametrize("format", FORMATS)
@pytest.mark.parametrize("strict", [False, True])
def test_at_the_limit_every_source_gives_the_same_samples(tmp_path, monkeypatch, cap, format, strict):
    if strict and ffmpeg is None:
        pytest.skip("strict mode needs ffmpeg")
    if strict and format == "opus":
        pytest.skip("ffmpeg has no opus demuxer, so pydub cant load it with format='opus'")
    monkeypatch.setattr(pydub2, "strict", strict)
    data = audio(format)
    path = tmp_path / ("a." + format)
    path.write_bytes(data)
    want = AudioSegment.from_file(io.BytesIO(data), format).raw_data
    cap(len(data))

    def fifo():
        name = str(tmp_path / ("pipe." + format))
        if os.path.exists(name):
            os.unlink(name)
        os.mkfifo(name)
        feed(name, data)
        return name
    sources = [lambda: io.BytesIO(data), lambda: str(path), lambda: open(path, "rb"), lambda: Pipe(data),
               lambda: io.BufferedReader(Pipe(data))]
    for src in sources:
        if format != "m4a" and not strict:
            assert b"".join(c.raw_data for c in AudioSegment.stream(src())) == want
    if format != "wav":
        # pydub rewinds a wav, which a pipe cant do
        sources.append(fifo)
    else:
        sources = sources[:3]
    for src in sources:
        assert AudioSegment.from_file(src(), format).raw_data == want


@pytest.mark.parametrize("format", FORMATS)
def test_at_the_limit_a_file_object_loads(cap, format):
    data = audio(format)
    want = AudioSegment.from_file(io.BytesIO(data), format).raw_data
    cap(len(data))
    f = Counted(data)
    assert AudioSegment.from_file(f, format).raw_data == want
    assert f.taken == len(data)
    cap(len(data) - 1)
    with pytest.raises(CouldntDecodeError, match=TOO_BIG):
        AudioSegment.from_file(io.BytesIO(data), format)


@need_fifo
@pytest.mark.parametrize("format", ["flac", "ogg", pytest.param("m4a", marks=need_ffmpeg)])
def test_at_the_limit_a_fifo_loads(tmp_path, cap, format):
    data = audio(format)
    want = AudioSegment.from_file(io.BytesIO(data), format).raw_data
    fifo = str(tmp_path / ("pipe." + format))
    os.mkfifo(fifo)
    feed(fifo, data)
    cap(len(data))
    assert AudioSegment.from_file(fifo).raw_data == want


def test_without_a_limit_the_file_is_read_as_before(cap):
    calls = []

    class Upload(io.BytesIO):
        def read(self, *a):
            calls.append(a)
            return super().read(*a)
    data = audio("flac")
    for v in (None, float("inf"), 2 ** 70):
        cap(v)
        calls.clear()
        assert len(AudioSegment.from_file(Upload(data), "flac")) == 1000
        assert calls == [()]


def test_odd_values_raise(tmp_path, cap):
    for v in (-1, float("nan"), "10"):
        cap(v)
        with pytest.raises(ValueError, match="^pydub2.max_file_size has to be None or a number"):
            AudioSegment.from_file(io.BytesIO(audio("wav")))
        with pytest.raises(ValueError, match="^pydub2.max_file_size has to be None or a number"):
            AudioSegment.stream(io.BytesIO(audio("wav")))


def test_a_pipe_from_a_process(cap):
    if sys.platform == "win32" or shutil.which("cat") is None:
        pytest.skip("needs cat")
    data = audio("ogg")
    want = AudioSegment.from_file(io.BytesIO(data), "ogg").raw_data
    for limit, ok in ((len(data), True), (len(data) - 1, False)):
        cap(limit)
        p = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        threading.Thread(target=lambda: (p.stdin.write(data), p.stdin.close()), daemon=True).start()
        try:
            if ok:
                assert AudioSegment.from_file(p.stdout, "ogg").raw_data == want
            else:
                with pytest.raises(CouldntDecodeError, match=TOO_BIG):
                    AudioSegment.from_file(p.stdout, "ogg")
        finally:
            p.stdout.close()
            p.wait()
