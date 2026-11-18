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
