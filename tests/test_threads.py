import hashlib
import io
import os
import shutil
import subprocess
import sys
import sysconfig
import threading
import time

import pytest

import _pydub2
from pydub2 import AudioSegment, silence
from pydub2.generators import Sine, WhiteNoise

# PYDUB2_SOAK=1 for the long run
SOAK = bool(os.environ.get("PYDUB2_SOAK"))
SECONDS = 300 if SOAK else 3
THREADS = 16
EXTS = ["wav", "flac", "ogg", "opus", "mp3"]


class Ctx:
    pass


SEG = (Sine(440).to_audio_segment(600) + AudioSegment.silent(700, 44100) +
       WhiteNoise().to_audio_segment(700) + AudioSegment.silent(500, 44100)).set_channels(2)


def build(d):
    c = Ctx()
    c.seg = SEG
    c.files, c.blobs = {}, {}
    for ext in EXTS:
        path = os.path.join(str(d), "x." + ext)
        try:
            c.seg.export(path, format=ext)
        except Exception:
            continue
        c.files[ext] = path
        with open(path, "rb") as f:
            c.blobs[ext] = f.read()
    return c


def h(x):
    if isinstance(x, AudioSegment):
        return (x.frame_rate, x.channels, x.sample_width, hashlib.sha1(x.raw_data).hexdigest())
    if isinstance(x, bytes):
        return hashlib.sha1(x).hexdigest()
    return repr(x)


def tasks(c):
    t = {}
    seg = c.seg
    for ext in c.files:
        path, blob = c.files[ext], c.blobs[ext]
        t["path " + ext] = lambda path=path: h(AudioSegment.from_file(path))
        t["fileobj " + ext] = lambda path=path: h(AudioSegment.from_file(open(path, "rb")))
        t["bytesio " + ext] = lambda blob=blob, ext=ext: h(AudioSegment.from_file(io.BytesIO(blob), format=ext))
        t["same bytes " + ext] = lambda blob=blob: h(_pydub2.decode(blob)[0])
        t["decoder bytes " + ext] = lambda blob=blob: h(_pydub2.Decoder(blob).read())
        t["decoder seek " + ext] = lambda path=path: h((lambda d: (d.seek(1000), d.read(5000))[1])(_pydub2.Decoder(path)))
        t["stream " + ext] = lambda path=path: [h(x) for x in AudioSegment.stream(path, chunk_ms=300)]
        # roundtrip, opus and ogg writers pick a random stream serial
        t["export " + ext] = lambda ext=ext: h(AudioSegment.from_file(io.BytesIO(seg.export(format=ext).read()), format=ext))
    t["export raw"] = lambda: h(seg.export(format="raw").read())
    if shutil.which("ffmpeg"):
        t["export m4a"] = lambda: h(seg.export(format="m4a").read())
    t["slice"] = lambda: h(seg[123:2001])
    t["overlay"] = lambda: h(seg.overlay(seg[300:900], position=250, loop=True, gain_during_overlay=-6))
    t["gain"] = lambda: h(seg.apply_gain(-3.5) + seg[:100] * 3)
    t["append"] = lambda: h(seg.append(seg, crossfade=50))
    t["fade"] = lambda: h(seg.fade_in(200).fade_out(300).reverse())
    t["resample"] = lambda: h(seg.set_frame_rate(16000))
    t["resample 48k"] = lambda: h(seg.set_frame_rate(48000).set_channels(1).set_sample_width(4))
    t["measure"] = lambda: (seg.rms, seg.max, seg.dBFS, seg.loudness())
    t["detect silence"] = lambda: (silence.detect_silence(seg, 200, -40), silence.detect_nonsilent(seg, 200, -40))
    t["split on silence"] = lambda: [h(x) for x in silence.split_on_silence(seg, 200, -40)]
    t["detect speech"] = lambda: silence.detect_speech(seg)
    t["normalize"] = lambda: h(seg.normalize(2.0))
    t["compress"] = lambda: h(seg[:200].compress_dynamic_range())
    t["filter"] = lambda: h(seg[:300].low_pass_filter(3000).high_pass_filter(200))
    t["mono"] = lambda: [h(x) for x in seg.split_to_mono()]
    t["samples"] = lambda: hashlib.sha1(seg.get_array_of_samples().tobytes()).hexdigest()
    return t


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    return build(tmp_path_factory.mktemp("t"))


@pytest.fixture(scope="module")
def want(ctx):
    return {k: f() for k, f in tasks(ctx).items()}


def hammer(fns, n, seconds):
    """every thread cycles through all fns from a different start, returns what went wrong"""
    names = list(fns)
    bad = []
    go = threading.Barrier(n)
    end = [0]

    def work(i):
        k = i
        try:
            go.wait()
            end[0] = end[0] or time.time() + seconds
            while time.time() < end[0] and not bad:
                name = names[k % len(names)]
                k += 1
                fns[name]()
        except BaseException as e:
            bad.append((names[(k - 1) % len(names)], repr(e)))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    try:
        for x in ts:
            x.start()
        for x in ts:
            x.join()
    finally:
        sys.setswitchinterval(old)
    return bad


def test_same_result_in_threads(ctx, want):
    t = tasks(ctx)

    def check(k):
        if t[k]() != want[k]:
            raise AssertionError(k + " gave another result")

    assert hammer({k: (lambda k=k: check(k)) for k in t}, THREADS, SECONDS) == []


def test_private_objects(tmp_path, want):
    # every thread writes its own files and builds its own segments
    def one(i):
        d = tmp_path / str(i)
        d.mkdir()
        t = tasks(build(d))
        for k, f in t.items():
            if "m4a" not in k:
                assert f() == want[k], k

    bad = []

    def own(i):
        try:
            one(i)
        except BaseException as e:
            bad.append(repr(e))

    ts = [threading.Thread(target=own, args=(i,)) for i in range(THREADS)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    assert bad == []


DECODER = """
import sys, threading
import _pydub2
path = sys.argv[1]
d = _pydub2.Decoder(path)
n = d.frames
errors = []
def work(i):
    for k in range(300):
        try:
            if i % 2:
                d.seek((k * 977) % max(n, 1))
            else:
                d.read(1000 + k)
        except (RuntimeError, ValueError, OverflowError) as e:
            errors.append(e)
ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
[x.start() for x in ts]
[x.join() for x in ts]
print("done", len(errors))
"""


@pytest.mark.parametrize("ext", EXTS)
def test_one_decoder_two_threads(ctx, ext):
    # safe or a clean exception, never a crash
    if ext not in ctx.files:
        pytest.skip("no " + ext)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    p = subprocess.run([sys.executable, "-c", DECODER, ctx.files[ext]], capture_output=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr.decode()[-500:]


def test_stream_handed_between_threads(ctx):
    path = ctx.files["flac"]
    want = [h(x) for x in AudioSegment.stream(path, chunk_ms=100)]
    gen = AudioSegment.stream(path, chunk_ms=100)
    lock, got = threading.Lock(), []

    def pull():
        while True:
            with lock:
                x = next(gen, None)
                if x is None:
                    return
                got.append(h(x))

    ts = [threading.Thread(target=pull) for _ in range(8)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    assert got == want


@pytest.mark.skipif(sysconfig.get_config_var("Py_GIL_DISABLED"), reason="free threaded cpython crashes when threads advance one generator, plain python code does it too")
def test_stream_advanced_without_lock(ctx):
    # generators refuse a second thread, anything else would be memory corruption
    gen = AudioSegment.stream(ctx.files["flac"], chunk_ms=10)
    bad = []

    def pull():
        try:
            for _ in gen:
                pass
        except ValueError:
            pass
        except BaseException as e:
            bad.append(repr(e))

    ts = [threading.Thread(target=pull) for _ in range(8)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    assert bad == []


def test_declares_free_threading():
    code = "import sys, _pydub2; print(sys._is_gil_enabled())"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    if not sysconfig.get_config_var("Py_GIL_DISABLED"):
        pytest.skip("this python has a gil")
    assert p.stdout.strip() == "False", p.stderr


def test_summed_segment_joined_once():
    parts = [SEG[i * 100:(i + 1) * 100] for i in range(25)]
    want = b"".join(p.raw_data for p in parts)
    for _ in range(20):
        seg = sum(parts)
        go = threading.Barrier(THREADS)
        got = []

        def read(i):
            go.wait()
            got.append(seg.raw_data if i % 2 else vars(seg)["_data"])

        ts = [threading.Thread(target=read, args=(i,)) for i in range(THREADS)]
        for x in ts:
            x.start()
        for x in ts:
            x.join()
        assert len(got) == THREADS and got[0] == want
        assert all(g is got[0] for g in got) and "_parts" not in vars(seg)
