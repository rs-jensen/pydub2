import io
import math
import os
import shutil
import struct
import subprocess
import sys
import wave

import pytest

import pathlib

from pydub2 import AudioSegment, _ffmpeg, playback, utils

pydub = pytest.importorskip("pydub")

ff = "/opt/homebrew/bin/ffmpeg"
if not os.path.exists(ff):
    ff = "ffmpeg"
pytestmark = pytest.mark.skipif(_ffmpeg.find(ff) is None, reason="no ffmpeg")


def sine(rate=44100, secs=1.0):
    n = int(rate * secs)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<hh", s, s) for s in
                               (int(8000 * math.sin(i * 0.1)) for i in range(n))))
    return buf.getvalue()


def info(wav):
    with wave.open(io.BytesIO(wav)) as w:
        return w.getframerate(), w.getnchannels(), w.getnframes() / w.getframerate()


def test_m4a_roundtrip():
    m4a = _ffmpeg.from_wav(ff, sine(), "ipod")
    rate, ch, secs = info(_ffmpeg.to_wav(ff, data=m4a, format="m4a"))
    assert (rate, ch) == (44100, 2)
    assert abs(secs - 1.0) < 0.1


def test_path_and_slice(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(sine())
    secs = info(_ffmpeg.to_wav(ff, path=str(f), start_second=0.25, duration=0.5))[2]
    assert abs(secs - 0.5) < 0.01


def test_mp3_tags():
    mp3 = _ffmpeg.from_wav(ff, sine(), "mp3", bitrate="64k", tags={"title": "hello"})
    assert mp3[:3] == b"ID3"
    assert b"hello" in mp3


def test_tag_with_data_in_it(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(_ffmpeg.from_wav(ff, sine(), "mp3", tags={"artist": "data"}))
    wav = _ffmpeg.to_wav(ff, path=str(f), duration=0.5)
    assert b"IART" in wav
    assert abs(info(wav)[2] - 0.5) < 0.01


def test_missing_converter():
    with pytest.raises(FileNotFoundError, match="ffmpeg not found, pydub2 needs it for mp3"):
        _ffmpeg.from_wav("nope-not-here", sine(), "mp3")


def test_bad_data():
    with pytest.raises(_ffmpeg.CouldntDecodeError):
        _ffmpeg.to_wav(ff, data=b"not audio at all")


real = shutil.which("ffmpeg"), shutil.which("ffprobe"), shutil.which("ffplay")
need_real = pytest.mark.skipif(not (real[0] and real[1]) or sys.platform == "win32", reason="needs ffmpeg and ffprobe on posix")


@pytest.fixture(scope="module")
def m4a(tmp_path_factory):
    path = tmp_path_factory.mktemp("m4a") / "a.m4a"
    subprocess.run([real[0], "-y", "-v", "quiet", "-f", "lavfi", "-i", "sine=duration=0.5", "-c:a", "aac", str(path)], check=True)
    return str(path)


def outcome(f):
    try:
        return f()
    except Exception as e:
        return type(e)


def private(tmp_path, monkeypatch, where, probe):
    folder = tmp_path / ("my bin" if where == "spaces" else "bin")
    folder.mkdir()
    for name, src in zip(("ffmpeg", "ffprobe", "ffplay"), real):
        if src and (probe or name != "ffprobe"):
            (folder / name).symlink_to(src)
    monkeypatch.setenv("PATH", str(folder) if where == "bare" else str(tmp_path))
    monkeypatch.chdir(tmp_path)
    conv = {"bare": "ffmpeg", "relative": "bin/ffmpeg"}.get(where, str(folder / "ffmpeg"))
    monkeypatch.setattr(AudioSegment, "converter", conv)
    monkeypatch.setattr(pydub.AudioSegment, "converter", conv)
    return folder


@need_real
@pytest.mark.parametrize("probe", [True, False])
@pytest.mark.parametrize("where", ["full", "relative", "bare", "spaces"])
def test_reading_with_a_private_converter(tmp_path, monkeypatch, m4a, where, probe):
    private(tmp_path, monkeypatch, where, probe)
    want = outcome(lambda: pydub.AudioSegment.from_file(m4a).raw_data)
    # pydub only finds ffprobe on PATH, pydub2 also next to the converter and falls back to ffmpeg -i
    assert (want is FileNotFoundError) != (where == "bare" and probe)
    with open(m4a, "rb") as f:
        data = f.read()
    segs = [AudioSegment.from_file(m4a), AudioSegment.from_file(open(m4a, "rb")), AudioSegment.from_file(io.BytesIO(data))]
    assert len(segs[0]) > 400
    assert all(s.raw_data == segs[0].raw_data for s in segs)
    if probe and where == "bare":
        assert segs[0].raw_data == want
    assert utils.mediainfo(m4a)["codec_name"] == "aac"
    assert utils.mediainfo_json(m4a)["streams"][0]["codec_name"] == "aac"
    for fmt in ("m4a", "mp3"):
        assert len(AudioSegment.silent(200).export(io.BytesIO(), format=fmt).getvalue()) > 100


@need_real
def test_missing_converter_raises_like_pydub(tmp_path, monkeypatch, m4a):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    for conv in ("ffmpeg", str(tmp_path / "nope" / "ffmpeg")):
        monkeypatch.setattr(AudioSegment, "converter", conv)
        monkeypatch.setattr(pydub.AudioSegment, "converter", conv)
        for f in (lambda a: a.from_file(m4a), lambda a: a.silent(100).export(io.BytesIO(), format="m4a")):
            assert outcome(lambda: f(AudioSegment)) is FileNotFoundError
        assert outcome(lambda: pydub.AudioSegment.from_file(m4a)) is FileNotFoundError
        assert outcome(lambda: utils.mediainfo(m4a)) is outcome(lambda: pydub.utils.mediainfo(m4a)) is FileNotFoundError


@need_real
@pytest.mark.parametrize("probe", [True, False])
def test_prober_and_player_lookup(tmp_path, monkeypatch, probe):
    folder = private(tmp_path, monkeypatch, "full", probe)
    want = str(folder / "ffprobe") if probe else "ffprobe"
    assert utils.get_prober_name() == want
    assert (utils.get_player_name() == str(folder / "ffplay")) == bool(real[2])


@need_real
def test_patched_prober_is_used_without_one_on_path(tmp_path, monkeypatch, m4a):
    private(tmp_path, monkeypatch, "full", False)
    monkeypatch.setattr(utils, "get_prober_name", lambda: real[1])
    monkeypatch.setattr(pydub.utils, "get_prober_name", lambda: real[1])
    want = pydub.utils.mediainfo(m4a)
    assert utils.mediainfo(m4a)["bits_per_sample"] == want["bits_per_sample"]
    assert utils.mediainfo(m4a)["duration"] == want["duration"]


@need_real
@pytest.mark.parametrize("probe", [True, False])
@pytest.mark.parametrize("kind", [str, pathlib.Path])
def test_a_subclass_converter_is_used_everywhere(tmp_path, monkeypatch, m4a, probe, kind):
    folder = private(tmp_path, monkeypatch, "full", probe)
    (folder / "ffplay").unlink(missing_ok=True)
    (folder / "ffplay").write_text("#!/bin/sh\n")
    (folder / "ffplay").chmod(0o755)
    monkeypatch.setattr(AudioSegment, "converter", "no-such-ffmpeg")

    class My(AudioSegment):
        converter = kind(folder / "ffmpeg")

    assert len(My.from_file(m4a)) > 400
    assert len(My.from_file(io.BytesIO(open(m4a, "rb").read()))) > 400
    with utils._using(My.converter):
        assert utils.mediainfo(m4a)["codec_name"] == "aac"
        assert utils.mediainfo_json(m4a)["streams"][0]["codec_name"] == "aac"
        assert "aac" in utils.get_supported_decoders()
    for fmt in ("m4a", "mp3"):
        assert len(My.silent(200).export(io.BytesIO(), format=fmt).getvalue()) > 100
    seen = []
    monkeypatch.setattr(playback.subprocess, "call", lambda cmd, **kw: seen.append(cmd))
    playback._play_with_ffplay(My.silent(100))
    assert seen[0][0] == str(folder / "ffplay")


def test_which_takes_a_path_on_windows(monkeypatch):
    monkeypatch.setattr(utils.os, "name", "nt")
    assert utils.which(pathlib.PurePath("no-such-program")) is None


FORMATS = {
    "wav": "pcm_u8 pcm_s16le pcm_s24le pcm_s32le pcm_f32le pcm_f64le pcm_mulaw pcm_alaw adpcm_ima_wav adpcm_ms",
    "aiff": "pcm_u8 pcm_s8 pcm_s16be pcm_s24be pcm_s32be pcm_f32be pcm_mulaw pcm_alaw adpcm_ima_qt",
    "caf": "pcm_s16le pcm_s24le pcm_s24be pcm_s32le pcm_f32le pcm_f64be pcm_mulaw pcm_alaw adpcm_ima_qt",
    "mov": "pcm_u8 pcm_s16be pcm_s24le pcm_s24be pcm_s32le pcm_f32be pcm_mulaw pcm_alaw adpcm_ima_qt",
    "au": "pcm_s8 pcm_s16be pcm_s24be pcm_s32be pcm_f32be pcm_f64be pcm_mulaw pcm_alaw",
    "w64": "pcm_u8 pcm_s16le pcm_s24le pcm_s32le pcm_f32le pcm_mulaw pcm_alaw adpcm_ima_wav",
}


@need_real
@pytest.mark.parametrize("fmt,codec", [(f, c) for f, cs in FORMATS.items() for c in cs.split()])
def test_reading_without_ffprobe_gives_what_ffprobe_gives(tmp_path, monkeypatch, fmt, codec):
    path = str(tmp_path / ("a." + fmt))
    made = subprocess.run([real[0], "-y", "-v", "quiet", "-f", "lavfi", "-i", "sine=d=0.2:r=11025", "-ac", "2",
                           "-c:a", codec, path])
    if made.returncode:
        pytest.skip("this ffmpeg cant write it")

    def read():
        seg = AudioSegment.from_file(path)
        return (seg.sample_width, seg.frame_rate, seg.channels, seg.raw_data,
                utils.mediainfo_json(path)["streams"][0]["bits_per_sample"])

    want = read()
    which = utils.which
    monkeypatch.setattr(utils, "which", lambda p: None if os.path.basename(p) in ("ffprobe", "avprobe") else which(p))
    assert read() == want


@need_real
@pytest.mark.parametrize("args", [0, 1])
@pytest.mark.parametrize("name", ["get_prober_name", "get_encoder_name", "get_player_name"])
def test_replaced_lookups_still_work(tmp_path, monkeypatch, m4a, name, args):
    folder = private(tmp_path, monkeypatch, "full", False)
    path = {"get_prober_name": real[1], "get_encoder_name": real[0], "get_player_name": "/the/player"}[name]
    seen = []

    def one(converter=None):
        seen.append(converter)
        return path

    def none():
        seen.append(None)
        return path

    monkeypatch.setattr(utils, name, one if args else none)
    monkeypatch.setattr(playback, name, one if args else none, raising=False)
    assert len(AudioSegment.from_file(m4a)) > 400
    assert utils.mediainfo(m4a)["codec_name"] == "aac"
    assert len(AudioSegment.silent(200).export(io.BytesIO(), format="m4a").getvalue()) > 100
    if name == "get_encoder_name":
        utils._codecs.cache_clear()
        with utils._using("no-such-ffmpeg"):
            assert "aac" in utils.get_supported_decoders()
        utils._codecs.cache_clear()
    calls = []
    monkeypatch.setattr(playback.subprocess, "call", lambda cmd, **kw: calls.append(cmd))
    playback._play_with_ffplay(AudioSegment.silent(100))
    assert calls[0][0] == (path if name == "get_player_name" else str(folder / "ffplay"))
    assert all(x is None for x in seen)
