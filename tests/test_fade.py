import os
import random
import time

import pytest

import _pydub2
from pydub2 import AudioSegment

pydub = pytest.importorskip("pydub")


class Stepped(AudioSegment):
    """Slices through its own method, so a fade takes the loop that cuts one millisecond at a time."""

    def __getitem__(self, ms):
        return super().__getitem__(ms)


def clip(cls, width, channels, frames, rate, extra=0, seed=0):
    rnd = random.Random(seed)
    data = rnd.randbytes(frames * channels * width)
    # runs of full scale so the gain clips
    top = (1 << (8 * width - 1)) - 1
    if frames > 40:
        loud = top.to_bytes(width, "little") * channels * 20
        data = data[:channels * width * 10] + loud + data[channels * width * 30:]
    seg = cls(data, sample_width=width, frame_rate=rate, channels=channels)
    # a partial last frame can only come from a decoder or a slice
    return seg._spawn(data + rnd.randbytes(extra)) if extra else seg


def outcome(call):
    try:
        out = call()
    except Exception as e:
        return type(e).__name__, str(e)
    return out.raw_data, out.channels, out.frame_rate, out.sample_width


KW = [
    dict(to_gain=-20, start=0, duration=100000),
    dict(to_gain=-20, start=0, end=float("inf")),
    dict(from_gain=-120, start=0, duration=2000),
    dict(to_gain=-120, end=float("inf"), duration=3000),
    dict(to_gain=6, from_gain=-6, start=100, end=1500),
    dict(to_gain=-12, from_gain=3, start=30, duration=250),
    dict(to_gain=40, start=7, end=9000),
    dict(to_gain=-300, from_gain=300, start=0, end=102),
    dict(to_gain=-3, start=1000, end=20000),
    dict(to_gain=-3, start=10 ** 6, end=10 ** 7),
    dict(to_gain=-3, start=-500, end=-20),
    dict(to_gain=-3, start=101, duration=101),
    dict(to_gain=float("inf"), start=0, duration=500),
    dict(to_gain=float("nan"), start=0, duration=500),
    dict(from_gain=float("-inf"), start=0, duration=500),
    dict(to_gain=-9, start=0.5, duration=500),
    dict(to_gain=-9, start=0, duration=500.0),
]


@pytest.mark.parametrize("kw", KW, ids=lambda kw: ",".join("%s=%s" % i for i in kw.items()))
@pytest.mark.parametrize("width,channels,frames,rate,extra", [
    (2, 2, 44100, 44100, 0),
    (2, 1, 3000, 8000, 1),
    (1, 3, 5000, 8000, 2),
    (4, 2, 20000, 22050, 0),
    (3, 6, 9000, 8000, 0),
    (2, 8, 4410, 44100, 5),
    (4, 1, 700, 500, 0),
    (2, 2, 1500, 1000, 0),
    (2, 1, 5, 44100, 0),
    (2, 1, 0, 44100, 0),
    (2, 2, 3000, 400, 0),
])
def test_fade_per_millisecond_matches_the_loop(kw, width, channels, frames, rate, extra):
    fast = clip(AudioSegment, width, channels, frames, rate, extra)
    slow = clip(Stepped, width, channels, frames, rate, extra)
    assert outcome(lambda: fast.fade(**kw)) == outcome(lambda: slow.fade(**kw))


@pytest.mark.parametrize("kw", KW[:8] + KW[10:12], ids=lambda kw: ",".join("%s=%s" % i for i in kw.items()))
@pytest.mark.parametrize("width,channels,frames,rate", [(2, 2, 44100, 44100), (1, 3, 5000, 8000), (4, 1, 30000, 22050),
                                                        (2, 1, 3000, 1000), (2, 1, 900, 400)])
def test_fade_per_millisecond_matches_pydub(kw, width, channels, frames, rate):
    ours = clip(AudioSegment, width, channels, frames, rate)
    theirs = pydub.AudioSegment(ours.raw_data, sample_width=width, frame_rate=rate, channels=channels)
    assert outcome(lambda: ours.fade(**kw)) == outcome(lambda: theirs.fade(**kw))


@pytest.mark.parametrize("meta", [dict(sample_width=2, channels=1, frame_width=4), dict(sample_width=1, channels=2, frame_width=4),
                                  dict(sample_width=2, channels=2.0, frame_width=4)])
@pytest.mark.parametrize("kw", [dict(to_gain=-20, start=0, duration=500), dict(from_gain=-6, start=100, end=900)])
def test_fade_per_millisecond_of_a_segment_with_its_own_frame_width_matches_the_loop(meta, kw):
    # metadata can give a frame width that is not the sample width times the channels, the loop cuts by the frame width
    data = random.Random(3).randbytes(32000)
    fast = AudioSegment(data, metadata=dict(meta, frame_rate=8000))
    slow = Stepped(data, metadata=dict(meta, frame_rate=8000))
    assert outcome(lambda: fast.fade(**kw)) == outcome(lambda: slow.fade(**kw))


def test_fade_takes_the_fast_path_only_for_a_plain_segment(monkeypatch):
    calls = []
    real = _pydub2.fade
    monkeypatch.setattr(_pydub2, "fade", lambda *a: calls.append(1) or real(*a))
    clip(AudioSegment, 2, 2, 44100, 44100).fade_in(500)
    assert calls == [1]
    clip(Stepped, 2, 2, 44100, 44100).fade_in(500)
    assert calls == [1]
    clip(AudioSegment, 2, 2, 44100, 44100).fade_in(50)
    assert calls == [1]


def test_fade_gives_up_on_a_chunk_it_cannot_make():
    # below 500 hz a millisecond is less than a frame and the loop in fade decides what happens
    assert _pydub2.fade(b"", None, bytes(600), 2, 1, 0.4, 0, 100, 1.0, 0.0, b"", None) is None


@pytest.mark.parametrize("args", [
    (b"\0", None, b"", 2, 1, 44.1, 0, 0, 1.0, 0.0, b"", None),
    (b"", None, b"\0", 2, 1, 44.1, 0, 0, 1.0, 0.0, b"", None),
    (b"", None, b"", 2, 1, 44.1, 0, 0, 1.0, 0.0, b"\0\0\0", None),
    (b"", None, b"\0\0\0\0", 2, 0, 44.1, 0, 1, 1.0, 0.0, b"", None),
    (b"", None, b"\0\0\0", 2, 2, 44.1, 0, 1, 1.0, 0.0, b"", None),
    (b"", None, b"", 5, 1, 44.1, 0, 1, 1.0, 0.0, b"", None),
])
def test_fade_checks_its_buffers(args):
    with pytest.raises(ValueError):
        _pydub2.fade(*args)


def test_fade_of_what_is_not_a_buffer():
    with pytest.raises(TypeError):
        _pydub2.fade("", None, b"", 2, 1, 44.1, 0, 0, 1.0, 0.0, b"", None)


@pytest.mark.parametrize("start,count", [(-1, 1), (0, -1), (10 ** 18, 3), (2 ** 63 - 1, 1), (0, 2 ** 63 - 1)])
def test_fade_refuses_positions_it_cannot_hold(start, count):
    assert _pydub2.fade(b"", None, bytes(88200), 2, 1, 44.1, start, count, 1.0, 0.0, b"", None) is None


def test_fade_stops_where_the_audio_ends():
    data = random.Random(2).randbytes(88200)
    assert _pydub2.fade(b"", None, data, 2, 1, 44.1, 0, 10 ** 12, 1.0, 0.0, b"", None) == data


def test_fade_reads_a_memoryview_of_the_audio():
    seg = clip(AudioSegment, 2, 2, 44100, 44100)
    data = seg.raw_data
    view = memoryview(data)
    out = _pydub2.fade(view[:400], 0.5, data, 2, 2, 44.1, 100, 300, 1.0, -0.001, view[40000:], None)
    # 300 ms from 100 ms, 13230 frames
    assert len(out) == 400 + 13230 * 4 + len(data) - 40000
    assert out[:400] == _pydub2.mul(data[:400], 2, 0.5)
    assert out[-(len(data) - 40000):] == data[40000:]


# a fade over the whole file ran a python loop with a slice, a gain and a bytes object for each millisecond. On 30 seconds of
# stereo it took 82 ms where it takes 7 ms now, so the loop is what the subclass gets here
@pytest.mark.skipif(any(k in os.environ for k in ("ASAN_OPTIONS", "UBSAN_OPTIONS", "TSAN_OPTIONS")), reason="runs under a sanitizer")
def test_a_fade_over_the_whole_file_is_not_a_loop_per_millisecond():
    fast = clip(AudioSegment, 2, 2, 44100 * 30, 44100)
    slow = clip(Stepped, 2, 2, 44100 * 30, 44100)

    def best(seg):
        out = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            seg.fade(to_gain=-20, start=0, duration=len(seg))
            out = min(out, time.perf_counter() - start)
        return out

    assert best(fast) * 3 < best(slow)
