import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time

import pytest

import _pydub2
import pydub2
from pydub2 import AudioSegment, _ffmpeg, _mp3, utils
from pydub2.exceptions import CouldntDecodeError

ffmpeg = shutil.which("ffmpeg")
need_ffmpeg = pytest.mark.skipif(ffmpeg is None or shutil.which("ffprobe") is None, reason="no ffmpeg")
posix = pytest.mark.skipif(sys.platform == "win32", reason="posix only")
pydub = pytest.importorskip("pydub")

SCRIPT = b"ffconcat version 1.0\nfile secret.wav\n"


def tone(path, secs=1.0, *args):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=%s" % secs,
                    "-ar", "16000", *args, str(path)], check=True)


@pytest.fixture
def secret(tmp_path, monkeypatch):
    AudioSegment.silent(1000, 8000).export(str(tmp_path / "secret.wav"), "wav")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pydub2, "max_duration", None)
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", None)
    return tmp_path


def peak_mb(code):
    code = "import resource, sys\nimport _pydub2\n" + code + "\nr = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n" \
        "print(r / (1 if sys.platform == 'darwin' else 1024) / 1e6)"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return float(out.stdout)


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
    mb = peak_mb("_pydub2.detect_silence(bytes(50000), 1, 1, 1, 1000, -40, 1)")
    assert mb < 150


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
    monkeypatch.setenv("PATH", str(empty) + os.pathsep)
    assert utils.which("ffmpeg") is None
    assert _ffmpeg.find("ffmpeg") is None
    assert not utils._has_prober()
    monkeypatch.delenv("PATH")
    assert utils.which("ffmpeg") is None


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
    assert peak_mb("try:\n _pydub2.encode(b'', 44100, 65535, 2, 'flac')\nexcept RuntimeError: pass") < 400


def test_resample_checks_the_ratio_first():
    with pytest.raises(RuntimeError):
        _pydub2.resample(bytes(200000), 8000, 1, 2, 8000 * 1000)
    assert peak_mb("try:\n _pydub2.resample(bytes(200000), 8000, 1, 2, 8000 * 1000)\nexcept RuntimeError: pass") < 150


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
