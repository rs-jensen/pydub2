import array
import math
import random

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


def naive(data, rate, channels, width, min_len, db, step):
    audioop = pytest.importorskip("audioop")
    fw = channels * width
    seg_len = round(1000 * (len(data) // fw) / rate)
    if seg_len < min_len:
        return []
    thresh = 10 ** (db / 20) * 2 ** (8 * width - 1)
    per_ms = rate / 1000

    def is_silent(i):
        b = int(i * per_ms) * fw
        e = int((i + min_len) * per_ms) * fw
        s = data[b:e]
        s += bytes(e - b - len(s))
        return audioop.rms(s, width) <= thresh

    last = seg_len - min_len
    starts = list(range(0, last + 1, step))
    if last % step:
        starts.append(last)
    starts = [i for i in starts if is_silent(i)]
    if not starts:
        return []
    out = []
    prev = cur = starts.pop(0)
    for s in starts:
        if s != prev + step and s > prev + min_len:
            out.append([cur, prev + min_len])
            cur = s
        prev = s
    out.append([cur, prev + min_len])
    return out


def random_signal(rnd, rate, channels, width):
    full = 2 ** (8 * width - 1)
    samples = []
    for _ in range(rnd.randint(1, 6)):
        n = rnd.randint(1, rate // 4) * channels
        amp = rnd.choice([0, 1, full // 1000, full // 50, full // 3, full - 1])
        samples += [rnd.randint(-amp, amp) for _ in range(n)]
    samples = samples[:len(samples) // channels * channels]
    out = bytearray()
    for v in samples:
        out += v.to_bytes(width, "little", signed=True)
    return bytes(out)


def test_matches_naive_walk():
    audioop = pytest.importorskip("audioop")
    rnd = random.Random(7)
    for _ in range(300):
        width = rnd.choice([1, 2, 3, 4])
        channels = rnd.choice([1, 2])
        rate = rnd.choice([1000, 8000, 11025, 16000])
        data = random_signal(rnd, rate, channels, width)
        fw = channels * width
        if rnd.random() < 0.3:
            data = data[:len(data) - fw]
        min_len = rnd.choice([1, 20, 50, 333])
        step = rnd.choice([1, 3, 10, 50])
        i = rnd.randrange(max(1, len(data) // fw - 1))
        win = data[i * fw:(i + max(1, min_len * rate // 1000)) * fw]
        r = max(audioop.rms(win, width), 1)
        db = 20 * math.log10(r / 2 ** (8 * width - 1)) + rnd.choice([0, 0, 1e-9, -1e-9, 0.01, -0.01, 3])
        want = naive(data, rate, channels, width, min_len, db, step)
        assert ours(data, rate, channels, width, min_len, db, step) == want
