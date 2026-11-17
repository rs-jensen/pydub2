import math

import numpy as np
import pytest

import _pydub2
from pydub2 import AudioSegment

TYPES = {1: np.int8, 2: np.int16, 4: np.int32}


def tone(freq=440, ms=2000, rate=44100, channels=1, width=2):
    t = np.arange(rate * ms // 1000) / rate
    x = 0.4 * np.sin(2 * np.pi * freq * t) * 2 ** (8 * width - 1)
    x = np.repeat(x[:, None], channels, 1).astype(TYPES[width])
    return AudioSegment(data=x.tobytes(), sample_width=width, frame_rate=rate, channels=channels)


def frames(seg):
    return len(seg.raw_data) // seg.frame_width


def peak(seg):
    x = seg.to_numpy()[:, 0].astype(float)
    x = x * np.hanning(len(x))
    return np.fft.rfftfreq(len(x), 1 / seg.frame_rate)[np.abs(np.fft.rfft(x)).argmax()]


def jump(seg):
    return np.abs(np.diff(seg.to_numpy().astype(float), axis=0)).max()


@pytest.mark.parametrize("rate", [0.25, 0.5, 0.8, 1.3, 2.0, 4.0])
@pytest.mark.parametrize("channels", [1, 2, 6])
def test_length(rate, channels):
    seg = tone(ms=1000, channels=channels)
    out = seg.time_stretch(rate)
    assert abs(frames(out) - frames(seg) / rate) <= 1
    assert (out.frame_rate, out.channels, out.sample_width) == (seg.frame_rate, seg.channels, seg.sample_width)


@pytest.mark.parametrize("rate", [0.5, 0.75, 1.5, 2.0])
def test_time_stretch_keeps_the_pitch(rate):
    seg = tone(440)
    assert abs(peak(seg.time_stretch(rate)) / 440 - 1) < 0.01


@pytest.mark.parametrize("semitones", [-12, -5, 3, 7, 12])
def test_pitch_shift(semitones):
    seg = tone(440)
    out = seg.pitch_shift(semitones)
    assert frames(out) == frames(seg)
    assert abs(peak(out) / (440 * 2 ** (semitones / 12)) - 1) < 0.01


@pytest.mark.parametrize("fn,arg", [("time_stretch", 0.7), ("time_stretch", 1.6), ("pitch_shift", 5)])
def test_stereo_stays_aligned(fn, arg):
    # right is half of left, so any offset between the channels shows
    left = (np.random.default_rng(1).standard_normal(30000) * 6000).astype(np.int16)
    x = np.stack([left, left // 2], 1)
    seg = AudioSegment(data=x.tobytes(), sample_width=2, frame_rate=22050, channels=2)
    out = getattr(seg, fn)(arg).to_numpy().astype(int)
    assert np.abs(out[:, 0]).max() > 3000
    assert np.abs(out[:, 0] - 2 * out[:, 1]).max() <= 4


@pytest.mark.parametrize("rate", [0.3, 0.6, 1.4, 3.0])
def test_no_clicks(rate):
    # faded in and out so the input is smooth at the ends too
    seg = tone(440, channels=2).fade_in(50).fade_out(50)
    out = seg.time_stretch(rate)
    assert jump(out) <= jump(seg) * 1.05


@pytest.mark.parametrize("semitones", [-7, 4])
def test_no_clicks_pitch(semitones):
    seg = tone(440).fade_in(50).fade_out(50)
    out = seg.pitch_shift(semitones)
    # a higher tone moves further between samples
    assert jump(out) <= jump(seg) * 2 ** (semitones / 12) * 1.05


def test_unchanged():
    seg = tone(ms=300, channels=2)
    assert seg.time_stretch(1.0).raw_data == seg.raw_data
    assert seg.time_stretch(1).raw_data == seg.raw_data
    assert seg.pitch_shift(0).raw_data == seg.raw_data
    assert seg.pitch_shift(0.0).raw_data == seg.raw_data


@pytest.mark.parametrize("width", [1, 2, 4])
def test_widths(width):
    seg = tone(440, ms=1000, rate=16000, channels=2, width=width)
    out = seg.time_stretch(1.5)
    assert out.sample_width == width
    assert abs(frames(out) - frames(seg) / 1.5) <= 1
    assert abs(peak(out) / 440 - 1) < 0.01
    assert abs(out.rms / seg.rms - 1) < 0.05
    out = seg.pitch_shift(-3)
    assert frames(out) == frames(seg)
    assert abs(peak(out) / (440 * 2 ** (-3 / 12)) - 1) < 0.01


def test_24_bit():
    x = np.round(0.4 * np.sin(np.arange(8000) * 2 * np.pi * 440 / 8000) * 2 ** 23).astype("<i4")
    data = b"".join(v.tobytes()[:3] for v in x)
    out = _pydub2.stretch(data, 8000, 1, 3, 2.0)
    assert len(out) == 4000 * 3
    y = np.array([int.from_bytes(out[i:i + 3], "little", signed=True) for i in range(0, len(out), 3)])
    assert 0.38 * 2 ** 23 < np.abs(y).max() <= 0.41 * 2 ** 23


@pytest.mark.parametrize("n", [1, 2, 3, 7, 100, 1000])
@pytest.mark.parametrize("rate", [0.25, 0.9, 4.0])
def test_short(n, rate):
    # one window is 1323 frames at 44.1 kHz
    seg = tone(ms=1000, channels=2)[:0]._spawn(tone(ms=1000, channels=2).raw_data[:n * 4])
    assert frames(seg) == n
    assert abs(frames(seg.time_stretch(rate)) - n / rate) <= 1
    assert frames(seg.pitch_shift(12 * math.log2(rate))) == n


def test_empty():
    seg = AudioSegment.empty()
    assert len(seg.time_stretch(2)) == 0
    assert len(seg.pitch_shift(-3)) == 0
    seg = AudioSegment.silent(0, frame_rate=48000)
    assert seg.time_stretch(0.5).raw_data == b""


def test_silence_stays_silent():
    seg = AudioSegment.silent(500, frame_rate=8000)
    assert seg.time_stretch(0.7).max == 0
    assert seg.pitch_shift(2).max == 0


@pytest.mark.parametrize("rate", [0, -1, 0.2, 4.01, float("nan"), float("inf"), -float("inf")])
def test_bad_rate(rate):
    with pytest.raises(ValueError):
        tone(ms=100).time_stretch(rate)


@pytest.mark.parametrize("semitones", [24.5, -25, float("nan"), float("inf"), -float("inf")])
def test_bad_semitones(semitones):
    with pytest.raises(ValueError):
        tone(ms=100).pitch_shift(semitones)


def test_limits():
    seg = tone(ms=200)
    assert abs(frames(seg.time_stretch(0.25)) - 4 * frames(seg)) <= 1
    assert abs(frames(seg.time_stretch(4)) - frames(seg) / 4) <= 1
    assert frames(seg.pitch_shift(24)) == frames(seg.pitch_shift(-24)) == frames(seg)


def test_core_checks():
    with pytest.raises(ValueError):
        _pydub2.stretch(b"\0\0", 8000, 1, 2, 0.0)
    with pytest.raises(ValueError):
        _pydub2.stretch(b"\0\0", 8000, 1, 2, float("nan"))
    with pytest.raises(ValueError):
        _pydub2.stretch(b"\0\0\0", 8000, 1, 2, 2.0)


def test_random_input_at_odd_sizes():
    # odd rates, widths and lengths around the window size, run under the sanitizer build too
    rng = np.random.default_rng(7)
    for _ in range(200):
        width, channels = int(rng.choice([1, 2, 3, 4])), int(rng.integers(1, 4))
        rate = int(rng.choice([1, 100, 8000, 11025, 44100, 96000]))
        n = int(rng.integers(0, 3000))
        data = rng.integers(0, 256, n * width * channels, dtype=np.uint8).tobytes()
        speed = float(rng.uniform(0.25, 4))
        out = _pydub2.stretch(data, rate, channels, width, speed)
        assert len(out) == round(n / speed) * width * channels


@pytest.mark.parametrize("rate", [0.5, 0.8, 1.25, 1.5, 2.0])
def test_inverted_channel(rate):
    # left plus right is silence here, the search still has to line the windows up
    x = tone(440).to_numpy()
    seg = AudioSegment(data=np.hstack([x, -x]).tobytes(), sample_width=2, frame_rate=44100, channels=2)
    out = seg.time_stretch(rate)
    y = out.to_numpy().astype(int)
    assert np.abs(y[:, 0] + y[:, 1]).max() <= 2
    assert abs(out.rms / seg.rms - 1) < 0.02
    mid = np.abs(y[2000:-2000, 0])
    top = np.array([mid[i:i + 200].max() for i in range(0, len(mid) - 200, 200)])
    assert top.min() > 0.95 * top.max()


@pytest.mark.parametrize("rate", [0.25, 0.7, 0.9, 1.1, 1.5, 2.0, 3.0, 4.0])
def test_no_silence_at_the_end(rate):
    # a constant level, so a frame taken from behind the end of the input shows as a drop
    for n in [44100, 43769, 43400, 3000, 700, 50]:
        x = np.full(n, 1000, np.int16)
        seg = AudioSegment(data=x.tobytes(), sample_width=2, frame_rate=44100, channels=1)
        assert seg.time_stretch(rate).to_numpy().min() >= 999
    out = AudioSegment(data=np.full(44100, 1000, np.int16).tobytes(), sample_width=2, frame_rate=44100, channels=1).pitch_shift(7)
    assert out.to_numpy()[:-100].min() >= 900
