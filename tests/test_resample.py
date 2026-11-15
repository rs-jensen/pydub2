import math

import numpy as np
import pytest

import _pydub2

DTYPE = {1: np.int8, 2: np.int16, 4: np.int32}


def sine(rate, freq, seconds, width=2, channels=1, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    x = np.sin(2 * math.pi * freq * t) * amp * (2 ** (8 * width - 1) - 1)
    x = np.repeat(x[:, None], channels, axis=1)
    return x.astype(DTYPE[width]).tobytes()


def resample(data, rate, channels, width, new_rate):
    return _pydub2.resample(data, rate, channels, width, new_rate)


def to_array(data, width, channels):
    return np.frombuffer(data, dtype=DTYPE[width]).reshape(-1, channels).astype(float)


def rms_db(x):
    return 20 * math.log10(np.sqrt(np.mean(x ** 2)))


def test_round_trip_keeps_frequency_and_level():
    data = sine(44100, 1000, 1.0)
    down = resample(data, 44100, 1, 2, 16000)
    back = resample(down, 16000, 1, 2, 44100)
    x = to_array(back, 2, 1)[:, 0]
    y = to_array(data, 2, 1)[:, 0]
    mid = slice(4410, -4410) # skip the filter edges
    assert abs(rms_db(x[mid]) - rms_db(y[mid])) < 0.1
    spec = np.abs(np.fft.rfft(x))
    assert abs(np.argmax(spec) * 44100 / len(x) - 1000) < 1


@pytest.mark.parametrize("rate,new_rate", [(44100, 16000), (16000, 44100), (48000, 22050), (8000, 44100), (44100, 48000)])
@pytest.mark.parametrize("frames", [1000, 12345])
def test_frame_count_is_exact(rate, new_rate, frames):
    data = sine(rate, 440, frames / rate)
    out = resample(data, rate, 1, 2, new_rate)
    assert len(out) // 2 == round(frames * new_rate / rate)


@pytest.mark.parametrize("width", [1, 2, 4])
def test_widths_and_channels(width):
    data = sine(44100, 1000, 0.5, width=width, channels=3)
    out = resample(data, 44100, 3, width, 16000)
    assert len(out) == round(22050 * 16000 / 44100) * 3 * width
    arr = to_array(out, width, 3)
    assert np.abs(arr).max() > 0


def test_stereo_channels_stay_separate():
    left = sine(44100, 1000, 0.5)
    frames = np.frombuffer(left, dtype=np.int16).reshape(-1, 1)
    both = np.hstack([frames, np.zeros_like(frames)]).tobytes()
    out = to_array(resample(both, 44100, 2, 2, 16000), 2, 2)
    assert np.abs(out[:, 1]).max() == 0
    assert rms_db(out[:, 0]) > -20


def test_full_scale_does_not_wrap():
    data = np.full(44100, 32767, dtype=np.int16).tobytes()
    out = to_array(resample(data, 44100, 1, 2, 16000), 2, 1)[:, 0]
    mid = out[200:-200]
    assert mid.min() > 32000
    assert out.max() <= 32767
    assert out.min() >= -32768


def test_empty_input_gives_empty_output():
    assert resample(b"", 44100, 1, 2, 16000) == b""


def test_same_rate_returns_input():
    data = sine(44100, 1000, 0.1)
    assert resample(data, 44100, 1, 2, 44100) == data
