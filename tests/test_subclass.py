import array
import inspect
import math

import pytest

import pydub2
import pydub2.effects
import pydub2.silence

pydub = pytest.importorskip("pydub")
import pydub.effects  # noqa: E402
import pydub.silence  # noqa: E402


def tracked(base, log, *names):
    ns = {}
    for n in names:
        attr = inspect.getattr_static(base, n)
        if isinstance(attr, property):
            ns[n] = property(lambda self, n=n, attr=attr: (log.add(n), attr.fget(self))[1])
        else:
            ns[n] = lambda self, *a, n=n, attr=attr, **k: (log.add(n), attr(self, *a, **k))[1]
    return type("Tracked", (base,), ns)


def make(lib, log, *names):
    samples = array.array("h")
    for i in range(1600):
        loud = (i // 400) % 2 == 0
        samples.append(int(12000 * math.sin(i * 0.3)) if loud else 0)
    cls = tracked(lib.AudioSegment, log, *names)
    return cls(samples.tobytes(), frame_rate=8000, channels=1, sample_width=2)


def result(r):
    return [x.raw_data for x in r] if isinstance(r, list) and r and hasattr(r[0], "raw_data") else getattr(r, "raw_data", r)


CASES = {
    "detect_silence": ("silence", lambda m, s: m.silence.detect_silence(s, 100, -30),
                       ("rms", "max_possible_amplitude", "_spawn", "__getitem__")),
    "detect_silence_step": ("silence", lambda m, s: m.silence.detect_silence(s, 100, -30, 7), ("rms",)),
    "detect_nonsilent": ("silence", lambda m, s: m.silence.detect_nonsilent(s, 100, -30), ("rms",)),
    "split_on_silence": ("silence", lambda m, s: m.silence.split_on_silence(s, 100, -30), ("rms", "_spawn")),
    "strip_silence": ("effects", lambda m, s: m.effects.strip_silence(s, 100, -30, 20), ("rms", "_spawn")),
    "detect_leading_silence": ("silence", lambda m, s: m.silence.detect_leading_silence(s, -30), ("dBFS",)),
    "compress": ("effects", lambda m, s: m.effects.compress_dynamic_range(s),
                 ("rms", "get_frame", "get_sample_slice", "frame_count", "_spawn")),
    "low_pass": ("effects", lambda m, s: m.effects.low_pass_filter(s, 900),
                 ("get_array_of_samples", "frame_count", "_spawn")),
    "high_pass": ("effects", lambda m, s: m.effects.high_pass_filter(s, 900),
                  ("get_array_of_samples", "frame_count", "_spawn")),
    "speedup": ("effects", lambda m, s: m.effects.speedup(s, 1.5, 50, 10), ("append", "__add__", "_spawn")),
    "normalize": ("effects", lambda m, s: m.effects.normalize(s), ("max", "max_possible_amplitude", "apply_gain")),
    "invert_phase": ("effects", lambda m, s: m.effects.invert_phase(s), ("_spawn",)),
}


@pytest.mark.parametrize("case", CASES)
def test_overrides_are_called_like_pydub(case):
    _, call, names = CASES[case]
    seen = {}
    out = {}
    for lib in (pydub, pydub2):
        # every name at once, then each one alone, so a name that is missed shows up
        for only in (None,) + names:
            log = set()
            s = make(lib, log, *(names if only is None else (only,)))
            log.clear()
            r = result(call(lib, s))
            seen[lib.__name__, only] = log
            out[lib.__name__, only] = r
    for only in (None,) + names:
        assert seen["pydub2", only] == seen["pydub", only], only
        assert out["pydub2", only] == out["pydub", only], only


def nested(lib, log, upper, lower):
    # the names in upper are overridden one class up, the ones in lower in the class that is used
    base = tracked(lib.AudioSegment, log, *upper)
    cls = type("Leaf", (tracked(base, log, *lower),), {})
    samples = array.array("h", (int(12000 * math.sin(i * 0.3)) if (i // 400) % 2 == 0 else 0 for i in range(1600)))
    return cls(samples.tobytes(), frame_rate=8000, channels=1, sample_width=2)


@pytest.mark.parametrize("case", ["detect_silence", "split_on_silence", "strip_silence", "compress", "normalize"])
@pytest.mark.parametrize("split", [(0, 1), (1, 0), (1, 1)])
def test_subclass_of_a_subclass(case, split):
    _, call, names = CASES[case]
    upper, lower = (names[:len(names) // 2 + 1], names[len(names) // 2:])
    upper, lower = (upper if split[0] else (), lower if split[1] else ())
    seen = {}
    out = {}
    for lib in (pydub, pydub2):
        log = set()
        s = nested(lib, log, upper, lower)
        log.clear()
        out[lib.__name__] = result(call(lib, s))
        seen[lib.__name__] = log
    assert seen["pydub2"] == seen["pydub"]
    assert out["pydub2"] == out["pydub"]


@pytest.mark.parametrize("name, case", [("rms", "detect_silence"), ("rms", "strip_silence"), ("dBFS", "detect_leading_silence"),
                                        ("max_possible_amplitude", "normalize")])
def test_overridden_property(name, case):
    _, call, _ = CASES[case]
    seen = {}
    out = {}
    for lib in (pydub, pydub2):
        log = set()
        # a property that gives its own number, so the result shows the override was used
        attr = inspect.getattr_static(lib.AudioSegment, name)
        cls = type("Prop", (lib.AudioSegment,), {name: property(lambda self, attr=attr: (log.add(name), attr.fget(self) // 2)[1])})
        samples = array.array("h", (int(12000 * math.sin(i * 0.3)) if (i // 400) % 2 == 0 else 0 for i in range(1600)))
        s = cls(samples.tobytes(), frame_rate=8000, channels=1, sample_width=2)
        log.clear()
        out[lib.__name__] = result(call(lib, s))
        seen[lib.__name__] = log
    assert seen["pydub"] and seen["pydub2"] == seen["pydub"]
    assert out["pydub2"] == out["pydub"]


def partial_frame(lib, log, *names):
    # 70 whole frames of 3 channels and 2 stray samples
    samples = array.array("h", [int(9000 * math.sin(i * 0.3)) for i in range(212)])
    seg = tracked(lib.AudioSegment, log, *names)(samples.tobytes(), frame_rate=1000, channels=1, sample_width=2)
    return seg._spawn(samples.tobytes(), {"channels": 3, "frame_width": 6})


@pytest.mark.parametrize("case", ["compress", "low_pass", "high_pass", "detect_silence", "detect_nonsilent"])
def test_override_slow_path_with_partial_last_frame(case):
    calls = {
        "compress": lambda m, s: m.effects.compress_dynamic_range(s),
        "low_pass": lambda m, s: m.effects.low_pass_filter(s, 100),
        "high_pass": lambda m, s: m.effects.high_pass_filter(s, 100),
        "detect_silence": lambda m, s: m.silence.detect_silence(s, 10, -90),
        "detect_nonsilent": lambda m, s: m.silence.detect_nonsilent(s, 10, -90),
    }
    names = {"compress": ("rms", "get_frame"), "low_pass": ("get_array_of_samples",),
             "high_pass": ("get_array_of_samples",), "detect_silence": ("rms",), "detect_nonsilent": ("rms",)}[case]
    out = {}
    for lib in (pydub, pydub2):
        try:
            out[lib.__name__] = result(calls[case](lib, partial_frame(lib, set(), *names)))
        except Exception as e:
            out[lib.__name__] = type(e), str(e)
    assert out["pydub2"] == out["pydub"]
