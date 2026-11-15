import array
import copy
import importlib
import importlib.util
import inspect
import math
import pickle
import wave

import pytest

pydub = pytest.importorskip("pydub")
import pydub2

# key: "module.name" as the pydub side prints it, value: why it differs (five words)
ALLOWED = {
    "pydub.audio_segment.AudioSegment.export": "format taken from file extension",
    "pydub.audio_segment.audioop": "no audioop in pydub2",
    "pydub.effects.audioop": "no audioop in pydub2",
    "pydub.utils.audioop": "no audioop in pydub2",
}

MODULES = [
    "pydub",
    "pydub.audio_segment",
    "pydub.effects",
    "pydub.silence",
    "pydub.utils",
    "pydub.generators",
    "pydub.playback",
    "pydub.exceptions",
    "pydub.logging_utils",
]
if importlib.util.find_spec("scipy") is not None:
    MODULES.append("pydub.scipy_effects")

HIDDEN = {
    "__dict__", "__weakref__", "__module__", "__doc__", "__static_attributes__",
    "__firstlineno__", "__qualname__", "__annotations__",
}
EMPTY = inspect.Parameter.empty
POK = inspect.Parameter.POSITIONAL_OR_KEYWORD
KO = inspect.Parameter.KEYWORD_ONLY
FUNC_KINDS = ("function", "staticmethod", "classmethod", "property")
DATA = array.array("h", [int(9000 * math.sin(i * 0.3)) for i in range(2000)]).tobytes()


def twin(name):
    return "pydub2" + name[len("pydub"):]


def kind(obj):
    if isinstance(obj, staticmethod):
        return "staticmethod"
    if isinstance(obj, classmethod):
        return "classmethod"
    if isinstance(obj, property):
        return "property"
    if inspect.isfunction(obj) or inspect.isbuiltin(obj):
        return "function"
    if inspect.isclass(obj):
        return "class"
    if inspect.ismodule(obj):
        return "module"
    return "data " + type(obj).__name__


def callable_of(obj):
    if isinstance(obj, (staticmethod, classmethod)):
        return obj.__func__
    if isinstance(obj, property):
        return obj.fget
    return obj


def sig(obj):
    try:
        return inspect.signature(obj)
    except (TypeError, ValueError):
        return None


def same_default(a, b):
    if a is EMPTY or b is EMPTY:
        return a is b
    return type(a) is type(b) and (a == b or repr(a) == repr(b))


def compatible(a, b):
    sa, sb = sig(a), sig(b)
    if sa is None or sb is None:
        return sa is None and sb is None
    p = list(sa.parameters.values())
    q = list(sb.parameters.values())
    if len(q) < len(p):
        return False
    for x, y in zip(p, q):
        if (x.name, x.kind) != (y.name, y.kind) or not same_default(x.default, y.default):
            return False
    return all(y.default is not EMPTY and y.kind in (POK, KO) for y in q[len(p):])


def same_value(a, b):
    try:
        return type(a) is type(b) and a == b
    except Exception:
        return False


def check(key, a, b, diffs, own=True, values=False):
    ka, kb = kind(a), kind(b)
    if ka != kb:
        diffs.append((key, ka, kb))
    elif ka == "class":
        ma = [c.__name__ for c in a.__mro__]
        mb = [c.__name__ for c in b.__mro__]
        if ma != mb:
            diffs.append((key + " (mro)", ma, mb))
        if not own:
            return
        for n in vars(a):
            if n in HIDDEN:
                continue
            if n not in vars(b):
                diffs.append((key + "." + n, "has it", "missing"))
                continue
            check(key + "." + n, vars(a)[n], vars(b)[n], diffs)
        if values:
            for n in vars(a):
                if n.startswith("__") or not kind(vars(a)[n]).startswith("data") or n not in vars(b):
                    continue
                va, vb = getattr(a, n), getattr(b, n)
                if not same_value(va, vb):
                    diffs.append((key + "." + n, repr(va)[:100], repr(vb)[:100]))
    elif own and ka in FUNC_KINDS:
        fa, fb = callable_of(a), callable_of(b)
        if fa is not None and fb is not None and not compatible(fa, fb):
            diffs.append((key, str(sig(fa)), str(sig(fb))))


def module_diffs(name):
    a_mod = importlib.import_module(name)
    b_mod = importlib.import_module(twin(name))
    diffs = []
    for n in dir(a_mod):
        if n.startswith("__"):
            continue
        key = name + "." + n
        if not hasattr(b_mod, n):
            diffs.append((key, "has it", "missing"))
            continue
        a, b = getattr(a_mod, n), getattr(b_mod, n)
        own = getattr(a, "__module__", None) == name
        values = name == "pydub.audio_segment" and n == "AudioSegment"
        check(key, a, b, diffs, own=own, values=values)
    return [d for d in diffs if d[0] not in ALLOWED]


def report(diffs):
    return "\n".join("%s, pydub has %s, pydub2 has %s" % d for d in diffs)


def mk(AS):
    return AS(data=DATA, sample_width=2, frame_rate=8000, channels=1)


def show(v, AS):
    if isinstance(v, AS):
        return ("seg", type(v).__name__, v.frame_rate, v.sample_width, v.channels, bytes(v.raw_data))
    if isinstance(v, (list, tuple)):
        return (type(v).__name__, [show(x, AS) for x in v])
    if isinstance(v, (bool, int, float, str, bytes, type(None))):
        return (type(v).__name__, v)
    return ("obj", type(v).__name__)


OPS = {
    "repr_type": lambda AS, S, s, sub, w: type(repr(s)).__name__,
    "eq_equal": lambda AS, S, s, sub, w: s == mk(AS),
    "eq_shorter": lambda AS, S, s, sub, w: s == s[:-1],
    "ne_shorter": lambda AS, S, s, sub, w: s != s[:-1],
    "eq_int": lambda AS, S, s, sub, w: s == 0,
    "hash_equal": lambda AS, S, s, sub, w: hash(s) == hash(mk(AS)),
    "bool": lambda AS, S, s, sub, w: bool(s),
    "bool_empty": lambda AS, S, s, sub, w: bool(AS.empty()),
    "len": lambda AS, S, s, sub, w: len(s),
    "iter": lambda AS, S, s, sub, w: list(s),
    "in": lambda AS, S, s, sub, w: s[0:1] in s,
    "reversed": lambda AS, S, s, sub, w: list(reversed(s)),
    "sum": lambda AS, S, s, sub, w: sum([s, s]),
    "sum_start": lambda AS, S, s, sub, w: sum([s, s], AS.empty()),
    "add_zero": lambda AS, S, s, sub, w: s + 0,
    "radd_zero": lambda AS, S, s, sub, w: 0 + s,
    "add_self": lambda AS, S, s, sub, w: s + s,
    "add_str": lambda AS, S, s, sub, w: s + "x",
    "pickle": lambda AS, S, s, sub, w: pickle.loads(pickle.dumps(s)),
    "copy": lambda AS, S, s, sub, w: copy.copy(s),
    "deepcopy": lambda AS, S, s, sub, w: copy.deepcopy(s),
    "attr_data": lambda AS, S, s, sub, w: s._data == s.raw_data,
    "attr_data_type": lambda AS, S, s, sub, w: type(s._data).__name__,
    "attr_frame_width": lambda AS, S, s, sub, w: s.frame_width,
    "attr_sample_width": lambda AS, S, s, sub, w: s.sample_width,
    "attr_array_type": lambda AS, S, s, sub, w: s.array_type,
    "from_file": lambda AS, S, s, sub, w: AS.from_file(w, format="wav"),
    "sub_slice": lambda AS, S, s, sub, w: sub[0:100],
    "sub_add": lambda AS, S, s, sub, w: sub + sub,
    "sub_radd": lambda AS, S, s, sub, w: 0 + sub,
    "sub_apply_gain": lambda AS, S, s, sub, w: sub.apply_gain(3),
    "sub_set_frame_rate": lambda AS, S, s, sub, w: sub.set_frame_rate(16000),
    "sub_from_file": lambda AS, S, s, sub, w: S.from_file(w, format="wav"),
    "sub_spawn": lambda AS, S, s, sub, w: sub._spawn(sub._data),
    "sub_pickle": lambda AS, S, s, sub, w: pickle.loads(pickle.dumps(sub)),
    "sub_copy": lambda AS, S, s, sub, w: copy.copy(sub),
    "sub_deepcopy": lambda AS, S, s, sub, w: copy.deepcopy(sub),
}


def probe(modname, wav):
    AS = importlib.import_module(modname).AudioSegment
    S = type("Sub", (AS,), {})
    s = mk(AS)
    sub = mk(S)
    out = {}
    for label, op in OPS.items():
        try:
            out[label] = show(op(AS, S, s, sub, wav), AS)
        except Exception as e:
            out[label] = ("raise", type(e).__name__)
    return out


@pytest.fixture(scope="module")
def wav(tmp_path_factory):
    path = tmp_path_factory.mktemp("surface") / "tone.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(DATA)
    return str(path)


@pytest.fixture(scope="module")
def probes(wav):
    return probe("pydub", wav), probe("pydub2", wav)


@pytest.mark.parametrize("name", MODULES)
def test_module_surface(name):
    diffs = module_diffs(name)
    assert not diffs, report(diffs)


def test_effects_registered():
    a = pydub.AudioSegment
    b = pydub2.AudioSegment
    names = [n for n, v in vars(a).items() if getattr(v, "__module__", None) == "pydub.effects"]
    diffs = []
    for n in sorted(names):
        key = "pydub.effects." + n
        if key not in ALLOWED and not callable(getattr(b, n, None)):
            diffs.append((key, "registered", "missing"))
    assert not diffs, report(diffs)


@pytest.mark.parametrize("label", list(OPS))
def test_protocol(label, probes):
    a, b = probes
    key = "protocol." + label
    assert key in ALLOWED or a[label] == b[label], "%s, pydub has %r, pydub2 has %r" % (key, a[label], b[label])
