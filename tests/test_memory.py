import os
import subprocess
import sys
import sysconfig

import pytest

from pydub2 import _ffmpeg, _mp3
from test_untrusted import WIN_PEAK, peak_mb

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


# The free-threaded build always uses mimalloc, which commits memory on Windows in pieces of 32 MiB, so a 24 MB
# buffer counts 1.4 times there: fade_in went to 2.80, set_sample_width(3) to 4.20. tracemalloc shows the same live
# memory as the regular build (2.00 and 3.00), so there the peak working set is measured, as on Linux and macOS.
WORKING_SET = sys.platform == "win32" and bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


class Peak(float):
    """The peak working set, with the peak commit next to it when an assertion fails."""
    def __repr__(self):
        return "%.3f (commit %.3f)" % (self, self.commit)


def windows_peaks(code):
    code = "import _pydub2\n" + code + "\n" + WIN_PEAK.replace("print(c.h / 1e6)", "print(c.a / 1e6, c.h / 1e6)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    assert out.returncode == 0, out.stderr
    return [float(v) for v in out.stdout.split()]


def peak_x(code, setup=STEREO % SAW, pcm=PCM):
    """Peak of a fresh process over a bare import of pydub2, as a multiple of the PCM, the audio included."""
    if not WORKING_SET:
        return (peak_mb("import pydub2\n" + setup + code) - peak_mb("import pydub2")) * 1e6 / pcm
    (ws, commit), (ws0, commit0) = windows_peaks("import pydub2\n" + setup + code), windows_peaks("import pydub2")
    peak = Peak((ws - ws0) * 1e6 / pcm)
    peak.commit = (commit - commit0) * 1e6 / pcm
    return peak


def live_x(code, setup=STEREO % SAW):
    """Peak of the memory python hands out while the setup and the call run, as a multiple of the PCM.

    This is what tracemalloc sees, so it does not depend on whether the allocator gives pages back. Bytes made in C++
    through the C API count, memory the extension takes with malloc or std::vector does not."""
    body = "import tracemalloc\ntracemalloc.start()\n" + setup + code + "\nprint(tracemalloc.get_traced_memory()[1])"
    out = subprocess.run([sys.executable, "-c", "import pydub2\n" + body], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    assert out.returncode == 0, out.stderr
    return float(out.stdout) / PCM


def mapped_x(code, setup=STEREO % SAW):
    """Peak of the address space of a fresh process over a bare import of pydub2, as a multiple of the PCM.

    Linux only. Like the commit Windows counts, memory taken and not yet written is in it."""
    def peak(code):
        code += "\nprint(int([l for l in open('/proc/self/status') if l.startswith('VmPeak')][0].split()[1]) / 1e3)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
        assert out.returncode == 0, out.stderr
        return float(out.stdout)
    return (peak("import pydub2\n" + setup + code) - peak("import pydub2")) * 1e6 / PCM


def export(format):
    return "with tempfile.TemporaryDirectory() as d:\n    seg.export(d + '/out', format=%r)" % format


# measured with 10 minutes on Linux, before and after: ogg 3.2 and 1.2, opus 3.1 and 1.2, mp3 3.4 and 1.2, 1.5 at this size
@pytest.mark.parametrize("format", ["ogg", "opus", "mp3"])
def test_export_holds_the_audio_once(format):
    if format == "mp3" and not _mp3.available():
        pytest.skip("pydub2-mp3 not installed")
    assert peak_x(export(format)) < 2.0


# ffmpeg gets the audio as one wav, so it is held twice: 2.03 to 2.06 on Linux, 2.07 to 2.10 on macOS and Windows
@pytest.mark.skipif(_ffmpeg.find("ffmpeg") is None, reason="no ffmpeg")
def test_export_mp3_through_ffmpeg_holds_the_audio_twice():
    setup = STEREO % SAW + "from pydub2 import _mp3\n_mp3.available = lambda: False\n"
    assert peak_x(export("mp3"), setup) < 2.5


# 3.0 before and 2.0 after with noise, which flac cannot shrink: the audio and the file, written a block at a time.
# Windows counts commit, not resident pages, so it saw 3.0 while the whole file was made next to the blocks
def test_export_flac_does_not_copy_the_file():
    assert peak_x(export("flac"), STEREO % NOISE) < 2.5


# 2.9 before and 1.9 after: the blocks and the whole file were side by side, so Windows saw 3.0 where Linux saw 2.0
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads VmPeak")
def test_export_flac_to_a_path_never_holds_the_file_twice():
    assert mapped_x(export("flac"), STEREO % NOISE) < 2.5


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


# 3.8 before and 2.0 after: a bytes object per millisecond was held next to the audio and then joined
def test_a_fade_over_the_whole_file_makes_the_result_once():
    assert peak_x("seg.fade(to_gain=-20, start=0, duration=len(seg))") < 2.5


# 3.9 before and 2.9 after, the audio and a result twice its size: the parts went through a temporary file and were read back
def test_append_with_a_crossfade_holds_the_audio_and_the_result():
    assert peak_x("seg.append(seg, 100)") < 3.3


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


# 3.1 before and 1.7 after: the audio and the result, the chunks used to be copied and held as well
def test_speedup_copies_only_what_it_keeps():
    assert live_x("from pydub2.effects import speedup\nspeedup(seg, 1.5)") < 2.3


# 4.0 before and 2.0 after: the audio and the result, the channels were copied out and back
def test_remove_dc_offset_makes_no_copy_per_channel():
    assert live_x("seg.remove_dc_offset()") < 2.5
