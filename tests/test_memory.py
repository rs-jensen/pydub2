import pytest

from test_untrusted import peak_mb

# 24 MB of 16 bit stereo, about 2 minutes 16 s at 44.1 kHz
PCM = 24_000_000
SAW = "bytes(range(256)) * %d" % (PCM // 256)
NOISE = "os.urandom(%d)" % PCM


def peak_x(code, data=SAW):
    """Peak of a fresh process over a bare import of pydub2, as a multiple of the PCM, the segment included."""
    setup = "import os, tempfile\nfrom pydub2 import AudioSegment\n" \
        "seg = AudioSegment(%s, sample_width=2, frame_rate=44100, channels=2)\n" % data
    return (peak_mb("import pydub2\n" + setup + code) - peak_mb("import pydub2")) * 1e6 / PCM


def export(format):
    return "with tempfile.TemporaryDirectory() as d:\n    seg.export(d + '/out', format=%r)" % format


# measured with 10 minutes on Linux, before and after: ogg 3.2 and 1.2, opus 3.1 and 1.2, mp3 3.4 and 1.2, 1.5 at this size
@pytest.mark.parametrize("format", ["ogg", "opus", "mp3"])
def test_export_holds_the_audio_once(format):
    assert peak_x(export(format)) < 2.0


# 3.0 before and 2.0 after with noise, which flac cannot shrink: the audio, the file and one block
def test_export_flac_does_not_copy_the_file():
    assert peak_x(export("flac"), NOISE) < 2.5
