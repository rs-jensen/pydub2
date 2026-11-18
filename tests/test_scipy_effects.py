import numpy as np
import pytest

pytest.importorskip("scipy")
pydub = pytest.importorskip("pydub")
import pydub.scipy_effects as old_effects
import pydub2
import pydub2.scipy_effects as effects


def pair(channels=1, width=2, rate=16000, ms=600, seed=0):
    rng = np.random.default_rng(seed)
    n = rate * ms // 1000
    t = np.arange(n) / rate
    x = sum(np.sin(2 * np.pi * f * t) for f in (150, 800, 3000, 6500)) / 4 + rng.normal(0, 0.05, n)
    x = np.stack([x, np.roll(x, 25)], 1)[:, :channels]
    kw = dict(data=(x * 2 ** (8 * width - 2)).astype({1: "i1", 2: "<i2", 4: "<i4"}[width]).tobytes(),
              sample_width=width, frame_rate=rate, channels=channels)
    return pydub2.AudioSegment(**kw), pydub.AudioSegment(**kw)


@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("low, high, order", [(300, 3000, 5), (100, 7000, 2), (1000, 1500, 8)])
def test_band_pass_filter(channels, width, low, high, order):
    new, old = pair(channels, width)
    a = effects.band_pass_filter(new, low, high, order)
    b = old_effects.band_pass_filter(old, low, high, order)
    assert a.raw_data == b.raw_data
    assert (a.channels, a.frame_rate, a.sample_width) == (b.channels, b.frame_rate, b.sample_width)
    assert a.raw_data != new.raw_data


def test_band_pass_filter_is_a_method_once_the_module_is_imported():
    new, old = pair(2)
    assert new.band_pass_filter(300, 3000).raw_data == old.band_pass_filter(300, 3000).raw_data


@pytest.mark.parametrize("name", ["low_pass_filter", "high_pass_filter"])
@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("cutoff, order", [(500, 5), (4000, 2), (7000, 8)])
def test_low_and_high_pass_filter(name, channels, width, cutoff, order):
    new, old = pair(channels, width)
    a = getattr(effects, name)(new, cutoff, order)
    b = getattr(old_effects, name)(old, cutoff, order)
    assert a.raw_data == b.raw_data
    assert a.raw_data != new.raw_data


@pytest.mark.parametrize("mode", ["peak", "low_shelf", "high_shelf"])
@pytest.mark.parametrize("gain", [6, -6])
@pytest.mark.parametrize("channels", [1, 2])
def test_eq_like_pydub(mode, channels, gain):
    new, old = pair(channels)
    a = effects.eq(new, 1000, 400, "L+R", mode, gain)
    assert a.raw_data == old_effects.eq(old, 1000, 400, "L+R", mode, gain).raw_data
    assert a.raw_data != new.raw_data
