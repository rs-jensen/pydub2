import os
import random

import pytest

pydub = pytest.importorskip("pydub")
import _pydub2
import pydub2


def segs(data, width, channels):
    kw = dict(sample_width=width, frame_rate=8000, channels=channels)
    return pydub.AudioSegment(data=data, **kw), pydub2.AudioSegment(data=data, **kw)


def run(seg):
    try:
        return seg.set_channels(1).raw_data
    except OverflowError:
        return "overflow"


@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [3, 4, 5, 6, 8, 20])
def test_downmix_matches_pydub(width, channels):
    rnd = random.Random(width * 100 + channels)
    peak = 2 ** (8 * width - 1)
    # quiet noise stays in range, loud noise has the same quirks as pydub
    for loud in (peak // 64, peak):
        vals = [rnd.randrange(-loud, loud) for _ in range(300 * channels)]
        data = b"".join(v.to_bytes(width, "little", signed=True) for v in vals)
        old, new = segs(data, width, channels)
        want = run(old)
        assert want != "overflow" or loud == peak
        got = run(new)
        assert got == want
        if got != "overflow":
            assert new.set_channels(1).channels == 1


@pytest.mark.parametrize("width", [1, 2, 4])
def test_downmix_overflow_like_pydub(width):
    peak = 2 ** (8 * width - 1)
    for v in (-peak, peak - 1, 0, -1):
        data = v.to_bytes(width, "little", signed=True) * 9
        old, new = segs(data, width, 3)
        assert run(new) == run(old)
    # floor division makes three minimums add up to one below the minimum
    assert run(segs((-peak).to_bytes(width, "little", signed=True) * 3, width, 3)[1]) == "overflow"


def test_downmix_empty_and_ragged():
    assert pydub2.AudioSegment(data=b"", sample_width=2, frame_rate=8000, channels=3).set_channels(1).raw_data == b""
    with pytest.raises(ValueError):
        _pydub2.downmix(b"\0" * 7, 2, 3)
