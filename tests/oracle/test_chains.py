import array
import ctypes
import decimal
import faulthandler
import fractions
import importlib
import io
import itertools
import math
import json
import os
import random
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import zlib

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


def on_alarm(tid):
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), ctypes.py_object(Timeout))


class Lib:
    def __init__(self, name):
        self.AS = importlib.import_module(name).AudioSegment
        for m in ("silence", "generators", "utils", "exceptions"):
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
    return dict(w=r.choice([1, 2, 3, 4]), ch=r.choice([1, 2, 1, 2, 1, 2, 3, 4, 6]), rate=rt, n=n,
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
    a = np.clip(np.round(a), lo, hi).astype({1: "<i1", 2: "<i2", 3: "<i4", 4: "<i4"}[w])
    if w == 3:
        a = np.frombuffer(a.tobytes(), np.uint8).reshape(-1, 4)[:, :3]
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
                 seek_step=r.choice([1, 1, 2, 5, 10, 0, -1, 1.5, 10 ** 6, 2 ** 31, 2 ** 63, 10 ** 30, 1e18, 1e300, INF, NAN, True, "5"]))
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


def g_fn(r):
    name, args = r.choice([("reverse", ()), ("apply_gain", (db(r),)), ("low_pass_filter", (cutoff(r),)),
                           ("fade_out", (ms(r, False),)), ("invert_phase", ())])
    return (("@fn", name, args),), {}


def g_raw(r):
    return (), dict(format=r.choice(["raw", "pcm"]), **pick_kw(r, sample_width=r.choice([1, 2, 3, 4, 0, 5, None]),
                    frame_rate=r.choice([rate(r), 0, -1, None]), channels=r.choice([1, 2, 3, 0, None]),
                    start_second=r.choice([0, 0.1, 1, -1, None]), duration=r.choice([0, 0.05, 1, None])))


def g_spawn(r):
    return (r.choice([0, 1, 2, 3, 7, 100, 1000, None]),), pick_kw(r, frame_rate=rate(r), channels=r.choice([1, 2, 3, 4]),
                                                                    sample_width=r.choice([1, 2, 4]))


def g_util(r):
    f = r.choice(["db_to_float", "ratio_to_db", "get_array_type", "get_min_max_value", "get_frame_width"])
    if f == "db_to_float":
        a = (db(r),)
    elif f == "ratio_to_db":
        a = (r.choice([0, 1, 2, 0.5, -1, 1e-9, 1e300, INF, NAN, None, r.uniform(0, 10)]),
             r.choice([None, 0, 1, 2, 0.5]))
    else:
        a = (r.choice([8, 16, 24, 32, 0, 12, 64, None]),)
    return (f, a, pick_kw(r, using_amplitude=r.random() < 0.5) if f in ("db_to_float", "ratio_to_db") else {}), {}


def g_eqhash(r):
    return (sg(r),), {}


def cutoff(r):
    return r.choice([0, 1, 100, 1000, 5000, 20000, 1e9, -5, 440.5, NAN, None, r.randint(1, 20000)])


def num(x):
    # numbers that do not survive a repr round trip are written as "kind:text"
    if isinstance(x, str) and ":" in x:
        kind, _, v = x.partition(":")
        make = {"frac": fractions.Fraction, "f64": np.float64, "f32": np.float32, "f16": np.float16,
                "i64": np.int64, "b": lambda v: v == "1"}.get(kind)
        return make(v) if make else x
    return x


def secs(r):
    if r.random() < 0.5:
        return r.choice([None, None, 0, 0.05, 0.1, 0.25, 0.5, 1])
    return r.choice([2, 1.5, -1, -0.5, 1e-5, 1e-9, 0.0001, 0.001, 0.0005, 1e12, 1e13, 10 ** 30, 1e300, INF, -INF, NAN, True, False,
                     "frac:1/2", "frac:1/3", "frac:-1/2", "frac:1/100000", "f64:0.1", "f32:0.25", "f32:0.7", "f16:0.1",
                     "i64:0", "i64:1", "b:1", "x", "1.5", 2 ** 63, 2 ** 64])


def for_pydub(v):
    # pydub writes these into the ffmpeg command as text, which ffmpeg refuses, see docs/differences.md
    if isinstance(v, (bool, np.bool_)):
        return int(v)
    if isinstance(v, fractions.Fraction):
        v = float(v)
    if isinstance(v, float) and 0 < v < 1e-4:
        return Plain(v)
    return v


class Plain(float):
    def __str__(self):
        return format(decimal.Decimal(repr(float(self))), "f")


VARIANTS = ["pcm", "pcm", "pcm", "ext", "float", "extfloat", "alaw", "mulaw", "extalaw", "ext24", "odd", "odd_tail",
            "chunk", "chunk_nopad"]
# pydub reads these as integer pcm or alaw bytes, pydub2 decodes them, so only rate, channels and length are compared
WRONG_IN_PYDUB = {"extfloat", "extalaw"}
# pydub2 has no decoder of its own for these, so the format goes to ffmpeg there too
VIA_FFMPEG = WRONG_IN_PYDUB | {"alaw", "mulaw"}
GUID = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"


def ck(tag, body, pad=True):
    return tag + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 and pad else b"")


def wav_variant(s, variant):
    w, ch, rate = s.sample_width, s.channels, max(s.frame_rate, 0)
    audioop = pydub.audio_segment.audioop
    if w not in (1, 2, 4):
        raise ValueError("no wav of this width")
    raw = s.raw_data
    if variant not in ("odd", "odd_tail"):
        raw = raw[:len(raw) // s.frame_width * s.frame_width]
    code, bits, ext, pad, before, after = 1, 8 * w, None, True, b"", b""
    if w == 1:
        raw = audioop.bias(raw, 1, 128)
    if variant in ("ext", "ext24"):
        ext = 1
        if variant == "ext24" and w == 4:
            raw = (np.frombuffer(raw, "<u4") & 0xFFFFFF00).astype("<u4").tobytes()
    elif variant in ("float", "extfloat"):
        raw, code, bits = (np.frombuffer(audioop.lin2lin(raw, w, 4), "<i4") / 2.0 ** 31).astype("<f4").tobytes(), 3, 32
        ext = 3 if variant == "extfloat" else None
    elif variant in ("alaw", "mulaw", "extalaw"):
        enc = audioop.lin2ulaw if variant == "mulaw" else audioop.lin2alaw
        raw, code, bits = enc(audioop.lin2lin(raw, w, 2) if w != 2 else raw, 2), 7 if variant == "mulaw" else 6, 8
        ext = code if variant == "extalaw" else None
    elif variant in ("odd", "odd_tail"):
        pad = False
        after = ck(b"junk", b"abc", False) if variant == "odd_tail" else b""
    elif variant in ("chunk", "chunk_nopad"):
        before = ck(b"junk", b"abc", variant == "chunk")
    if ext:
        f = struct.pack("<HHIIHHHHI", 0xFFFE, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits, 22,
                        24 if variant == "ext24" and w == 4 else bits, 0) + struct.pack("<H", ext) + GUID
    else:
        f = struct.pack("<HHIIHH", code, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
    body = b"WAVE" + ck(b"fmt ", f) + before + ck(b"data", raw, pad) + after
    return b"RIFF" + struct.pack("<I", len(body)) + body


def g_load(r):
    kw = pick_kw(r, start_second=secs(r), duration=secs(r))
    if r.random() < 0.15:
        kw["format"] = r.choice(["wav", "wav", "WAV", "raw", "mp3", "ogg", "x", None])
    return (r.choice(VARIANTS), r.choice(["from_file", "from_file", "from_file_using_temporary_files"]),
            r.choice(["path", "bytes", "file", "pathlib"])), kw


def fits_native(v):
    return v is None or (isinstance(v, (int, float, fractions.Fraction, np.number)) and 0 <= v < 1e12)


def sp_load(L, s, variant, how, src, **kw):
    kw = {k: num(v) for k, v in kw.items()}
    data = wav_variant(s, variant)
    wrong = kw.get("format") not in (None, "wav", "WAV") and isinstance(kw.get("format"), str)
    via_ffmpeg = variant in VIA_FFMPEG
    # ffmpeg reads ext24 as float when pydub has it read a file object, a pipe or a nameless path, see docs/differences.md
    params_only = variant in WRONG_IN_PYDUB or (variant == "ext24" and (
        src in ("bytes", "file") or (how == "from_file_using_temporary_files" and src == "pathlib")))
    if L is PD:
        kw = {k: for_pydub(v) if k != "format" else v for k, v in kw.items()}
        # pydub2 reads wav data as what it is, ffmpeg is told the format and fails, or reads the audio as a stereo mp3
        if wrong and kw["format"] not in ("raw", "pcm") and not via_ffmpeg and all(fits_native(v) for k, v in kw.items() if k != "format"):
            del kw["format"]
    if variant in ("extfloat", "extalaw"):
        # pydub reads these in python and pydub2 has ffmpeg do it, which cuts and takes formats in its own way
        kw = {k: v for k, v in kw.items() if k == "format" and not wrong}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.wav")
        with open(path, "wb") as f:
            f.write(data)

        def load(how=how, src=src, **kw):
            fn = getattr(L.AS, how)
            if src == "bytes":
                return fn(io.BytesIO(data), **kw)
            if src == "file":
                with open(path, "rb") as f:
                    return fn(f, **kw)
            return fn(pathlib.Path(path) if src == "pathlib" else path, **kw)
        try:
            seg = load(**kw)
        except Exception as e:
            name = type(e).__name__
            # a wav with a rate or a channel count of 0 is damaged to pydub2, and pydub cannot cut it or ffmpeg refuses it, see docs/differences.md
            if (s.frame_rate < 1 or s.channels < 1) and kw.get("format") not in ("raw", "pcm") and (L is PD or name == "CouldntDecodeError"):
                return ["rate 0"]
            if L is not PD:
                raise
            if name == "CouldntDecodeError" and "format" in kw and wrong and not via_ffmpeg and \
                    all(fits_native(v) for k, v in kw.items() if k != "format"):
                # a wrong format is ignored for wav data that pydub2 reads itself, pydub hands it to ffmpeg
                seg = load(**{k: v for k, v in kw.items() if k != "format"})
            elif how == "from_file_using_temporary_files" and isinstance(e, ValueError) and "closed file" in str(e):
                # pydub cannot slice what it read and raises here, pydub2 goes on to ffmpeg like from_file, with 16 bit
                seg = load("from_file", **kw).set_sample_width(2)
            elif name == "CouldntDecodeError" and variant in ("odd", "odd_tail") and not int(s.frame_count()) and (
                    src in ("bytes", "file") or (how == "from_file_using_temporary_files" and src == "pathlib")) and \
                    all(fits_native(v) for k, v in kw.items() if k != "format"):
                # ffmpeg cannot read a wav without a whole frame and with an unpadded chunk from a pipe, pydub2 drops the part frame
                seg = load(src="path", **kw)
                seg = seg._spawn(seg.raw_data[:int(seg.frame_count()) * seg.frame_width])
                if how == "from_file_using_temporary_files":
                    seg = seg.set_sample_width(2)
            else:
                raise
    if L is PD and (s.frame_rate < 1 or s.channels < 1) and kw.get("format") not in ("raw", "pcm"):
        return ["rate 0"]
    if params_only:
        return ["load", seg.frame_rate, seg.channels, int(seg.frame_count())]
    return seg


def sp_stream(L, s, variant, chunk, **kw):
    kw = {k: num(v) for k, v in kw.items()}
    chunk = num(chunk)
    data = wav_variant(s, variant)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.wav")
        with open(path, "wb") as f:
            f.write(data)
        if L is P2:
            return list(L.AS.stream(path, chunk, **kw))
        if s.frame_rate < 1 or s.channels < 1 or variant == "chunk_nopad":
            # stream does not take a wav with a rate or a channel count of 0 or a chunk without its pad byte, see docs/differences.md
            raise L.exceptions.CouldntDecodeError("cant read this file")
        # pydub has no stream, the same file read whole and cut into chunks is what it has to give
        if not all(v is None or (isinstance(v, (int, float, fractions.Fraction, np.number)) and
                                 0 <= v < 1e12) for v in kw.values()):
            raise ValueError("start_second and duration have to be plain numbers")
        seg = L.AS.from_file(path, **{k: for_pydub(v) for k, v in kw.items()})
        step = int(chunk * (seg.frame_rate / 1000.0))
        if step < 1:
            raise ValueError("chunk_ms is too small")
        # a partial last frame stays in what from_file reads, stream leaves it out
        raw = seg.raw_data[:int(seg.frame_count()) * seg.frame_width]
        return [seg._spawn(raw[i:i + step * seg.frame_width]) for i in range(0, len(raw), step * seg.frame_width)]


def g_stream(r):
    kw = pick_kw(r, start_second=secs(r), duration=secs(r))
    return (r.choice(["pcm", "pcm", "ext", "odd", "chunk", "chunk_nopad"]),
            r.choice([10000, 10, 1, 7, 100, 0.5, 0, -1, 1e-9, INF, NAN, "frac:3/2", "x", None, True])), kw


def png():
    row = b"\0\xff\x00\x00"
    body = zlib.compress(row)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
        for t, b in ((b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)), (b"IDAT", body), (b"IEND", b"")))


BITRATES = [None, "64k", "128k", "192k", "320k", "32k", "8k", "1000000", "96000", "1M", "1.5M", "0x10000", "64K", "1e5", "96Ki",
            "64kb", "k", "", "abc", "-64k", "0", "0k", "1e400", "nan", "inf", "9" * 30, "64 k", " 64k", "64k ", "2*32k", "+64k",
            64, 128000, 0, -64, True, 1.5, b"64k", "64k\x00", "10000000000k"]
COVERS = [None, None, "@png", "@png", "@jpg", "@missing", "@text", "@path", "", 5, b"x", "@dir", "@png.png"]
TAGS = [None, None, {}, {"title": "café 日本語", "artist": "a\nb"}, {"album": ""}, {"": "x"}, {"k=v": "y"},
        {"comment": "x" * 300}, {"title": 5, "track": 3}, {"title": None}, {"a": "\udc80"}, {"title": "a\x00b"},
        {"TITLE": "x", "title": "y"}, {"album_artist": "z", "disc": "1/2", "track": "3/12"},
        {"date": "2020", "genre": "Rock", "composer": "x"}, {"title": "\U0001f600"}, "x", ["a"], [("a", "b")], {1: 2}, {"a b": "c d"},
        {"title": "x" * 70000}]


def g_export(r):
    fmt = r.choice(["mp3", "mp3", "mp3", "wav", "wav", "flac", "ogg", "opus", "raw", None, "m4a", "aac", "x"])
    return (fmt,), pick_kw(r, bitrate=r.choice(BITRATES), cover=r.choice(COVERS), tags=r.choice(TAGS),
                           id3v2_version=r.choice(["3", "4", "3", "4", 3, "2", "5", None, ""]))


def cover_arg(c, d):
    if not isinstance(c, str) or c[:1] != "@":
        return c
    if c == "@missing":
        return os.path.join(d, "nothing.png")
    if c == "@dir":
        return d
    p = os.path.join(d, "c.jpg" if c == "@jpg" else "c.png")
    with open(p, "wb") as f:
        f.write(b"not an image" if c == "@text" else png())
    return pathlib.Path(p) if c == "@path" else p


def lone_surrogate(x):
    if isinstance(x, str):
        return any("\ud800" <= c <= "\udfff" for c in x)
    return isinstance(x, dict) and any(lone_surrogate(v) or lone_surrogate(k) for k, v in x.items())


def native_bitrate(bitrate, channels):
    from pydub2.audio_segment import _bits_per_second
    try:
        return bitrate is None or _bits_per_second(bitrate, "opus", channels, "ffmpeg") is not None
    except Exception:
        return False


def big_tag(tags):
    # ffmpeg cannot read back the mp3 it writes for a tag of 70000 characters
    return isinstance(tags, dict) and any(len(str(v)) > 60000 for v in tags.values())


def sp_export(L, s, fmt, **kw):
    if fmt != "raw":
        # pydub writes a partial last frame, pydub2 leaves it out, see docs/differences.md
        s = s._spawn(s.raw_data[:int(s.frame_count()) * s.frame_width])
    out = io.BytesIO()
    if L is PD:
        # pydub takes no format from the target and has no muxer called m4a or aac, see docs/differences.md
        fmt = {None: "mp3", "m4a": "ipod", "aac": "adts"}.get(fmt, fmt)
        if fmt == "opus" and s.channels in (3, 4) and s.frame_rate >= 100 and kw.get("cover") is None and native_bitrate(kw.get("bitrate"), s.channels):
            # ffmpeg has no channel layout for these in libopus, pydub2 writes them unless something sends it to ffmpeg
            s = L.AS(data=bytes(len(s.raw_data) // s.channels * 2), sample_width=s.sample_width, frame_rate=s.frame_rate, channels=2)
            if not native_bitrate(kw.get("bitrate"), 2):
                # a bitrate that fits four channels can be too high for two
                kw.pop("bitrate")
    with tempfile.TemporaryDirectory() as d:
        if "cover" in kw:
            kw["cover"] = cover_arg(kw["cover"], d)
        if L is PD:
            # pydub takes no path object as cover and no int as bitrate
            if isinstance(kw.get("cover"), pathlib.Path):
                kw["cover"] = str(kw["cover"])
            if type(kw.get("bitrate")) is int:
                kw["bitrate"] = str(kw["bitrate"])
        s.export(out, format=fmt, **kw)
        data = out.getvalue()
        if fmt in ("wav", "raw"):
            return data
        # the encoders differ, what has to match is the layout the file reads back with, and for flac the length (a file of a
        # few frames is not read back, pydub fails on the mp3 ffmpeg writes for it)
        res = [bool(data)]
        if fmt in (None, "mp3", "flac") and s.frame_count() >= 4096 and not big_tag(kw.get("tags")):
            try:
                seg = L.AS.from_file(io.BytesIO(data), format=fmt or "mp3")
                res += [seg.frame_rate, seg.channels, int(seg.frame_count()) if fmt == "flac" else 0]
            except Exception as e:
                res.append(type(e).__name__)
        return res


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
    "s.set_frame_rate": (4, lambda r: ((r.choice([rate(r), 0, -1, 3.5, None, 1, 100000, 2 ** 31, 2 ** 32, 2 ** 63, 10 ** 20, -2 ** 31, -2 ** 63]),), {})),
    "s.set_channels": (3, lambda r: ((r.choice([1, 2, 1, 2, 0, 3, -1, None, 1.5]),), {})),
    "s.set_sample_width": (3, lambda r: ((r.choice([1, 2, 4, 3, 0, 5, -1, None, 2.5, 2 ** 31, 2 ** 32, 2 ** 63, 10 ** 20, -2 ** 31]),), {})),
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
    "s.apply_mono_filter_to_each_channel": (2, g_fn),
    "utils.ms_to_stereo": (1, lambda r: ((), {})),
    "utils.stereo_to_ms": (1, lambda r: ((), {})),
    "from_raw": (1, g_raw),
    "spawn": (1, g_spawn),
    "util": (1, g_util),
    "load": (2, g_load),
    "stream": (1, g_stream),
    "export": (2, g_export),
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
    if op in ("s.fade", "s.append", "s.overlay", "load", "stream", "export"):
        return a, kw
    kw = {k: fix[k]() if k in fix else v for k, v in kw.items()}
    if op == "s.set_sample_width":
        a = (r.choice([1, 2, 3, 4]),)
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
        if x[0] == "@fn":
            return lambda m: getattr(m, x[1])(*x[2])
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
    # pydub writes a partial last frame into the wav, pydub2 leaves it out
    s = s._spawn(s.raw_data[:int(s.frame_count()) * s.frame_width])
    b = io.BytesIO()
    s.export(b, format="wav")
    d = b.getvalue()
    return [d, L.AS.from_wav(io.BytesIO(d))]


def sp_raw(L, s):
    b = io.BytesIO()
    s.export(b, format="raw")
    return b.getvalue()


def sp_from_raw(L, s, **kw):
    kw = {"sample_width": s.sample_width, "frame_rate": s.frame_rate, "channels": s.channels, **kw}
    return L.AS.from_file(io.BytesIO(s.raw_data), **kw)


def sp_spawn(L, s, n, **ov):
    # a changed layout comes with its frame width, as set_channels does
    ov["frame_width"] = ov.get("channels", s.channels) * ov.get("sample_width", s.sample_width)
    return s._spawn(s.raw_data[:n], ov)


def sp_util(L, s, f, a, kw):
    return getattr(L.utils, f)(*a, **kw)


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
    "from_raw": sp_from_raw,
    "spawn": sp_spawn,
    "util": sp_util,
    "gen": sp_gen,
    "gen_samples": sp_gen_samples,
    "load": sp_load,
    "stream": sp_stream,
    "export": sp_export,
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


def is_audioop_error(e):
    return type(e).__name__ == "error" and type(e).__module__ in ("audioop", "_audioop")


def t(f):
    try:
        return f()
    except Exception as e:
        # audioop.error is a ValueError here, see docs/differences.md
        return ("exc", "ValueError" if is_audioop_error(e) else type(e).__name__)


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
        line = traceback.extract_tb(e.__traceback__)[-1].line or ""
        recs.append({"exc": type(e).__name__, "empty24": isinstance(e, IndexError) and "_data[0]" in line})
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
            # pydub indexes the first byte of an empty 24 bit buffer
            line = traceback.extract_tb(e.__traceback__)[-1].line or ""
            recs.append({"exc": type(e).__name__, "empty24": isinstance(e, IndexError) and "_data[0]" in line})
            continue
        recs.append(rec)
        segs = [out] if isinstance(out, L.AS) else [v for v in out if isinstance(v, L.AS)] if isinstance(out, list) else []
        if segs and (op not in ("gen", "AS.silent", "AS.empty") or pick % 2 == 0):
            s = segs[pick % len(segs)]


def timed(L, spec, steps, seed, limit=TIMEOUT):
    recs = []
    STATE["lib"] = L.AS.__module__.split(".")[0]
    timer = threading.Timer(limit, on_alarm, [threading.get_ident()])
    timer.start()
    try:
        run(L, spec, steps, seed, recs)
    except Timeout:
        return recs, True
    finally:
        timer.cancel()
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
         ("s.fade", "ZeroDivisionError"), ("s.append", "ZeroDivisionError"), ("s.speedup", "ZeroDivisionError"),
         ("s.strip_silence", "ZeroDivisionError"), ("s.apply_mono_filter_to_each_channel", "IndexError"),
         ("utils.stereo_to_ms", "NameError"), ("utils.ms_to_stereo", "NameError")}  # fade of zero frames divides


def nonfinite(step):
    xs = list(step[1]) + list(step[2].values())
    xs += [v for x in xs if type(x) is tuple and x[:1] == ("@fn",) for v in x[2]]
    return any(isinstance(x, float) and not math.isfinite(x) for x in xs)


def diff(ra, rb, steps=()):
    for i, (a, b) in enumerate(zip(ra, rb)):
        ea, eb = a.get("exc"), b.get("exc")
        # a nan gain is undefined in audioop (c cast), pydub2 refuses it
        if i and not ea and eb == "ValueError" and nonfinite(steps[i - 1]):
            return None
        if i and (steps[i - 1][0], ea) in FIXED and not eb:
            return None
        if a.get("empty24") and not eb:
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


FFMPEG_OPS = {"load", "stream", "export"}
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
# the ffmpeg encoder pydub needs for each export format, an ffmpeg built without it gives pydub nothing to compare with
ENCODERS = {None: "libmp3lame", "mp3": "libmp3lame", "ogg": "libvorbis", "opus": "libopus"}


def missing_encoders():
    if not HAVE_FFMPEG:
        return set()
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    have = {line.split()[1] for line in out.splitlines() if line.startswith(" A")}
    return set(ENCODERS.values()) - have


MISSING_ENCODERS = missing_encoders()


def compare(spec, steps, seed):
    if not HAVE_FFMPEG:
        used = [op for op, *_ in steps if op in FFMPEG_OPS]
        if used:
            return "skip", used[0]
    if any(op == "export" and ENCODERS.get(a[0]) in MISSING_ENCODERS for op, a, *_ in steps):
        return "skip", "export"
    ra, hung = timed(PD, spec, steps, seed)
    if hung:
        return "skip", steps[min(len(ra), len(steps)) - 1][0] if ra else "init"
    # nothing after a known pydub crash is compared, and pydub2 goes on from a different segment
    stop = next((i for i, a in enumerate(ra) if a.get("exc") and (
        a["empty24"] or (i and (steps[i - 1][0], a["exc"]) in FIXED))), None)
    rb, hung = timed(P2, spec, steps[:stop], seed, 5 * TIMEOUT)
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


def test_pydub_crash_at_start_ends_the_chain():
    # pydub cannot build an empty 24 bit segment, so what pydub2 does with it afterwards is not compared
    spec = dict(w=3, ch=2, rate=32000, n=0, kind="silence", seed=0, extra=False)
    assert compare(spec, [("s.fade", (), {"from_gain": -40, "start": -10 ** 9, "end": 16}, 0)], 0) == ("ok", None)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
@pytest.mark.parametrize("how", ["from_file", "from_file_using_temporary_files"])
@pytest.mark.parametrize("w", [2, 4])
def test_pipe_wav_with_only_a_part_frame(how, w):
    spec = dict(w=w, ch=1, rate=8000, n=100, kind="noise", seed=0, extra=False)
    steps = [("spawn", (1,), {}, 0), ("load", ("odd", how, "bytes"), {}, 0)]
    assert compare(spec, steps, 0) == ("ok", None)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_alaw_loads_and_raw_exports_compare_the_samples():
    s = build(P2, dict(w=2, ch=1, rate=8000, n=100, kind="noise", seed=0, extra=False))
    for variant in ("alaw", "mulaw"):
        assert isinstance(sp_load(P2, s, variant, "from_file", "path"), P2.AS)
    s = s._spawn(s.raw_data + b"\1")
    out = io.BytesIO()
    s.export(out, format="raw")
    assert sp_export(P2, s, "raw") == out.getvalue()


def test_export_without_the_encoder_pydub_needs_is_not_compared(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "MISSING_ENCODERS", {"libvorbis"})
    spec = dict(w=2, ch=1, rate=8000, n=100, kind="noise", seed=0, extra=False)
    assert compare(spec, [("export", ("ogg",), {}, 0)], 0) == ("skip", "export")


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_pipe_wav_without_a_whole_frame_and_a_wrong_format():
    spec = dict(w=4, ch=4, rate=44100, n=0, kind="dc", seed=0, extra=False)
    assert compare(spec, [("load", ("odd_tail", "from_file", "file"), {"format": "mp3"}, 0)], 0) == ("ok", None)


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
