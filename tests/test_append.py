import random

import pytest

import pydub2
from pydub2 import AudioSegment
from pydub2.exceptions import CouldntDecodeError

pydub = pytest.importorskip("pydub")


def clip(cls, width, channels, frames, rate, seed):
    data = random.Random(seed).randbytes(frames * channels * width)
    return cls(data, sample_width=width, frame_rate=rate, channels=channels)


def outcome(call):
    try:
        out = call()
    except Exception as e:
        return type(e).__name__, str(e)
    return out.raw_data, out.channels, out.frame_rate, out.sample_width


@pytest.mark.parametrize("crossfade", [1, 5, 50, 99.5, 100, 250, 1000, 5000])
@pytest.mark.parametrize("width,channels,rate", [(2, 2, 44100), (1, 1, 8000), (4, 3, 22050), (2, 1, 8000)])
@pytest.mark.parametrize("frames", [1, 400, 3000, 30000])
def test_append_with_a_crossfade_matches_pydub(crossfade, width, channels, rate, frames):
    ours = clip(AudioSegment, width, channels, frames, rate, 1), clip(AudioSegment, width, channels, frames // 2 or 1, rate, 2)
    theirs = [pydub.AudioSegment(s.raw_data, sample_width=width, frame_rate=rate, channels=channels) for s in ours]
    assert outcome(lambda: ours[0].append(ours[1], crossfade)) == outcome(lambda: theirs[0].append(theirs[1], crossfade))


def test_append_with_a_crossfade_converts_the_other_segment_like_pydub():
    a, b = clip(AudioSegment, 2, 2, 5000, 8000, 1), clip(AudioSegment, 1, 1, 3000, 22050, 2)
    p, q = (pydub.AudioSegment(s.raw_data, sample_width=s.sample_width, frame_rate=s.frame_rate, channels=s.channels)
            for s in (a, b))
    assert outcome(lambda: a.append(b, 100)) == outcome(lambda: p.append(q, 100))


class Cutting(AudioSegment):
    """Cuts through its own method, so its slices go through the hook."""

    def __getitem__(self, ms):
        return super().__getitem__(ms)


def test_append_with_a_crossfade_cuts_through_the_hook_of_a_subclass():
    a, b = clip(Cutting, 2, 2, 5000, 8000, 1), clip(Cutting, 2, 2, 3000, 8000, 2)
    plain = clip(AudioSegment, 2, 2, 5000, 8000, 1).append(clip(AudioSegment, 2, 2, 3000, 8000, 2), 100)
    out = a.append(b, 100)
    assert type(out) is Cutting and out.raw_data == plain.raw_data


def test_append_with_a_crossfade_holds_the_result_to_max_file_size(monkeypatch):
    a = clip(AudioSegment, 2, 2, 3000, 8000, 1)
    monkeypatch.setattr(pydub2, "max_file_size", 24000)
    assert len(a.append(a, 50).raw_data) == 22400
    monkeypatch.setattr(pydub2, "max_file_size", 22000)
    with pytest.raises(CouldntDecodeError, match="larger than max_file_size"):
        a.append(a, 50)
