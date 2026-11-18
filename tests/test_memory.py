import pytest

from test_untrusted import peak_mb

# 24 MB of 16 bit stereo, about 2 minutes 16 s at 44.1 kHz
PCM = 24_000_000
IMPORTS = "import os, tempfile\nfrom pydub2 import AudioSegment\n"
SAW = "bytes(range(256)) * %d" % (PCM // 256)
NOISE = "os.urandom(%d)" % PCM
STEREO = IMPORTS + "seg = AudioSegment(%s, sample_width=2, frame_rate=44100, channels=2)\n"
# two mono segments that share nothing, half the PCM each
MONO = IMPORTS + "mono = [AudioSegment(bytes(range(256)) * %d, sample_width=2, frame_rate=44100, channels=1) for _ in range(2)]\n" % (PCM // 512)
# 24 bit audio as it comes from a file, three quarters of the PCM
RAW24 = IMPORTS + "raw = bytes(range(192)) * %d\n" % (PCM // 4 * 3 // 192)


def peak_x(code, setup=STEREO % SAW, pcm=PCM):
    """Peak of a fresh process over a bare import of pydub2, as a multiple of the PCM, the audio included."""
    return (peak_mb("import pydub2\n" + setup + code) - peak_mb("import pydub2")) * 1e6 / pcm


def export(format):
    return "with tempfile.TemporaryDirectory() as d:\n    seg.export(d + '/out', format=%r)" % format


# measured with 10 minutes on Linux, before and after: ogg 3.2 and 1.2, opus 3.1 and 1.2, mp3 3.4 and 1.2, 1.5 at this size
@pytest.mark.parametrize("format", ["ogg", "opus", "mp3"])
def test_export_holds_the_audio_once(format):
    assert peak_x(export(format)) < 2.0


# 3.0 before and 2.0 after with noise, which flac cannot shrink: the audio, the file and one block
def test_export_flac_does_not_copy_the_file():
    assert peak_x(export("flac"), STEREO % NOISE) < 2.5


# 3.2 before and 2.1 after: the audio and a result a tenth longer, which was held twice
def test_set_frame_rate_writes_the_result_in_place():
    assert peak_x("seg.set_frame_rate(48000)") < 2.6


# 5.8 before and 2.0 after at this size, the audio and the result
def test_overlay_loop_with_gain_makes_one_copy():
    assert peak_x("seg.overlay(seg[:1000], position=5000, loop=True, gain_during_overlay=-6)") < 3.0


# 3.0 before and 2.0 after: the audio and the result, the parts that are not faded were copied once more
@pytest.mark.parametrize("call", ["fade_in(2000)", "fade_out(2000)"])
def test_fade_does_not_copy_what_it_leaves_alone(call):
    assert peak_x("seg." + call) < 2.5


# 3.4 before and 2.0 after at this size: the audio and the channels, there was an array of the whole audio as well
def test_split_to_mono_copies_each_sample_once():
    assert peak_x("seg.split_to_mono()") < 2.7


# 3.9 before and 2.0 after at this size: the channels and the result, there was an array of the whole result as well
def test_from_mono_audiosegments_copies_each_sample_once():
    assert peak_x("AudioSegment.from_mono_audiosegments(*mono)", MONO) < 2.7


# 6.5 before and 3.0 after: the audio and a result twice as wide
def test_set_sample_width_24_bit_makes_the_result_once():
    assert peak_x("seg.set_sample_width(3)") < 4.0


# 2.7 before and 1.7 after: the bytes that come in and the result, the rest was in copies
def test_24_bit_data_is_widened_once():
    assert peak_x("AudioSegment(raw, sample_width=3, frame_rate=44100, channels=2)", RAW24) < 2.2


# 3.4 before and 2.6 after with twice the PCM so the gap is well over the noise: the audio, the pieces and the result
def test_speedup_lets_go_of_each_chunk_once_it_is_trimmed():
    code = "from pydub2.effects import speedup\nspeedup(seg, 1.5)"
    assert peak_x(code, STEREO % ("bytes(range(256)) * %d" % (2 * PCM // 256)), 2 * PCM) < 3.0


# 4.0 before and 3.0 after: the audio, the two channels and the result, there were two more copies of the whole audio
def test_remove_dc_offset_makes_no_stereo_copy_per_channel():
    assert peak_x("seg.remove_dc_offset()") < 3.5
