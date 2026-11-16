import numpy as np

import _pydub2

rate = 16000


# a voice-ish buzz: harmonics of a wobbling pitch through moving formants, chopped into syllables
def talk(secs):
    t = np.arange(int(rate * secs)) / rate
    f0 = 120 + 20 * np.sin(2 * np.pi * 3 * t)
    ph = np.cumsum(f0) / rate
    f1 = 500 + 300 * np.sin(2 * np.pi * 2.5 * t)
    f2 = 1500 + 700 * np.sin(2 * np.pi * 1.7 * t)
    x = np.zeros_like(t)
    for k in range(1, 40):
        amp = np.exp(-((k * f0 - f1) / 120) ** 2) + 0.6 * np.exp(-((k * f0 - f2) / 180) ** 2) + 0.05
        x += amp * np.sin(2 * np.pi * k * ph) / k ** 0.5
    x *= (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) ** 2
    return 0.3 * x / abs(x).max()


def speech(x, channels=1):
    data = np.repeat((x * 32767).astype("<i2"), channels).tobytes()
    return _pydub2.detect_speech(data, rate, channels, 2, 0.5, 250, 100)


def test_talk_between_silence():
    x = np.concatenate([np.zeros(rate * 2), talk(2), np.zeros(rate * 2)])
    r = speech(x)
    assert len(r) == 1
    start, end = r[0]
    assert abs(start - 2000) < 100
    assert abs(end - 4000) < 300


def test_stereo_same_as_mono():
    x = np.concatenate([np.zeros(rate), talk(2), np.zeros(rate)])
    assert speech(x, 2) == speech(x)


def test_silence_and_noise():
    assert speech(np.zeros(rate * 3)) == []
    noise = np.random.default_rng(1).uniform(-0.5, 0.5, rate * 3)
    assert speech(noise) == []


def test_short():
    assert _pydub2.detect_speech(b"", rate, 1, 2, 0.5, 250, 100) == []
    assert _pydub2.detect_speech(b"\1\0" * 100, rate, 1, 2, 0.5, 250, 100) == []


def test_other_rate():
    x = np.concatenate([np.zeros(rate), talk(2), np.zeros(rate)])
    data = np.repeat((x * 32767).astype("<i2"), 3).tobytes()
    r = _pydub2.detect_speech(data, rate * 3, 1, 2, 0.5, 250, 100)
    assert len(r) == 1
    assert abs(r[0][0] - 1000) < 100


def test_24_bit_same_as_16():
    x = np.concatenate([np.zeros(rate * 2), talk(2), np.zeros(rate * 2)])
    wide = (x * 2 ** 23).astype("<i4").view(np.uint8).reshape(-1, 4)
    data = wide[:, :3].tobytes()
    assert _pydub2.detect_speech(data, rate, 1, 3, 0.5, 250, 100) == speech(x)


def test_long_input_in_pieces():
    # over a million frames at 44.1 khz, so the resample inside runs in several pieces
    x = np.tile(np.concatenate([np.zeros(rate), talk(12), np.zeros(rate)]), 2)
    big = _pydub2.resample((x * 32767).astype("<i2").tobytes(), rate, 1, 2, 44100)
    assert len(big) > 2 * (1 << 20)
    whole = _pydub2.resample(big, 44100, 1, 2, rate)
    a = _pydub2.detect_speech(big, 44100, 1, 2, 0.5, 250, 100)
    assert a and a == _pydub2.detect_speech(whole, rate, 1, 2, 0.5, 250, 100)
