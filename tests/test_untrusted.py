import io
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

import pytest

import _pydub2
import pydub2
from pydub2 import AudioSegment, _ffmpeg, _mp3, playback, utils
from pydub2.exceptions import CouldntDecodeError

ffmpeg = shutil.which("ffmpeg")
need_ffmpeg = pytest.mark.skipif(ffmpeg is None or shutil.which("ffprobe") is None, reason="no ffmpeg")
posix = pytest.mark.skipif(sys.platform == "win32", reason="posix only")
pydub = pytest.importorskip("pydub")

SCRIPT = b"ffconcat version 1.0\nfile secret.wav\n"


def tone(path, secs=1.0, *args):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=%s" % secs,
                    *args, "-ar", "16000", str(path)], check=True)


@pytest.fixture
def secret(tmp_path, monkeypatch):
    AudioSegment.silent(1000, 8000).export(str(tmp_path / "secret.wav"), "wav")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pydub2, "max_duration", None)
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", None)
    return tmp_path


WIN_PEAK = "import ctypes\nfrom ctypes import wintypes as w\n" \
    "class C(ctypes.Structure):\n _fields_ = [('cb', w.DWORD), ('f', w.DWORD)] + [(n, ctypes.c_size_t) for n in 'abcdefgh']\n" \
    "c = C(cb=ctypes.sizeof(C))\nk = ctypes.WinDLL('kernel32')\nk.GetCurrentProcess.restype = w.HANDLE\n" \
    "p = ctypes.WinDLL('psapi')\np.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]\n" \
    "p.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)\nprint(c.h / 1e6)"
# linux keeps the high water mark of the parent over exec, so ru_maxrss would be the pytest process
UNIX_PEAK = "import sys\nif sys.platform == 'linux':\n print(int([l for l in open('/proc/self/status') if l.startswith('VmHWM')][0].split()[1]) / 1e3)\n" \
    "else:\n import resource\n print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6)"


def peak_mb(code):
    code = "import _pydub2\n" + code + "\n" + (WIN_PEAK if sys.platform == "win32" else UNIX_PEAK)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return float(out.stdout)


def grows_mb(code):
    """Peak growth over a bare subprocess, so the bound does not depend on the platform's baseline."""
    return peak_mb(code) - peak_mb("pass")


@need_ffmpeg
@pytest.mark.parametrize("name", ["upload.m4a", "upload.wav", "upload.bin"])
def test_concat_script_as_path(secret, name):
    (secret / name).write_bytes(SCRIPT)
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(str(secret / name))
    with pytest.raises(CouldntDecodeError):
        utils.mediainfo_json(str(secret / name))
    with pytest.raises(CouldntDecodeError):
        utils.mediainfo(str(secret / name))


@need_ffmpeg
def test_concat_script_as_file_object(secret):
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(io.BytesIO(SCRIPT))
    assert not utils.mediainfo_json(io.BytesIO(SCRIPT)).get("streams")
    assert not utils.mediainfo(io.BytesIO(SCRIPT)).get("codec_name")


@need_ffmpeg
@pytest.mark.parametrize("head", [b"#EXTM3U\n", b"\xef\xbb\xbf#EXTM3U\n", b"  <MPD", b"<?xml version='1.0'?>"])
def test_playlists_as_path(secret, head):
    (secret / "x.m4a").write_bytes(head + b"secret.wav\n")
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(str(secret / "x.m4a"))


@need_ffmpeg
@pytest.mark.parametrize("before", [b"<!-- x -->\n", b" " * 64, b"\n" * 200])
def test_dash_manifest_behind_a_comment(secret, before):
    tone(secret / "other.m4a", 1.0, "-c:a", "aac")
    mpd = before + (b'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
                    b'type="static" mediaPresentationDuration="PT1S"><BaseURL>file://' + str(secret).encode() +
                    b'/</BaseURL><Period><AdaptationSet mimeType="audio/mp4"><Representation id="a" bandwidth="1">'
                    b'<BaseURL>other.m4a</BaseURL><SegmentBase indexRange="0-0"/></Representation></AdaptationSet>'
                    b'</Period></MPD>')
    (secret / "x.m4a").write_bytes(mpd)
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(str(secret / "x.m4a"))
    assert not utils.mediainfo_json(str(secret / "x.m4a")).get("streams")


@need_ffmpeg
def test_commands_name_the_demuxers():
    names = utils.allowed_demuxers(ffmpeg).split(",")
    assert {"wav", "mp3", "flac", "ogg", "matroska", "mov", "mp4"} <= set(names)
    assert not {"hls", "dash", "concat", "imf"} & set(",".join(names).split(","))
    cmd = utils.source(__file__, exe=ffmpeg)[0]
    assert cmd[cmd.index("-format_whitelist") + 1] == ",".join(names)


def test_demuxer_list_skips_scripts_and_survives_a_timeout(monkeypatch):
    table = "Demuxers:\n D. = Demuxing supported\n --\n D  avisynth  AviSynth\n D  hls,applehttp  HLS\n" \
        " D  vapoursynth  VapourSynth\n D  wav  WAV\n"
    calls = []

    def fake(cmd, data=None, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            raise TimeoutError("stop")
        return subprocess.CompletedProcess(cmd, 0, table.encode(), b"")

    monkeypatch.setattr(utils, "run", fake)
    with pytest.raises(TimeoutError):
        utils.allowed_demuxers("fake-ffmpeg")
    assert utils.allowed_demuxers("fake-ffmpeg") == "wav"


def test_demuxer_list_skips_devices_and_an_empty_list_raises_and_is_not_kept(monkeypatch):
    table = "Formats:\n ..d = Is a device\n ---\n D   wav  WAV\n D d lavfi  Lavfi\n D d avfoundation  AVF\n D   aac  AAC\n"
    out = [b"", table.encode()]

    monkeypatch.setattr(utils, "run", lambda cmd, data=None, **kw: subprocess.CompletedProcess(cmd, 0, out.pop(0), b""))
    with pytest.raises(CouldntDecodeError, match="3.3"):
        utils.allowed_demuxers("old-ffmpeg")
    assert utils.allowed_demuxers("old-ffmpeg") == "wav,aac"


def test_commands_restrict_protocols(monkeypatch, tmp_path):
    seen = []

    def fake(cmd, data=None, **kw):
        seen.append(cmd)
        raise TimeoutError("stop")

    monkeypatch.setattr(_ffmpeg, "run_command", fake)
    monkeypatch.setattr(_ffmpeg, "mediainfo_json", lambda f: {})
    monkeypatch.setattr(_ffmpeg, "find", lambda c: "ffmpeg")
    (tmp_path / "a.m4a").write_bytes(b"\0" * 100)
    for kw in ({"data": b"\0" * 100}, {"path": str(tmp_path / "a.m4a")}):
        with pytest.raises(CouldntDecodeError):
            _ffmpeg.to_wav("ffmpeg", **kw)
    cmd = seen[0]
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "cache,pipe"
    cmd = seen[1]
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"
    assert cmd[cmd.index("-i") + 1] == "file:" + str(tmp_path / "a.m4a")


@need_ffmpeg
@pytest.mark.parametrize("name, args", [
    ("a.m4a", ["-c:a", "aac"]),
    ("a.webm", ["-c:a", "libopus"]),
    ("a.wma", ["-c:a", "wmav2"]),
    ("a.aiff", []),
    ("a.mp4", ["-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10", "-c:a", "aac", "-shortest"]),
])
def test_real_formats_unchanged(tmp_path, name, args):
    path = tmp_path / name
    try:
        tone(path, 1.0, *args)
    except subprocess.CalledProcessError:
        pytest.skip("this ffmpeg cant write " + name)
    want = pydub.AudioSegment.from_file(str(path))
    got = AudioSegment.from_file(str(path))
    assert (got.raw_data, got.frame_rate, got.channels) == (want.raw_data, want.frame_rate, want.channels)
    got = AudioSegment.from_file(io.BytesIO(path.read_bytes()))
    assert got.raw_data == want.raw_data
    assert utils.mediainfo(str(path))["codec_type"] == "audio"


@need_ffmpeg
@posix
def test_name_that_looks_like_a_protocol(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tone("plain.m4a", 0.5, "-c:a", "aac")
    shutil.copy("plain.m4a", "concat:nothere.m4a")
    assert AudioSegment.from_file("concat:nothere.m4a").raw_data == AudioSegment.from_file("plain.m4a").raw_data


def listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    s.setblocking(False)
    return s


def connected(s):
    time.sleep(0.3)
    try:
        s.accept()
        return True
    except BlockingIOError:
        return False


@need_ffmpeg
def test_mediainfo_does_not_fetch_urls():
    s = listener()
    utils.mediainfo("http://127.0.0.1:%d/x.wav" % s.getsockname()[1])
    assert not connected(s)


@need_ffmpeg
def test_cover_is_not_fetched():
    s = listener()
    try:
        AudioSegment.silent(500, 8000).export(io.BytesIO(), "mp3", cover="http://127.0.0.1:%d/c.png" % s.getsockname()[1])
    except Exception:
        pass
    assert not connected(s)


def test_detect_silence_memory():
    assert grows_mb("_pydub2.detect_silence(bytes(50000), 1, 1, 1, 1000, -40, 1)") < 20


def test_detect_silence_too_long():
    with pytest.raises(ValueError):
        _pydub2.detect_silence(bytes(3000000), 1, 1, 1, 1000, -40, 1)


def test_detect_silence_huge_step():
    from pydub.silence import detect_silence
    data = bytes(2000)
    seg = pydub.AudioSegment(data, sample_width=2, frame_rate=1000, channels=1)
    assert _pydub2.detect_silence(data, 1000, 1, 2, 100, -40, 2 ** 31 - 1) == detect_silence(seg, 100, -40, 2 ** 31 - 1)


def test_ms_limit_and_timeout_are_off_by_default():
    assert pydub2.max_duration is None
    assert pydub2.ffmpeg_timeout is None


def test_check_duration(monkeypatch):
    utils.check_duration(10 ** 9, 1)
    monkeypatch.setattr(pydub2, "max_duration", 2)
    utils.check_duration(2 * 8000, 8000)
    with pytest.raises(CouldntDecodeError):
        utils.check_duration(2 * 8000 + 1, 8000)


@need_ffmpeg
def test_max_duration_on_the_ffmpeg_path(secret, monkeypatch):
    tone(secret / "long.m4a", 3.0, "-c:a", "aac")
    assert len(AudioSegment.from_file(str(secret / "long.m4a"))) > 2900
    monkeypatch.setattr(pydub2, "max_duration", 1)
    for src in (str(secret / "long.m4a"), io.BytesIO((secret / "long.m4a").read_bytes())):
        with pytest.raises(CouldntDecodeError):
            AudioSegment.from_file(src)
    monkeypatch.setattr(pydub2, "max_duration", 10)
    assert len(AudioSegment.from_file(str(secret / "long.m4a"))) > 2900


@need_ffmpeg
@pytest.mark.parametrize("name", ["long.wav", "long.flac", "long.mp3", "long.ogg"])
def test_max_duration_on_the_native_path(tmp_path, monkeypatch, name):
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=1800:r=8000:a=0.1", "-ac", "1",
                    *{"long.ogg": ["-c:a", "libvorbis"]}.get(name, []), str(tmp_path / name)], check=True)
    import tracemalloc
    peaks = []
    for limit in (None, 10):
        monkeypatch.setattr(pydub2, "max_duration", limit)
        tracemalloc.start()
        try:
            AudioSegment.from_file(str(tmp_path / name))
        except CouldntDecodeError:
            pass
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
    assert peaks[1] < 2 << 20 < 20 << 20 < peaks[0]
    monkeypatch.setattr(pydub2, "max_duration", 10)
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(str(tmp_path / name))
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(str(tmp_path / name), start_second=1)
    assert len(AudioSegment.from_file(str(tmp_path / name), start_second=1, duration=9)) == 9000
    chunks = AudioSegment.stream(str(tmp_path / name), chunk_ms=3000)
    assert [len(next(chunks)) for _ in range(3)] == [3000] * 3
    with pytest.raises(CouldntDecodeError):
        next(chunks)
    monkeypatch.setattr(pydub2, "max_duration", 1800)
    assert len(AudioSegment.from_file(str(tmp_path / name))) == 1800000


@need_ffmpeg
def test_max_duration_in_mediainfo_without_ffprobe(tmp_path, monkeypatch):
    path = tmp_path / "long.flac"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "600", str(path)], check=True)
    data = bytearray(path.read_bytes())
    data[21] &= 0xf0
    data[22:26] = bytes(4)
    path.write_bytes(data)
    monkeypatch.setattr(utils, "_has_prober", lambda: False)
    assert float(utils.mediainfo_json(str(path))["streams"][0]["duration"]) == 600
    monkeypatch.setattr(pydub2, "max_duration", 10)
    with pytest.raises(CouldntDecodeError):
        utils.mediainfo_json(str(path))


@posix
def test_ffmpeg_timeout(tmp_path, monkeypatch):
    slow = tmp_path / "ffmpeg"
    slow.write_text("#!/bin/sh\nsleep 20\n")
    slow.chmod(0o755)
    monkeypatch.setattr(AudioSegment, "converter", str(slow))
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", 0.5)
    monkeypatch.setattr(utils, "_has_prober", lambda: False)
    start = time.time()
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_file(io.BytesIO(b"not audio at all" * 10), format="m4a")
    assert time.time() - start < 10
    with pytest.raises(CouldntEncodeError_()):
        AudioSegment.silent(100, 8000).export(io.BytesIO(), "m4a")


def CouldntEncodeError_():
    from pydub2.exceptions import CouldntEncodeError
    return CouldntEncodeError


@posix
def test_current_directory_is_not_searched(tmp_path, monkeypatch):
    for name in ("ffmpeg", "ffprobe", "avconv", "avprobe", "ffplay"):
        (tmp_path / name).write_text("#!/bin/sh\n")
        (tmp_path / name).chmod(0o755)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(AudioSegment, "converter", "ffmpeg")
    monkeypatch.setenv("PATH", str(empty) + os.pathsep)
    assert utils.which("ffmpeg") is None
    assert _ffmpeg.find("ffmpeg") is None
    assert not utils._has_prober()
    monkeypatch.delenv("PATH")
    found = utils.which("ffmpeg")
    assert found is None or os.path.isabs(found)


def test_nothing_is_started_from_a_name_that_was_not_found(tmp_path, monkeypatch):
    for name in ("ffmpeg", "ffprobe", "ffplay", "avconv", "avprobe", "avplay"):
        for ext in ("", ".exe"):
            (tmp_path / (name + ext)).write_bytes(b"MZ")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(AudioSegment, "converter", "ffmpeg")
    started = []
    for name in ("run", "call", "Popen"):
        monkeypatch.setattr(subprocess, name, lambda *a, **k: started.append(a))
    utils._codecs.cache_clear()
    with pytest.warns(RuntimeWarning):
        assert utils.get_supported_codecs() == (set(), set())
    with pytest.warns(RuntimeWarning), pytest.raises(FileNotFoundError):
        playback._play_with_ffplay(AudioSegment.silent(100))
    with pytest.raises(FileNotFoundError):
        _ffmpeg.run("ffmpeg", [], None, "this file")
    assert not utils._has_prober()
    assert started == []
    utils._codecs.cache_clear()


@posix
def test_explicit_converter_and_found_path(tmp_path, monkeypatch):
    for name in ("ffmpeg", "avconv", "ffprobe"):
        (tmp_path / name).write_text("#!/bin/sh\n")
        (tmp_path / name).chmod(0o755)
    assert _ffmpeg.find(str(tmp_path / "ffmpeg")) == str(tmp_path / "ffmpeg")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert utils.get_encoder_name() == str(tmp_path / "ffmpeg")
    assert utils.get_prober_name() == str(tmp_path / "ffprobe")


def clip(n=300, rate=8000):
    data = bytes((i * 7) % 256 for i in range(n * rate // 1000 * 2))
    return AudioSegment(data, sample_width=2, frame_rate=rate, channels=1)


@pytest.mark.parametrize("kw", [
    {"from_gain": -120, "duration": 2000, "start": 0},
    {"to_gain": -120, "duration": 5000, "end": float("inf")},
    {"to_gain": -6, "start": 50, "duration": 3000},
    {"to_gain": -6, "from_gain": 3, "end": 200, "duration": 4000},
    {"to_gain": 6, "start": 100, "end": 150},
])
def test_fade_matches_pydub(kw):
    ours = clip()
    theirs = pydub.AudioSegment(ours.raw_data, sample_width=2, frame_rate=8000, channels=1)
    assert ours.fade(**kw).raw_data == theirs.fade(**kw).raw_data


def test_fade_with_a_huge_duration_is_quick():
    start = time.time()
    clip(100).fade_in(3 * 10 ** 6)
    assert time.time() - start < 2


@pytest.mark.parametrize("kw", [
    {"loop": True},
    {"times": 1},
    {"times": 2},
    {"times": 5},
    {"times": 1000},
    {"times": 3.0},
    {"times": 1.5},
    {"times": -2},
    {"loop": True, "position": 30},
    {"loop": True, "gain_during_overlay": -6},
    {"times": 3, "position": 250, "gain_during_overlay": -3},
    {"times": 0},
])
@pytest.mark.parametrize("ms", [1, 7, 120, 400])
def test_overlay_matches_pydub(kw, ms):
    base = clip(300)
    over = clip(ms)
    tb = pydub.AudioSegment(base.raw_data, sample_width=2, frame_rate=8000, channels=1)
    to = pydub.AudioSegment(over.raw_data, sample_width=2, frame_rate=8000, channels=1)
    assert base.overlay(over, **kw).raw_data == tb.overlay(to, **kw).raw_data


def test_overlay_with_an_empty_clip():
    base = clip(300)
    assert base.overlay(base[:0], loop=True).raw_data == base.raw_data


def test_looping_a_tiny_clip_is_quick():
    base = AudioSegment.silent(300000, 44100)
    tiny = AudioSegment(b"\1\0", sample_width=2, frame_rate=44100, channels=1)
    start = time.time()
    out = base.overlay(tiny, loop=True)
    assert time.time() - start < 1.5
    assert out.raw_data[:6] == b"\1\0\1\0\1\0"


@pytest.mark.parametrize("channels", [0, 65536, 2 ** 30])
def test_channel_count_is_capped(channels):
    with pytest.raises(ValueError):
        _pydub2.detect_silence(b"", 1, channels, 4, 0, -16, 1)
    with pytest.raises(ValueError):
        _pydub2.loudness(b"", 48000, channels, 2)
    with pytest.raises(ValueError):
        _pydub2.encode(b"", 48000, channels, 2, "wav")


def test_ratecv_channel_count():
    with pytest.raises(ValueError):
        _pydub2.ratecv(b"", 2, 70000, 8000, 16000)


def test_wav_byte_rate_overflow():
    with pytest.raises(RuntimeError):
        _pydub2.encode(b"", 2 ** 31 - 1, 65535, 4, "wav")


def test_flac_many_channels_does_not_allocate():
    with pytest.raises(RuntimeError):
        _pydub2.encode(b"", 44100, 65535, 2, "flac")
    assert grows_mb("try:\n _pydub2.encode(b'', 44100, 65535, 2, 'flac')\nexcept RuntimeError: pass") < 20


def test_resample_checks_the_ratio_first():
    with pytest.raises(RuntimeError):
        _pydub2.resample(bytes(200000), 8000, 1, 2, 8000 * 1000)
    assert grows_mb("try:\n _pydub2.resample(bytes(200000), 8000, 1, 2, 8000 * 1000)\nexcept RuntimeError: pass") < 20


REGRESSIONS = os.path.join(os.path.dirname(__file__), "fuzz", "regressions")


def test_vorbis_last_granule_near_the_int64_limits():
    # the skip worked out from a last granule of -2**63 + 1 overflowed, the sanitizer build of tests/fuzz reports it
    path = os.path.join(REGRESSIONS, "vorbis_granule.ogg")
    d = _pydub2.Decoder(path)
    assert d.frames == 1024
    first = d.read()
    d.seek(0)
    assert d.read() == first


def test_float_wav_frames_follow_the_sample_size():
    # 256 channels of 32 bit float with a block align of 1 counted a frame per byte, 1024 times the audio there is
    fmt = struct.pack("<HHIIHH", 3, 256, 8000, 8000, 1, 32)
    data = bytes(1024 * 64)
    wav = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<I", 16) + fmt + \
        b"data" + struct.pack("<I", len(data)) + data
    d = _pydub2.Decoder(wav)
    assert d.frames == 64
    assert len(d.read()) == len(data)


def test_opus_at_a_huge_rate_is_refused_quickly():
    # the resampler of libopusenc works in the square of the rate, an ogg header of 1 GHz took 150 s a channel
    start = time.time()
    with pytest.raises(RuntimeError):
        _pydub2.encode(bytes(800), 3 * 10 ** 8, 1, 2, "opus")
    assert time.time() - start < 2


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_mul_with_a_factor_that_is_not_finite(bad):
    for width in (1, 2, 3, 4):
        with pytest.raises(ValueError):
            _pydub2.mul(bytes(8 * width), width, bad)
    assert _pydub2.mul(b"", 2, bad) == b""


def test_mul_finite_overflow_is_clamped():
    assert _pydub2.mul(b"\0\x40\0\xc0", 2, 1e308) == struct.pack("<hh", 32767, -32768)


def test_tomono_cancelling_infinities():
    with pytest.raises(ValueError):
        _pydub2.tomono(struct.pack("<ii", 2 ** 31 - 1, 2 ** 31 - 1), 4, 1e308, -1e308)


def test_mp3_odd_parameters_fall_back():
    for p in (["-q:a", "nan"], ["-q:a", "10"], ["-q:a", "-1"], ["-ar", ""]):
        assert _mp3.encode(bytes(400), 8000, 1, 2, parameters=p) is None


def test_bits_from_a_hostile_file(monkeypatch):
    seen = []

    def fake(cmd, data=None):
        seen.append(cmd)
        raise TimeoutError("stop")

    monkeypatch.setattr(_ffmpeg, "run_command", fake)
    monkeypatch.setattr(_ffmpeg, "find", lambda c: "ffmpeg")
    for bits in (64, 12, 0, None):
        info = {"streams": [{"codec_type": "audio", "bits_per_sample": bits}]}
        monkeypatch.setattr(_ffmpeg, "mediainfo_json", lambda f, info=info: info)
        with pytest.raises(CouldntDecodeError):
            _ffmpeg.to_wav("ffmpeg", data=b"x")
        assert "pcm_s16le" in seen[-1]


def test_title_cannot_pose_as_a_stream():
    assert utils.get_extra_info("    title : x Stream #0:0: Audio: x, s64\n") == {}
    assert 0 in utils.get_extra_info("    Stream #0:0: Audio: flac, 88200 Hz, stereo, s32 (24 bit)\n")


def test_odd_ffprobe_and_ffmpeg_output(monkeypatch):
    class Done:
        returncode = 0
        stderr = b""
        stdout = b"RIFF\xff\xff\xff\xffWAVEfmt "

    monkeypatch.setattr(_ffmpeg, "find", lambda c: "ffmpeg")
    monkeypatch.setattr(_ffmpeg, "run_command", lambda cmd, data=None: Done)
    for info in ({"streams": [{"index": 0}]}, {"streams": [{"codec_type": "audio"}]}, {}):
        monkeypatch.setattr(_ffmpeg, "mediainfo_json", lambda f, info=info: info)
        with pytest.raises(CouldntDecodeError):
            _ffmpeg.to_wav("ffmpeg", data=b"x")

    def dies(f):
        return json.loads("")

    monkeypatch.setattr(_ffmpeg, "mediainfo_json", dies)
    with pytest.raises(CouldntDecodeError):
        _ffmpeg.to_wav("ffmpeg", data=b"x")


def test_fade_past_the_end_at_a_low_rate_raises_like_pydub():
    data = bytes(100)
    ours = AudioSegment(data, sample_width=2, frame_rate=100, channels=1)
    theirs = pydub.AudioSegment(data, sample_width=2, frame_rate=100, channels=1)
    with pytest.raises(pydub.exceptions.TooManyMissingFrames):
        theirs.fade_in(2000)
    with pytest.raises(pydub2.exceptions.TooManyMissingFrames):
        ours.fade_in(2000)


def test_mp3_frame_at_the_end_of_the_scan_buffer(tmp_path):
    hd = bytes.fromhex("fffb0000")
    data = b"ID3\x03" + bytes(65523 - 4) + hd * 3 + b"\0" + hd * 8
    data += bytes(65632 - len(data))
    (tmp_path / "x.mp3").write_bytes(data)
    for src in (str(tmp_path / "x.mp3"), io.BytesIO(data)):
        try:
            AudioSegment.from_file(src, format="mp3")
        except CouldntDecodeError:
            pass


BEAT = "import sys, time\nf = open(sys.argv[1], 'a')\nwhile True:\n    f.write('x')\n    f.flush()\n    time.sleep(0.05)\n"


def assert_stopped(beat):
    assert beat.stat().st_size > 0
    size = beat.stat().st_size
    time.sleep(0.5)
    assert beat.stat().st_size == size


def test_ffmpeg_is_killed_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", 3)
    with pytest.raises(TimeoutError):
        utils.run([sys.executable, "-c", BEAT, str(tmp_path / "beat")])
    assert_stopped(tmp_path / "beat")


@pytest.mark.skipif(sys.platform == "win32" and sys.version_info < (3, 13), reason="a thread join cant be interrupted")
def test_ffmpeg_is_killed_on_interrupt(tmp_path, monkeypatch):
    import _thread
    import signal
    import threading
    if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        pytest.skip("SIGINT is ignored, pytest was started in the background")
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", 60)
    timer = threading.Timer(3, _thread.interrupt_main if os.name == "nt" else lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            utils.run([sys.executable, "-c", BEAT, str(tmp_path / "beat")])
    finally:
        timer.cancel()
    assert_stopped(tmp_path / "beat")


def test_a_tag_too_long_for_the_command_line(tmp_path):
    with pytest.raises(CouldntEncodeError_(), match="tags"):
        _ffmpeg.from_wav(sys.executable, b"RIFF", "mp3", tags={"comment": "x" * 3000000})
    with pytest.raises(FileNotFoundError):
        _ffmpeg.from_wav(str(tmp_path / "nope"), b"RIFF", "mp3")


def test_a_held_temp_file_does_not_fail_the_export(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def fake(converter, args, data, what):
        with open(args[-1], "wb") as f:
            f.write(b"encoded")
        # what a virus scanner holding the file does to the cleanup
        for name in ("unlink", "rmdir"):
            monkeypatch.setattr(os, name, lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(_ffmpeg, "run", fake)
    assert _ffmpeg.from_wav("ffmpeg", b"RIFF", "mp3") == b"encoded"
    monkeypatch.undo()


LOAD = "import pydub2\nfrom pydub2.exceptions import CouldntDecodeError\n{}\n" \
    "try:\n pydub2.AudioSegment.from_file({!r}{})\nexcept CouldntDecodeError as e:\n assert e.reason in ('too_big', 'too_long'), e\n" \
    "else:\n raise SystemExit('loaded')"


def big_wav(path, mb=64):
    # 8 channels of 192 kHz 32 bit, the data is a hole in the file so writing it is free
    n = mb << 20
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 8, 192000, 192000 * 32, 32, 32)
                + b"data" + struct.pack("<I", n))
        f.truncate(44 + n)


@need_ffmpeg
@posix
@pytest.mark.parametrize("name,args", [
    ("bomb.flac", ["-f", "lavfi", "-i", "anullsrc=r=192000:cl=7.1", "-t", "60", "-sample_fmt", "s32"]),
    ("bomb.ogg", ["-f", "lavfi", "-i", "anullsrc=r=192000:cl=7.1", "-t", "60", "-c:a", "libvorbis"]),
    ("bomb.opus", ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=7.1", "-t", "60", "-c:a", "libopus"]),
    ("bomb.mp3", ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "300", "-b:a", "32k"]),
    ("bomb.wav", None),
])
def test_max_bytes_stops_a_small_file_that_decodes_to_a_lot(tmp_path, name, args):
    path = str(tmp_path / name)
    if args:
        subprocess.run([ffmpeg, "-v", "error", *args, path], check=True)
    else:
        big_wav(path)
    base = peak_mb("import pydub2")
    # what the header asks for is 57 to 368 MB, a megabyte is allowed
    assert peak_mb(LOAD.format("pydub2.max_duration = 3600\npydub2.max_bytes = 1 << 20", path, "")) < base + 10


@posix
def test_strict_checks_the_wav_header_before_reading(tmp_path):
    path = str(tmp_path / "big.wav")
    big_wav(path)
    base = peak_mb("import pydub2")
    for limit in ("max_duration = 10", "max_bytes = 1 << 20"):
        assert peak_mb(LOAD.format("pydub2.strict = True\npydub2." + limit, path, "")) < base + 10


@posix
def test_raw_respects_both_limits(tmp_path, monkeypatch):
    path = tmp_path / "big.raw"
    with open(path, "wb") as f:
        f.truncate(64 << 20)
    kw = ", format='raw', sample_width=2, frame_rate=44100, channels=2"
    base = peak_mb("import pydub2")
    for limit in ("max_duration = 10", "max_bytes = 1 << 20"):
        assert peak_mb(LOAD.format("pydub2." + limit, str(path), kw)) < base + 10
    monkeypatch.setattr(pydub2, "max_duration", 10)
    with pytest.raises(CouldntDecodeError):
        AudioSegment.from_raw(str(path), sample_width=2, frame_rate=44100, channels=2)
    monkeypatch.setattr(pydub2, "max_duration", None)
    monkeypatch.setattr(pydub2, "max_bytes", 64 << 20)
    assert len(AudioSegment.from_raw(str(path), sample_width=2, frame_rate=44100, channels=2).raw_data) == 64 << 20
    monkeypatch.setattr(pydub2, "max_bytes", (64 << 20) - 1)
    with pytest.raises(CouldntDecodeError, match="^this file is larger than max_bytes$"):
        AudioSegment.from_file(str(path), format="pcm", sample_width=2, frame_rate=44100, channels=2)


@need_ffmpeg
def test_max_bytes_on_the_ffmpeg_path_and_in_stream(secret, monkeypatch):
    tone(secret / "long.m4a", 3.0, "-c:a", "aac")
    tone(secret / "long.wav", 3.0)
    monkeypatch.setattr(pydub2, "max_bytes", 16000)
    for src in (str(secret / "long.m4a"), io.BytesIO((secret / "long.m4a").read_bytes())):
        with pytest.raises(CouldntDecodeError, match="^this file is larger than max_bytes$"):
            AudioSegment.from_file(src)
    chunks = AudioSegment.stream(str(secret / "long.wav"), chunk_ms=200)
    with pytest.raises(CouldntDecodeError):
        list(chunks)
    monkeypatch.setattr(pydub2, "max_bytes", 200000)
    assert len(AudioSegment.from_file(str(secret / "long.m4a"))) > 2900
    assert sum(len(c) for c in AudioSegment.stream(str(secret / "long.wav"))) == 3000


@need_ffmpeg
def test_infinite_limits_are_no_limits(secret, monkeypatch):
    tone(secret / "a.m4a", 1.0, "-c:a", "aac")
    tone(secret / "a.mp3", 1.0)
    want = [len(AudioSegment.from_file(str(secret / n))) for n in ("a.m4a", "a.mp3")]
    for name in ("max_duration", "max_bytes"):
        monkeypatch.setattr(pydub2, name, float("inf"))
        assert [len(AudioSegment.from_file(str(secret / n))) for n in ("a.m4a", "a.mp3")] == want
        assert sum(len(c) for c in AudioSegment.stream(str(secret / "a.mp3"))) == want[1]


@need_ffmpeg
def test_no_limit_changes_nothing(secret, monkeypatch):
    assert pydub2.max_bytes is None
    tone(secret / "a.m4a", 1.0, "-c:a", "aac")
    seen = []
    real = _ffmpeg.run
    monkeypatch.setattr(_ffmpeg, "run", lambda conv, args, *a: seen.append(args) or real(conv, args, *a))
    seg = AudioSegment.from_file(str(secret / "a.m4a"))
    assert not {"-t", "-fs"} & set(seen[-1])
    monkeypatch.setattr(pydub2, "max_bytes", 1 << 30)
    assert AudioSegment.from_file(str(secret / "a.m4a")).raw_data == seg.raw_data


@posix
def test_stream_reads_no_more_than_the_limits(tmp_path):
    path = str(tmp_path / "big.wav")
    big_wav(path)
    base = peak_mb("import pydub2")
    code = "import pydub2\nfrom pydub2.exceptions import CouldntDecodeError\npydub2.max_bytes = 1 << 20\n" \
        "try:\n for c in pydub2.AudioSegment.stream({!r}, chunk_ms=60000): pass\nexcept CouldntDecodeError as e:\n" \
        " assert e.reason == 'too_big', e\nelse:\n raise SystemExit('loaded')".format(path)
    # one chunk of the default size would be 64 MB
    assert peak_mb(code) < base + 10


@posix
def test_strict_reads_no_more_of_a_wav_than_the_limits(tmp_path):
    late, empty = str(tmp_path / "late.wav"), str(tmp_path / "empty.wav")
    n = 64 << 20
    fmt = b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 8, 192000, 192000 * 32, 32, 32)
    with open(late, "wb") as f:
        # the data chunk starts after the first 64 KiB
        f.write(b"RIFF" + struct.pack("<I", 36 + 8 + 70000 + n) + fmt + b"LIST" + struct.pack("<I", 70000)
                + b"\0" * 70000 + b"data" + struct.pack("<I", n))
        f.truncate(44 + 8 + 70000 + n)
    with open(empty, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + n) + fmt + b"data" + struct.pack("<I", 0))
        f.truncate(44 + n)
    base = peak_mb("import pydub2")
    code = "import pydub2\npydub2.strict = True\npydub2.max_bytes = 1 << 20\n"
    assert peak_mb(code + "assert len(pydub2.AudioSegment.from_file({!r})) == 0".format(empty)) < base + 10
    if ffmpeg:
        assert peak_mb(LOAD.format("pydub2.strict = True\npydub2.max_bytes = 1 << 20", late, "")) < base + 10


def test_numbers_of_any_kind_cut_a_wav_without_ffmpeg(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    from fractions import Fraction
    path = str(tmp_path / "a.wav")
    AudioSegment.silent(3000, 8000).export(path, "wav")
    whole = AudioSegment.from_file(path)
    monkeypatch.setattr(AudioSegment, "converter", str(tmp_path / "no-ffmpeg"))
    for v in (np.float64(0.5), np.int64(1), np.float32(0.5), True, Fraction(1, 2)):
        assert AudioSegment.from_file(path, start_second=v).raw_data == whole[v * 1000:].raw_data
        assert AudioSegment.from_file(path, duration=v).raw_data == whole[:v * 1000].raw_data


def test_limits_are_read_once_per_load(monkeypatch):
    class Upload(io.BytesIO):
        def read(self, *a):
            # what another thread could do in the middle of a load
            pydub2.max_duration = None
            return super().read(*a)
    monkeypatch.setattr(pydub2, "max_duration", 1)
    with pytest.raises(CouldntDecodeError, match="^this file is longer than max_duration$"):
        AudioSegment.from_file(Upload(b"\0" * 8000 * 60), format="raw", sample_width=1, frame_rate=8000, channels=1)


def test_odd_limits(tmp_path, monkeypatch):
    path = str(tmp_path / "a.wav")
    AudioSegment.silent(1000, 8000).export(path, "wav")
    (tmp_path / "a.raw").write_bytes(b"\0" * 16000)
    raw = dict(file=str(tmp_path / "a.raw"), format="raw", sample_width=2, frame_rate=8000, channels=1)
    for name in ("max_duration", "max_bytes"):
        for v in (-1, float("nan"), "10"):
            monkeypatch.setattr(pydub2, name, v)
            for load in (lambda: AudioSegment.from_file(path), lambda: AudioSegment.from_file(**raw),
                         lambda: list(AudioSegment.stream(path))):
                with pytest.raises(ValueError, match="^pydub2.%s has to be None or a number" % name):
                    load()
        for v in (1e14, 2 ** 70, float("inf")):
            monkeypatch.setattr(pydub2, name, v)
            assert len(AudioSegment.from_file(path)) == len(AudioSegment.from_file(**raw)) == 1000
            assert sum(len(c) for c in AudioSegment.stream(path)) == 1000
        monkeypatch.setattr(pydub2, name, None)


def alive(pid, name):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open("/proc/%d/stat" % pid) as f:
            comm, _, rest = f.read().partition("(")[2].rpartition(")")
    except OSError:
        return True
    # a pid that another process took after ours went is not ours
    return comm == name and rest.split()[0] != "Z"


# snap, conda and docker put ffmpeg behind a shell script
@posix
def test_timeout_stops_what_a_wrapper_started(tmp_path, monkeypatch):
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", 0.5)
    pidfile = tmp_path / "pid"
    with pytest.raises(TimeoutError):
        utils.run(["/bin/sh", "-c", 'PATH=/bin:/usr/bin:$PATH; sleep 60 & echo $! > "$0"; wait', str(pidfile)])
    pid = int(pidfile.read_text())
    try:
        end = time.time() + 5
        while alive(pid, "sleep") and time.time() < end:
            time.sleep(0.05)
        assert not alive(pid, "sleep")
    finally:
        if alive(pid, "sleep"):
            os.kill(pid, signal.SIGKILL)
