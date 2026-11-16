import gc
import io
import os
import shutil
import subprocess
import tempfile
import time
import tracemalloc

import pytest

import _pydub2
from pydub2 import AudioSegment, silence

from test_threads import EXTS, SEG, build

# LEAK_N=20000 for the long run
N = int(os.environ.get("LEAK_N", 16))
# ps rss wanders a lot over a few loops, only the long run can tell a small leak
SLACK = (1 if N >= 2000 else 16) << 20
FF = shutil.which("ffmpeg")


def rss():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except OSError:
        return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())])) * 1024


def fds():
    return len(os.listdir("/dev/fd"))


def failing(f, must=True):
    def run():
        try:
            f()
        except Exception:
            return
        assert not must, "did not fail"
    return run


class Pull:
    def __init__(self, c):
        self.path = c.files["flac"]

    def __call__(self):
        g = AudioSegment.stream(self.path, chunk_ms=100)
        next(g)
        next(g)


def ops(c):
    seg, o = SEG, {}
    garbage = os.urandom(5000)
    for ext in c.files:
        path, blob = c.files[ext], c.blobs[ext]
        o[("load path " + ext, 1)] = lambda path=path: AudioSegment.from_file(path)
        o[("load bytesio " + ext, 1)] = lambda blob=blob, ext=ext: AudioSegment.from_file(io.BytesIO(blob), format=ext)
        o[("load fileobj " + ext, 1)] = lambda path=path: AudioSegment.from_file(open(path, "rb"))
        o[("decode " + ext, 1)] = lambda blob=blob: _pydub2.decode(blob)
        o[("decoder unread " + ext, 1)] = lambda path=path: _pydub2.Decoder(path)
        o[("decoder half " + ext, 1)] = lambda blob=blob: _pydub2.Decoder(blob).read(1000)
        o[("decoder seek " + ext, 1)] = lambda path=path: (lambda d: (d.seek(2000), d.read()))(_pydub2.Decoder(path))
        o[("truncated " + ext, 1)] = failing(lambda blob=blob: _pydub2.decode(blob[:len(blob) // 3]), must=False)
        o[("export " + ext, 1 if ext in ("wav", "flac") else 20)] = lambda ext=ext: seg.export(io.BytesIO(), format=ext)
    o[("export raw", 1)] = lambda: seg.export(io.BytesIO(), format="raw")
    o[("stream abandoned", 1)] = Pull(c)
    o[("stream whole", 1)] = lambda: list(AudioSegment.stream(c.files["wav"], chunk_ms=100))
    o[("garbage decode", 1)] = failing(lambda: _pydub2.decode(garbage))
    o[("garbage decoder", 1)] = failing(lambda: _pydub2.Decoder(garbage))
    o[("missing decoder", 1)] = failing(lambda: _pydub2.Decoder("/no/such/file.wav"))
    o[("bad encode format", 1)] = failing(lambda: _pydub2.encode(seg.raw_data, 44100, 2, 2, "nope"))
    o[("bad encode frames", 1)] = failing(lambda: _pydub2.encode(b"abc", 44100, 2, 2, "wav"))
    o[("bad rms", 1)] = failing(lambda: _pydub2.rms(b"abc", 9))
    o[("failing writer", 1)] = failing(lambda: seg.export(type("W", (), {"seek": lambda s, n: 0})(), format="wav"))
    if FF:
        o[("garbage file", 20)] = failing(lambda: AudioSegment.from_file(io.BytesIO(garbage), format="wav"))
        o[("export m4a", 20)] = lambda: seg.export(io.BytesIO(), format="m4a")
    s = seg[:200]
    o[("slice add", 1)] = lambda: seg[100:900] + seg[:300] * 2
    o[("overlay", 1)] = lambda: seg.overlay(s, position=100, loop=True, gain_during_overlay=-3)
    o[("gain fade reverse", 10)] = lambda: seg.apply_gain(2).fade_in(100).fade_out(100).reverse()
    o[("append", 10)] = lambda: seg.append(s, crossfade=50)
    o[("resample", 1)] = lambda: seg.set_frame_rate(16000)
    o[("width channels", 1)] = lambda: seg.set_sample_width(1).set_sample_width(4).set_channels(1)
    o[("split mono", 1)] = lambda: seg.split_to_mono()
    o[("measure", 1)] = lambda: (seg.rms, seg.max, seg.dBFS, seg.loudness, seg.get_dc_offset())
    o[("dc offset", 1)] = lambda: seg.remove_dc_offset()
    o[("silence", 1)] = lambda: (silence.detect_silence(seg, 200, -40), silence.split_on_silence(seg, 200, -40))
    o[("speech", 10)] = lambda: silence.detect_speech(seg)
    o[("normalize", 1)] = lambda: seg.normalize()
    o[("pan invert", 1)] = lambda: seg.pan(0.5).invert_phase().apply_gain_stereo(-1, 2)
    o[("strip silence", 1)] = lambda: seg.strip_silence(200, -40, 10)
    o[("speedup", 10)] = lambda: seg.speedup(1.5)
    o[("compress", 20)] = lambda: s.compress_dynamic_range()
    o[("filters", 20)] = lambda: s.low_pass_filter(3000).high_pass_filter(200)
    o[("numpy", 1)] = lambda: AudioSegment.from_numpy(seg.to_numpy(), seg.frame_rate)
    o[("samples", 10)] = lambda: seg.get_array_of_samples().tolist()
    return o


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    return build(tmp_path_factory.mktemp("leak"))


def test_ops_leak_nothing(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    bad = []
    o = ops(ctx)
    tracemalloc.start()
    for f in o.values():
        f()
    for (name, slow), f in o.items():
        n = max(N // slow, 4)
        for _ in range(n // 4):
            f()
        gc.collect()
        fd0, files0, sizes = fds(), len(os.listdir(tmp_path)), [rss()]
        py0 = tracemalloc.get_traced_memory()[0]
        t0 = time.time()
        for _ in range(4):
            for _ in range(n // 4):
                f()
            gc.collect()
            sizes.append(rss())
        py = tracemalloc.get_traced_memory()[0] - py0
        up = sizes[-1] - sizes[0]
        print(name, n, "rss", [x >> 10 for x in sizes], "py %+d b" % py, "%.1fs" % (time.time() - t0), flush=True)
        # only a leak climbs at every step, malloc caches and compressed pages wander
        creeping = all(b > a for a, b in zip(sizes, sizes[1:])) and up > SLACK
        if fds() != fd0 or len(os.listdir(tmp_path)) != files0 or py > 1 << 18 or creeping:
            bad.append((name, "fds %d" % (fds() - fd0), "tmp %d" % (len(os.listdir(tmp_path)) - files0),
                        "py %d" % py, "rss %d" % up))
    tracemalloc.stop()
    assert bad == []
