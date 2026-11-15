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
