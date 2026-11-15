import array
import math
import os

import pytest

pydub = pytest.importorskip("pydub")
import pydub.silence as old_silence
from pydub import AudioSegment as OldSegment

from pydub2 import AudioSegment
from pydub2 import silence


def make(rate=8000, channels=1):
    out = array.array("h")
    for ms, loud in [(700, 0), (500, 1), (1200, 0), (300, 1), (900, 0), (400, 1), (150, 0)]:
        for i in range(rate * ms // 1000):
            v = int(12000 * math.sin(i * 2 * math.pi * 440 / rate)) if loud else 0
            out.extend([v] * channels)
    return out.tobytes()


def pair(rate=8000, channels=1):
    kw = dict(data=make(rate, channels), sample_width=2, frame_rate=rate, channels=channels)
    return AudioSegment(**kw), OldSegment(**kw)


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("args", [
    dict(),
    dict(min_silence_len=300, silence_thresh=-30),
    dict(min_silence_len=250, silence_thresh=-40, seek_step=7),
    dict(min_silence_len=100, silence_thresh=-20, seek_step=50),
    dict(min_silence_len=100000),
])
def test_detect(channels, args):
    new, old = pair(channels=channels)
    assert silence.detect_silence(new, **args) == old_silence.detect_silence(old, **args)
    assert silence.detect_nonsilent(new, **args) == old_silence.detect_nonsilent(old, **args)


@pytest.mark.parametrize("keep", [0, 100, 5000, True, False])
def test_split_on_silence(keep):
    new, old = pair()
    a = silence.split_on_silence(new, 300, -30, keep)
    b = old_silence.split_on_silence(old, 300, -30, keep)
    assert len(a) == len(b) > 1
    for x, y in zip(a, b):
        assert x.raw_data == y.raw_data


def test_leading_silence():
    new, old = pair()
    assert silence.detect_leading_silence(new) == old_silence.detect_leading_silence(old)
    assert silence.detect_leading_silence(new, -20, 25) == old_silence.detect_leading_silence(old, -20, 25)


def test_all_silent():
    new = AudioSegment(data=b"\0\0" * 8000, sample_width=2, frame_rate=8000, channels=1)
    assert silence.detect_nonsilent(new, 100) == []
    assert silence.detect_speech(new) == []
    assert silence.split_on_speech(new) == []


def speech():
    path = os.path.join(os.path.dirname(__file__), "speech.wav")
    if not os.path.exists(path):
        pytest.skip("no speech.wav")
    return AudioSegment.from_file(path)


def test_detect_speech():
    seg = speech()
    r = silence.detect_speech(seg)
    assert r
    assert all(0 <= s < e <= len(seg) for s, e in r)


def test_split_on_speech():
    seg = speech()
    parts = silence.split_on_speech(seg, keep_silence=0)
    assert len(parts) == len(silence.detect_speech(seg))
