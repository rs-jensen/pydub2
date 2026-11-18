import shutil

import pytest

import pydub2
import pydub2.generators
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


class Plain(float):
    # ffmpeg does not read 5e-05, so give pydub the same number without an exponent
    def __str__(self):
        return format(self, "f")


@needs_ff
@pytest.mark.parametrize("kind", ["start_second", "duration"])
@pytest.mark.parametrize("value", [1e-5, 5e-5, 0.00012, 0.0005])
def test_stream_small_floats(tmp_path, kind, value):
    pydub = pytest.importorskip("pydub")
    seg = pydub2.generators.Sine(440, sample_rate=48000).to_audio_segment(duration=500)
    seg.export(str(tmp_path / "a.flac"), format="flac")
    want = pydub.AudioSegment.from_file(str(tmp_path / "a.flac"), **{kind: Plain(value)}).raw_data
    got = b"".join(c.raw_data for c in pydub2.AudioSegment.stream(str(tmp_path / "a.flac"), **{kind: value}))
    assert got == want
