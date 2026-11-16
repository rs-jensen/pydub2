import array
import math

import pytest

pydub = pytest.importorskip("pydub")
import pydub.effects as old_effects
import pydub.generators as old_gen
from pydub import AudioSegment as OldSegment

import _pydub2
from pydub2 import AudioSegment
from pydub2 import effects
from pydub2 import generators


def make(rate=8000, channels=1):
    out = array.array("h")
    for ms, loud in [(300, 0), (500, 1), (400, 0), (700, 1), (300, 0)]:
        for i in range(rate * ms // 1000):
            v = int((3000 + 200 * (i % 50)) * math.sin(i * 2 * math.pi * 440 / rate)) if loud else 0
            out.append(v)
            if channels == 2:
                out.append(v // 3 if loud else 0)
    return out.tobytes()


def pair(rate=8000, channels=1):
    kw = dict(data=make(rate, channels), sample_width=2, frame_rate=rate, channels=channels)
    return AudioSegment(**kw), OldSegment(**kw)


def same(a, b):
    assert a.raw_data == b.raw_data
    assert (a.channels, a.frame_rate, a.sample_width) == (b.channels, b.frame_rate, b.sample_width)


@pytest.mark.parametrize("channels", [1, 2])
def test_normalize(channels):
    new, old = pair(channels=channels)
    same(effects.normalize(new), old_effects.normalize(old))
    same(effects.normalize(new, 6), old_effects.normalize(old, 6))


def test_normalize_silent():
    new = AudioSegment(data=b"\0\0" * 800, sample_width=2, frame_rate=8000, channels=1)
    assert effects.normalize(new, target_lufs=-23).raw_data == new.raw_data


def test_normalize_lufs():
    new = AudioSegment(data=make(48000), sample_width=2, frame_rate=48000, channels=1)
    out = effects.normalize(new, target_lufs=-23)
    now = _pydub2.loudness(out.raw_data, out.frame_rate, out.channels, out.sample_width)
    assert abs(now + 23) < 0.1


def test_normalize_lufs_keeps_the_peak_under_full_scale():
    pcm = array.array("h", (int(100 * math.sin(i * 0.3)) for i in range(16000)))
    pcm[4000] = pcm[12000] = 3000
    new = AudioSegment(data=pcm.tobytes(), sample_width=2, frame_rate=8000, channels=1)
    out = effects.normalize(new, target_lufs=-14)
    top = 32767 * 10 ** (-0.1 / 20)
    assert top * 0.99 < out.max <= top + 1
    out = effects.normalize(new, headroom=3, target_lufs=-14)
    assert 32767 * 10 ** (-3 / 20) * 0.99 < out.max <= 32767 * 10 ** (-3 / 20) + 1
    # a target that needs less gain than the peak allows is met as before
    out = effects.normalize(new, target_lufs=-60)
    assert abs(_pydub2.loudness(out.raw_data, 8000, 1, 2) + 60) < 0.1

def test_speedup():
    new, old = pair()
    same(effects.speedup(new), old_effects.speedup(old))
    same(effects.speedup(new, 2.0, 100, 10), old_effects.speedup(old, 2.0, 100, 10))


def test_strip_silence():
    new, old = pair()
    same(effects.strip_silence(new, 300, -30, 100), old_effects.strip_silence(old, 300, -30, 100))
    with pytest.raises(Exception):
        effects.strip_silence(new, 100, -30, 200)


def test_compress_dynamic_range():
    new, old = pair(rate=2000)
    same(effects.compress_dynamic_range(new), old_effects.compress_dynamic_range(old))


@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [1, 2, 3])
def test_compress_and_filters_widths(width, channels):
    import random
    random.seed(width * 10 + channels)
    top = 2 ** (8 * width - 1)
    n = 1500 * channels
    data = array.array({1: "b", 2: "h", 4: "i"}[width], [
        random.randint(-top, top - 1) if i // 300 % 2 else random.randint(-top // 40, top // 40) for i in range(n)])
    kw = dict(data=data.tobytes(), sample_width=width, frame_rate=2000, channels=channels)
    new, old = AudioSegment(**kw), OldSegment(**kw)
    for args in [dict(), dict(threshold=-30.0, ratio=3.0, attack=2.0, release=20.0), dict(threshold=-6, ratio=20, attack=0.3, release=1)]:
        same(effects.compress_dynamic_range(new, **args), old_effects.compress_dynamic_range(old, **args))
    for cutoff in (50, 900):
        same(effects.low_pass_filter(new, cutoff), old_effects.low_pass_filter(old, cutoff))
        same(effects.high_pass_filter(new, cutoff), old_effects.high_pass_filter(old, cutoff))


def test_compress_zero_threshold():
    new, old = pair(rate=2000)
    with pytest.raises(ZeroDivisionError):
        old_effects.compress_dynamic_range(old, threshold=-float("inf"))
    with pytest.raises(ZeroDivisionError):
        effects.compress_dynamic_range(new, threshold=-float("inf"))
    quiet = AudioSegment.silent(100)
    assert effects.compress_dynamic_range(quiet, threshold=-float("inf")) == quiet


@pytest.mark.parametrize("channels", [1, 2])
def test_invert_phase(channels):
    new, old = pair(channels=channels)
    same(effects.invert_phase(new), old_effects.invert_phase(old))
    if channels == 2:
        same(effects.invert_phase(new, (1, 0)), old_effects.invert_phase(old, (1, 0)))
        same(effects.invert_phase(new, (0, 1)), old_effects.invert_phase(old, (0, 1)))


@pytest.mark.parametrize("channels", [1, 2])
def test_filters(channels):
    new, old = pair(channels=channels)
    same(effects.low_pass_filter(new, 500), old_effects.low_pass_filter(old, 500))
    same(effects.high_pass_filter(new, 500), old_effects.high_pass_filter(old, 500))


@pytest.mark.parametrize("amount", [-1.0, -0.3, 0.0, 0.5, 1.0])
def test_pan(amount):
    new, old = pair(channels=2)
    same(effects.pan(new, amount), old_effects.pan(old, amount))


def test_pan_range():
    new, _ = pair(channels=2)
    with pytest.raises(ValueError):
        effects.pan(new, 1.5)


@pytest.mark.parametrize("channels", [1, 2])
def test_apply_gain_stereo(channels):
    new, old = pair(channels=channels)
    same(effects.apply_gain_stereo(new, 3, -6), old_effects.apply_gain_stereo(old, 3, -6))


def test_apply_mono_filter_to_each_channel():
    new, old = pair(channels=2)
    same(effects.apply_mono_filter_to_each_channel(new, lambda s: s.apply_gain(-3)),
         old_effects.apply_mono_filter_to_each_channel(old, lambda s: s.apply_gain(-3)))


@pytest.mark.parametrize("name", ["Sine", "Square", "Sawtooth", "Triangle", "Pulse"])
@pytest.mark.parametrize("depth", [8, 16, 32])
def test_generators(name, depth):
    a = getattr(generators, name)(440, sample_rate=8000, bit_depth=depth).to_audio_segment(300, -6)
    b = getattr(old_gen, name)(440, sample_rate=8000, bit_depth=depth).to_audio_segment(300, -6)
    same(a, b)


def test_white_noise():
    a = generators.WhiteNoise(sample_rate=8000).to_audio_segment(250)
    assert len(a) == 250
    assert (a.channels, a.frame_rate, a.sample_width) == (1, 8000, 2)
