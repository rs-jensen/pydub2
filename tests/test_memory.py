import pytest

from test_untrusted import peak_mb

# 24 MB of 16 bit stereo, about 2 minutes 16 s at 44.1 kHz
PCM = 24_000_000
MAKE = "import io\nfrom pydub2 import AudioSegment\nseg = AudioSegment(bytes(range(256)) * %d, sample_width=2, frame_rate=44100, channels=2)\n" % (PCM // 256)


def peak_x(code):
    """Peak of a fresh process over a bare import of pydub2, as a multiple of the PCM, the segment included."""
    return (peak_mb("import pydub2\n" + MAKE + code) - peak_mb("import pydub2")) * 1e6 / PCM


# measured on Linux with 10 minutes: ogg 3.2 before and 1.2 after, opus 3.1 and 1.2, mp3 3.4 and 1.2, flac 2.5 and 1.8
@pytest.mark.parametrize("format, bound", [("ogg", 1.7), ("opus", 1.7), ("mp3", 1.7)])
def test_export_holds_the_audio_once(format, bound):
    assert peak_x("seg.export(io.BytesIO(), format=%r)" % format) < bound
