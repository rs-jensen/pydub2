import array
import faulthandler
import importlib
import io
import itertools
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import types

import pytest

np = pytest.importorskip("numpy")
pydub = pytest.importorskip("pydub")
import pydub2

INF = float("inf")
NAN = float("nan")
TIMEOUT = 2
RATES = [8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000]


class Timeout(BaseException):
    pass


def on_alarm(sig, frame):
    raise Timeout()


class Lib:
    def __init__(self, name):
        self.AS = importlib.import_module(name).AudioSegment
        for m in ("silence", "generators", "utils"):
            setattr(self, m, importlib.import_module(name + "." + m))


PD = Lib("pydub")
P2 = Lib("pydub2")


def rate(r):
    k = r.random()
    if k < 0.8:
        return r.choice(RATES)
    if k < 0.95:
        return r.randint(1000, 96000)
    return r.choice([1, 2, 3, 7, 100, 500])


def mkspec(r, small=False):
    rt = rate(r)
    k = r.random()
    if k < 0.1:
        n = r.randint(0, 5)
    elif k < 0.2:
        n = r.choice([7, 8, 1023, 1024, 1025])
    else:
        top = 300 if small or r.random() < 0.95 else 3000
        n = int(rt * r.randint(1, top) / 1000)
    return dict(w=r.choice([1, 2, 4]), ch=r.choice([1, 2]), rate=rt, n=n,
                kind=r.choice(["silence", "full", "noise", "noise", "sine", "sine", "dc"]),
                seed=r.getrandbits(32), extra=r.random() < 0.02)


def build(L, sp):
    w, ch, n = sp["w"], sp["ch"], sp["n"]
    rg = np.random.RandomState(sp["seed"])
    hi = 2 ** (8 * w - 1) - 1
    lo = -hi - 1
    kind = sp["kind"]
    if kind == "silence":
        a = np.zeros((n, ch))
    elif kind == "full":
        a = np.where(rg.rand(n, ch) < 0.5, hi, lo) if rg.rand() < 0.5 else np.full((n, ch), hi if rg.rand() < 0.5 else lo)
    elif kind == "noise":
        a = rg.randint(lo, hi + 1, (n, ch), dtype=np.int64) * rg.choice([1, 0.1, 0.001])
    elif kind == "sine":
        t = np.arange(n)[:, None]
        f = rg.uniform(1, max(1.0, sp["rate"] / 2))
        a = rg.choice([1, 0.5, 0.01]) * hi * np.sin(2 * np.pi * f * t / sp["rate"] + rg.uniform(0, 6, (1, ch)))
    else:
        a = rg.uniform(-1, 1) * hi * 0.8 + rg.randint(-100, 100, (n, ch))
    a = np.clip(np.round(a), lo, hi).astype({1: "<i1", 2: "<i2", 4: "<i4"}[w])
    data = a.tobytes() + (b"\x01" if sp["extra"] else b"")
    return L.AS(data=data, sample_width=w, frame_rate=sp["rate"], channels=ch)


def ms(r, big=True):
    k = r.random()
    if k < 0.5:
        return r.randint(0, 400)
    if k < 0.6:
        return r.randint(-400, -1)
    if k < 0.7:
        return r.uniform(-50, 500)
    x = r.choice([0, 1, -1, None, INF, -INF, 10 ** 9, -10 ** 9, 10 ** 18, 2 ** 63, NAN, 0.5, 1e-9, True])
    return x if big or not isinstance(x, int) or abs(x) < 10 ** 6 else 1


def db(r):
    k = r.random()
    if k < 0.5:
        return r.choice([0, 1, -1, 3, -3, 6, -6, 12, -12, 20, -20, 40, -40, 60, -60, 0.5, -0.5])
    if k < 0.75:
        return r.uniform(-100, 100)
    return r.choice([0.0, -0.0, 1e3, -1e3, 1e9, -1e9, 1e-9, INF, -INF, NAN, None, "x", True])


def sg(r):
    if r.random() < 0.7:
        return ("@new", mkspec(r, True))
    return ("@cur", ms(r), ms(r))


def pos(r):
    return r.choice([0, 0, 0, 1, 10, 100, 250, -10, 1e9, -1e9, None, INF, 12.5, r.randint(0, 300)])


def pick_kw(r, **opts):
    return {k: v for k, v in opts.items() if r.random() < 0.5}


def key(r):
    f = r.random()
    if f < 0.15:
        return ms(r)
    if f < 0.7:
        return ("@slice", ms(r), ms(r), None)
    if f < 0.8:
        return ("@slice", ms(r), None, None)
    if f < 0.9:
        return ("@slice", None, ms(r), None)
    if f < 0.95:
        return ("@slice", ms(r), ms(r), r.choice([0, 7, 50, 100, -10, 3000, None]))
    return r.choice(["x", None, [1], 1.5])


def g_fade(r):
    return (), pick_kw(r, to_gain=db(r), from_gain=db(r), start=ms(r), end=ms(r), duration=ms(r, False))


def g_mono(r):
    if r.random() < 0.6:
        return tuple(("@mono", r.randint(0, 3)) for _ in range(r.randint(0, 3))), {}
    sp = mkspec(r, True)
    sp["ch"] = 1
    return tuple(("@new", dict(sp, seed=r.getrandbits(32), n=sp["n"] if r.random() < 0.8 else sp["n"] + 1))
                 for _ in range(r.randint(1, 3))), {}


def g_overlay(r):
    return (sg(r),), pick_kw(r, position=pos(r), loop=r.random() < 0.5,
                             times=r.choice([None, 0, 1, 2, 3, -1, 5, 1.5]), gain_during_overlay=db(r))


def g_silent(r):
    return (), pick_kw(r, duration=r.choice([0, 1, 10, 100, 250.5, -5, None, r.randint(0, 500)]),
                       frame_rate=r.choice([rate(r), 0, -1, 3.5]))


def thresh(r):
    return r.choice([-16, -50, -30, -3, 0, 5, -INF, NAN, -16.5, r.randint(-90, 0)])


def silkw(r):
    kw = pick_kw(r, min_silence_len=r.choice([0, 1, 5, 10, 50, 100, 300, -1, 20.5, r.randint(1, 200)]),
                 silence_thresh=thresh(r),
                 seek_step=r.choice([1, 1, 2, 5, 10, 0, -1, 1.5, 10 ** 6]))
    return (), kw


def g_split(r):
    a, kw = silkw(r)
    if r.random() < 0.5:
        kw["keep_silence"] = r.choice([0, 100, 10, True, False, -5, 1e9, 20.5])
    return a, kw


def g_gen(r):
    cls = r.choice(["Sine", "Square", "Triangle", "Sawtooth", "Pulse", "WhiteNoise"])
    kw = pick_kw(r, sample_rate=r.choice([rate(r), 0, 44100]), bit_depth=r.choice([8, 16, 24, 32, 16, 16, 0, 12]))
    a = [] if cls == "WhiteNoise" else [r.choice([440, 1000, 50.5, 0, -100, 1e9, 22050, 3, r.randint(1, 5000)])]
    if cls == "Pulse" and r.random() < 0.5:
        a.append(r.choice([0.5, 0, 1, 0.1, -0.5, 2]))
    return (cls, a, kw, r.choice([0, 1, 10, 100, 250.5, -5, r.randint(0, 300)]), r.choice([0, -3, -20, 6, 0.0, INF, NAN])), {}


def g_gen_samples(r):
    (cls, a, kw, _, _), _ = g_gen(r)
    return (cls, a, kw, r.randint(0, 300)), {}


def g_eqhash(r):
    return (sg(r),), {}


def cutoff(r):
    return r.choice([0, 1, 100, 1000, 5000, 20000, 1e9, -5, 440.5, NAN, None, r.randint(1, 20000)])


# name -> (weight, gen returning (args, kwargs))
OPS = {
    "getitem": (6, lambda r: ((key(r),), {})),
    "add": (3, lambda r: ((db(r) if r.random() < 0.5 else sg(r),), {})),
    "radd": (1, lambda r: ((r.choice([0, 0, 1, db(r), None]),), {})),
    "sub": (2, lambda r: ((db(r) if r.random() < 0.8 else sg(r),), {})),
    "mul": (2, lambda r: ((r.choice([0, 1, 2, 3, 5, -1, 1.5, 0.5, None, True, sg(r), sg(r)]),), {})),
    "s.overlay": (6, g_overlay),
    "s.append": (4, lambda r: ((sg(r),), pick_kw(r, crossfade=r.choice([0, 0, 100, 50, 10, r.randint(0, 300), -5, 1e9, None, 12.5, INF])))),
    "s.fade": (5, g_fade),
    "s.fade_in": (2, lambda r: ((ms(r, False),), {})),
    "s.fade_out": (2, lambda r: ((ms(r, False),), {})),
    "s.apply_gain": (4, lambda r: ((db(r),), {})),
    "s.apply_gain_stereo": (3, lambda r: ((), pick_kw(r, left_gain=db(r), right_gain=db(r)))),
    "s.pan": (3, lambda r: ((r.choice([0, 1, -1, 0.5, -0.5, 0.25, 2, -2, NAN, INF, None, r.uniform(-1, 1)]),), {})),
    "s.reverse": (2, lambda r: ((), {})),
    "s.set_frame_rate": (4, lambda r: ((r.choice([rate(r), 0, -1, 3.5, None, 1, 100000]),), {})),
    "s.set_channels": (3, lambda r: ((r.choice([1, 2, 1, 2, 0, 3, -1, None, 1.5]),), {})),
    "s.set_sample_width": (3, lambda r: ((r.choice([1, 2, 4, 3, 0, 5, -1, None, 2.5]),), {})),
    "s.split_to_mono": (2, lambda r: ((), {})),
    "AS.from_mono_audiosegments": (2, g_mono),
    "s.get_array_of_samples": (2, lambda r: ((r.choice([None, None, "h", "i", "b", "B", "l", "f", "q", "x"]),), {})),
    "s.get_sample_slice": (2, lambda r: ((), pick_kw(r, start_sample=r.choice([0, 1, 10, 1000, -1, None, 1e9, 2.5, -1e9]), end_sample=r.choice([0, 1, 10, 1000, -1, None, 1e9, 2.5])))),
    "s.get_frame": (2, lambda r: ((r.choice([0, 1, 5, 100, -1, 10 ** 9, 1.5, None, r.randint(0, 3000)]),), {})),
    "s.frame_count": (2, lambda r: ((), pick_kw(r, ms=ms(r)))),
    "AS.silent": (1, g_silent),
    "AS.empty": (1, lambda r: ((), {})),
    "s.normalize": (2, lambda r: ((), pick_kw(r, headroom=r.choice([0.1, 0, 3, -1, 20, 100, INF, NAN, None, 0.5])))),
    "s.compress_dynamic_range": (2, lambda r: ((), pick_kw(r, threshold=r.choice([-20.0, -10, 0, -60, 5, -INF]), ratio=r.choice([4.0, 1, 2, 10, 0, -1, INF]), attack=r.choice([5.0, 0, 1, 50, 0.1, -1]), release=r.choice([50.0, 0, 10, 500, -1])))),
    "s.speedup": (2, lambda r: ((), pick_kw(r, playback_speed=r.choice([1.5, 1, 2, 0.5, 3, 1.01, 0, -1, INF]), chunk_size=r.choice([150, 50, 10, 500, 1, 0, -5, 20.5]), crossfade=r.choice([25, 0, 5, 100, -1, 10.5])))),
    "s.strip_silence": (2, lambda r: ((), pick_kw(r, silence_len=r.choice([1000, 1, 10, 50, 100, 0, -1, 20.5]), silence_thresh=thresh(r), padding=r.choice([100, 0, 10, 1000, -5, 20.5, None])))),
    "s.invert_phase": (2, lambda r: ((r.choice([(1, 1), (1, 1), (1, 0), (0, 1), (0, 0), (2, 3), (1,), None, (1, 1, 1)]),), {})),
    "s.low_pass_filter": (2, lambda r: ((cutoff(r),), {})),
    "s.high_pass_filter": (2, lambda r: ((cutoff(r),), {})),
    "s.remove_dc_offset": (2, lambda r: ((), pick_kw(r, channel=r.choice([None, 1, 2, 3, 0, -1]), offset=r.choice([None, 0, 0.1, -0.5, 1.5, 5, NAN, INF, 0.0001])))),
    "s.get_dc_offset": (2, lambda r: ((), pick_kw(r, channel=r.choice([1, 2, 3, 0, -1, None])))),
    "silence.detect_silence": (3, silkw),
    "silence.detect_nonsilent": (3, silkw),
    "silence.split_on_silence": (3, g_split),
    "silence.detect_leading_silence": (2, lambda r: ((), pick_kw(r, silence_threshold=thresh(r), chunk_size=r.choice([10, 1, 5, 50, 100, 7.5, 10, 10, 10, 20] if r.random() < 0.99 else [0, -1])))),
    "utils.make_chunks": (2, lambda r: ((r.choice([10, 50, 100, 250, 1000, 37.5, 7, 0 if r.random() < 0.3 else 20, -1, None, 10 ** 9]),), {})),
    "gen": (2, g_gen),
    "gen_samples": (1, g_gen_samples),
    "sum": (1, lambda r: ((tuple(sg(r) for _ in range(r.randint(0, 4))),), {})),
    "eqhash": (1, g_eqhash),
    "wav": (2, lambda r: ((), {})),
    "raw": (1, lambda r: ((), {})),
    "iter": (1, lambda r: ((), {})),
}
NAMES = list(OPS)
WEIGHTS = [OPS[k][0] for k in NAMES]


def sane(op, a, kw, r):
    th = lambda: r.choice([-16, -50, -30, 0, -3.5])
    fix = {"seek_step": lambda: r.choice([1, 2, 5, 10]), "min_silence_len": lambda: r.randint(0, 200),
           "silence_thresh": th, "silence_threshold": th, "keep_silence": lambda: r.choice([0, 100, 10, True, False]),
           "chunk_size": lambda: r.choice([1, 5, 10, 50, 150, 500]), "silence_len": lambda: r.randint(1, 300),
           "padding": lambda: r.choice([0, 10, 100]), "playback_speed": lambda: r.choice([1.5, 1, 2, 0.5, 3]),
           "crossfade": lambda: r.choice([25, 0, 5, 100]), "channel": lambda: r.choice([1, 2]),
           "offset": lambda: r.choice([0.01, -0.01]), "sample_rate": lambda: rate(r), "frame_rate": lambda: rate(r),
           "duration": lambda: r.randint(0, 300)}
    if op in ("s.fade", "s.append", "s.overlay"):
        return a, kw
    kw = {k: fix[k]() if k in fix else v for k, v in kw.items()}
    if op == "s.set_sample_width":
        a = (r.choice([1, 2, 4]),)
    elif op == "s.set_frame_rate":
        a = (rate(r),)
    elif op == "s.set_channels":
        a = (r.choice([1, 2]),)
    return a, kw


def make(seed):
    r = random.Random(seed)
    spec = mkspec(r)
    steps = []
    for _ in range(r.randint(1, 8)):
        op = r.choices(NAMES, WEIGHTS)[0]
        a, kw = OPS[op][1](r)
        if seed % 2:
            a, kw = sane(op, a, kw, r)
        steps.append((op, a, kw, r.getrandbits(8)))
    return spec, steps


def res(L, s, x):
    if type(x) is tuple and x and isinstance(x[0], str) and x[0][:1] == "@":
        if x[0] == "@new":
            return build(L, x[1])
        if x[0] == "@cur":
            return s[x[1]:x[2]]
        if x[0] == "@slice":
            return slice(*x[1:])
        m = s.split_to_mono()
        return m[x[1] % len(m)]
    if type(x) in (list, tuple):
        return type(x)(res(L, s, i) for i in x)
    if type(x) is dict:
        return {k: res(L, s, v) for k, v in x.items()}
    return x


def sp_eqhash(L, s, o):
    c = L.AS(data=s.raw_data, sample_width=s.sample_width, frame_rate=s.frame_rate, channels=s.channels)
    return [s == c, hash(s) == hash(c), s != o, s == o, s == 5, s == None, hash(s) == hash(o)]


def sp_wav(L, s):
    b = io.BytesIO()
    s.export(b, format="wav")
    d = b.getvalue()
    return [d, L.AS.from_wav(io.BytesIO(d))]


def sp_raw(L, s):
    b = io.BytesIO()
    s.export(b, format="raw")
    return b.getvalue()


def sp_gen(L, s, cls, a, kw, dur, vol):
    return getattr(L.generators, cls)(*a, **kw).to_audio_segment(duration=dur, volume=vol)


def sp_gen_samples(L, s, cls, a, kw, n):
    return list(itertools.islice(getattr(L.generators, cls)(*a, **kw).generate(), n))


SPECIAL = {
    "getitem": lambda L, s, k: s[k],
    "add": lambda L, s, x: s + x,
    "radd": lambda L, s, x: x + s,
    "sub": lambda L, s, x: s - x,
    "mul": lambda L, s, x: s * x,
    "sum": lambda L, s, xs: sum(xs),
    "eqhash": sp_eqhash,
    "wav": sp_wav,
    "raw": sp_raw,
    "iter": lambda L, s: list(itertools.islice(iter(s), 40)),
    "gen": sp_gen,
    "gen_samples": sp_gen_samples,
}


def call(L, s, op, a, k):
    if op in SPECIAL:
        return SPECIAL[op](L, s, *a, **k)
    head, name = op.split(".")
    if head == "s":
        return getattr(s, name)(*a, **k)
    if head == "AS":
        return getattr(L.AS, name)(*a, **k)
    return getattr(getattr(L, head), name)(s, *a, **k)


PROPS = ["frame_rate", "channels", "sample_width", "frame_width", "dBFS", "max", "rms", "max_dBFS",
         "duration_seconds", "max_possible_amplitude", "array_type"]


def t(f):
    try:
        return f()
    except Exception as e:
        return ("exc", type(e).__name__)


def flat(L, x, label, out):
    if isinstance(x, L.AS):
        out[label + ".raw"] = x.raw_data
        out[label + ".len"] = t(lambda: len(x))
        for p in PROPS:
            out[label + "." + p] = t(lambda: getattr(x, p))
    elif isinstance(x, (list, tuple)):
        out[label + ".n"] = len(x)
        for i, v in enumerate(x):
            flat(L, v, "%s[%d]" % (label, i), out)
    elif isinstance(x, array.array):
        out[label] = (x.typecode, x.tobytes())
    else:
        out[label] = x


def run(L, spec, steps, seed, recs):
    random.seed(seed)
    try:
        s = build(L, spec)
    except Exception as e:
        recs.append({"exc": type(e).__name__})
        return
    rec = {}
    flat(L, s, "r", rec)
    recs.append(rec)
    for op, a, kw, pick in steps:
        STATE["op"] = op
        try:
            out = call(L, s, op, res(L, s, a), res(L, s, kw))
            if isinstance(out, types.GeneratorType):
                out = list(out)
            rec = {}
            flat(L, out, "r", rec)
        except Exception as e:
            recs.append({"exc": type(e).__name__})
            continue
        recs.append(rec)
        segs = [out] if isinstance(out, L.AS) else [v for v in out if isinstance(v, L.AS)] if isinstance(out, list) else []
        if segs and (op not in ("gen", "AS.silent", "AS.empty") or pick % 2 == 0):
            s = segs[pick % len(segs)]


def timed(L, spec, steps, seed, limit=TIMEOUT):
    recs = []
    STATE["lib"] = L.AS.__module__.split(".")[0]
    signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        run(L, spec, steps, seed, recs)
    except Timeout:
        return recs, True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    return recs, False


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return a == b or (a != a and b != b)
    return a == b


def close(a, b):
    return isinstance(a, float) and isinstance(b, float) and abs(a - b) <= 1e-9 * max(abs(a), abs(b))


# pydub crashes here on purpose-fixed inputs, pydub2 returns a result
FIXED = {("s.low_pass_filter", "IndexError"), ("s.high_pass_filter", "IndexError"), ("s.set_sample_width", "IndexError"),
         ("s.fade", "ZeroDivisionError"), ("s.append", "ZeroDivisionError"), ("s.speedup", "ZeroDivisionError")}


def diff(ra, rb, steps=()):
    for i, (a, b) in enumerate(zip(ra, rb)):
        ea, eb = a.get("exc"), b.get("exc")
        if i and (steps[i - 1][0], ea) in FIXED and not eb:
            return None
        if bool(ea) != bool(eb):
            return i, "exc", ea, eb
        if ea:
            continue
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return i, k + " (missing)", a.get(k), b.get(k)
            if not same(a[k], b[k]):
                return i, k + (" (float)" if close(a[k], b[k]) else ""), a[k], b[k]
    return None


def cls_of(steps, d):
    i, label, va, vb = d
    op = "init" if i == 0 else steps[i - 1][0]
    label = re.sub(r"\[\d+\]", "[]", label)
    if label == "exc":
        label = "exc %s vs %s %s" % (va, vb, sorted(steps[i - 1][2]) if i else "")
    return op, label


def compare(spec, steps, seed):
    ra, hung = timed(PD, spec, steps, seed)
    if hung:
        return "skip", steps[len(ra) - 1][0] if ra else "init"
    rb, hung = timed(P2, spec, steps, seed, 5 * TIMEOUT)
    if hung:
        return "bad", ((len(rb), "pydub2 hang", None, None))
    d = diff(ra, rb, steps)
    return ("bad", d) if d else ("ok", None)


def short(v):
    s = repr(v)
    return s if len(s) < 120 else s[:120] + "..."


def shrink(spec, steps, seed, cls):
    def bad(sp, st):
        status, d = compare(sp, st, seed)
        return status == "bad" and d[1] != "pydub2 hang" and cls_of(st, d) == cls
    i = 0
    while i < len(steps):
        st = steps[:i] + steps[i + 1:]
        if bad(spec, st):
            steps = st
        else:
            i += 1
    while spec["n"] > 0:
        sp = dict(spec, n=spec["n"] // 2)
        if not bad(sp, steps):
            break
        spec = sp
    return spec, steps


def describe(seed):
    spec, steps = make(seed)
    status, d = compare(spec, steps, seed)
    if status != "bad":
        return None
    op = "init" if d[0] == 0 else steps[d[0] - 1][0]
    return "seed %d op %s: %s pydub=%s pydub2=%s\n  spec %r\n  chain %r" % (
        seed, op, d[1], short(d[2]), short(d[3]), spec, steps)


def test_chains():
    n = int(os.environ.get("PYDUB2_CHAINS", 300))
    bad = [m for m in (describe(s) for s in range(n)) if m]
    assert not bad, "%d of %d chains differ, first ones:\n%s" % (len(bad), n, "\n".join(bad[:5]))


SEEN = {}
STATE = {}


def work(a, b):
    for seed in range(a, b):
        print("P", json.dumps([seed]), flush=True)
        faulthandler.dump_traceback_later(30, exit=True)
        spec, steps = make(seed)
        status, d = compare(spec, steps, seed)
        if status == "skip":
            print("S", json.dumps([seed, d]), flush=True)
        elif status == "bad":
            hang = d[1] == "pydub2 hang"
            cls = (steps[d[0] - 1][0], "pydub2 hang") if hang else cls_of(steps, d)
            small = None
            if SEEN.get(cls, 0) < 3 and not hang:
                faulthandler.dump_traceback_later(120, exit=True)
                small = repr(shrink(spec, steps, seed, cls))
            SEEN[cls] = SEEN.get(cls, 0) + 1
            print("B", json.dumps([seed, cls, short(d[2]), short(d[3]), small]), flush=True)
    print("D", flush=True)


def supervise(a, b, out):
    while a < b:
        p = subprocess.Popen([sys.executable, __file__, "worker", str(a), str(b)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        seed = a
        for line in p.stdout:
            kind, _, rest = line.rstrip("\n").partition(" ")
            if kind == "P":
                seed = json.loads(rest)[0]
                if seed % 1000 == 0:
                    print("progress", seed, flush=True)
            elif kind != "D":
                out.append((kind, seed, json.loads(rest)))
        err = p.stderr.read()
        p.wait()
        if p.returncode == 0:
            return
        frames = [l.strip() for l in err.splitlines() if "File" in l and "pydub" in l and "test_chains" not in l]
        out.append(("hang", seed, ["pydub2" if "/pydub2/" in err else "pydub", frames[0] if frames else err[-300:]]))
        a = seed + 1


def main(start, count, procs):
    step = -(-count // procs)
    outs = [[] for _ in range(procs)]
    ts = [threading.Thread(target=supervise, args=(start + i * step, min(start + (i + 1) * step, start + count), outs[i])) for i in range(procs)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    classes = {}
    skips = []
    for o in outs:
        for kind, seed, v in o:
            if kind == "S" or (kind == "hang" and v[0] == "pydub"):
                skips.append((seed, v[1]))
            elif kind == "hang":
                classes.setdefault(("hang", v[1]), [0, [], None])[1].append(seed)
                classes[("hang", v[1])][0] += 1
            else:
                cls = tuple(v[1])
                print("MISMATCH seed=%d %s pydub=%s pydub2=%s" % (seed, cls, v[2], v[3]), flush=True)
                c = classes.setdefault(cls, [0, [], None])
                c[0] += 1
                c[1].append(seed)
                if v[4] and (c[2] is None or len(v[4]) < len(c[2][0])):
                    c[2] = (v[4], (v[2], v[3]))
    print("chains %d, pydub hang or timeout (skipped) %d" % (count, len(skips)))
    for seed, op in skips:
        print("SKIP seed=%d op=%s" % (seed, op))
    for cls, (n, seeds, small) in sorted(classes.items(), key=lambda kv: -kv[1][0]):
        print("CLASS %s count=%d seeds=%s" % (cls, n, seeds[:5]))
        if small:
            print("  smallest (spec, steps): %s\n  pydub, pydub2 = %s" % small)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "show":
        print(describe(int(a[1])) or "no mismatch")
    elif a[0] == "worker":
        work(int(a[1]), int(a[2]))
    else:
        main(int(a[0]), int(a[1]), int(a[2]) if len(a) > 2 else os.cpu_count())
