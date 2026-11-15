import array
import math

import pytest

import _pydub2

pydub = pytest.importorskip("pydub")
from pydub import AudioSegment
from pydub.silence import detect_silence as pydub_detect


def signal(parts, rate, channels, width):
    # parts is a list of (seconds, amplitude as a fraction of full scale)
    typecode = {1: "b", 2: "h", 4: "i"}[width]
    full = 2 ** (8 * width - 1) - 1
    samples = array.array(typecode)
    f = 0
    for secs, amp in parts:
        for _ in range(int(round(secs * rate))):
            v = int(amp * full * math.sin(2 * math.pi * 440 * f / rate))
            for ch in range(channels):
                samples.append(v if ch == 0 else v // 2)
            f += 1
    return samples.tobytes()


def ours(data, rate, channels, width, min_len, thresh, step):
    return [list(r) for r in _pydub2.detect_silence(data, rate, channels, width, min_len, thresh, step)]


def theirs(data, rate, channels, width, min_len, thresh, step):
    seg = AudioSegment(data, sample_width=width, frame_rate=rate, channels=channels)
    return pydub_detect(seg, min_len, thresh, step)


SIGNALS = {
    "tone_silence_tone_silence": [(0.7, 0.5), (0.6, 0), (0.5, 0.8), (0.9, 0)],
    "tone_silence_at_end": [(0.5, 0.5), (1.1, 0)],
    "all_silent": [(1.5, 0)],
    "all_loud": [(1.5, 0.9)],
    "short": [(0.1, 0)],
    "odd_length": [(0.5123, 0.3), (0.4011, 0), (0.3317, 0.6)],
}


@pytest.mark.parametrize("step", [1, 7])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_matches_pydub(name, width, channels, step):
    rate = 8000
    data = signal(SIGNALS[name], rate, channels, width)
    want = theirs(data, rate, channels, width, 300, -40, step)
    assert ours(data, rate, channels, width, 300, -40, step) == want


@pytest.mark.parametrize("min_len, thresh", [(100, -20), (500, -50), (250, -10)])
def test_matches_pydub_other_params(min_len, thresh):
    rate = 22050
    data = signal(SIGNALS["odd_length"] + [(0.8, 0.02)], rate, 2, 2)
    for step in (1, 7, 50):
        want = theirs(data, rate, 2, 2, min_len, thresh, step)
        assert ours(data, rate, 2, 2, min_len, thresh, step) == want


def test_seek_step_zero_raises():
    with pytest.raises(ValueError):
        _pydub2.detect_silence(b"\0" * 100, 8000, 1, 2, 10, -40.0, 0)
