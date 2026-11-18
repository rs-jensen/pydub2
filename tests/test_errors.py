import shutil

import pytest

import pydub2
from pydub2.exceptions import CouldntEncodeError

needs_ff = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


@pytest.mark.parametrize("bitrate", ["k", "1e400k", "1e400", "abc", "infk"])
@pytest.mark.parametrize("format", ["ogg", "opus"])
def test_bad_bitrate_native(format, bitrate):
    with pytest.raises(CouldntEncodeError):
        pydub2.AudioSegment.silent(500).export(format=format, bitrate=bitrate)


@needs_ff
@pytest.mark.parametrize("bitrate", ["k", "1e400k", "1e400", "abc", "infk"])
def test_bad_bitrate_mp3(bitrate):
    with pytest.raises(CouldntEncodeError):
        pydub2.AudioSegment.silent(500).export(format="mp3", bitrate=bitrate)
