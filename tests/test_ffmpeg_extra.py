import io
import math
import os
import shutil
import struct
import subprocess
import sys
import types
import wave

import pytest

from pydub2 import AudioSegment, _ffmpeg, utils
from pydub2.utils import mediainfo


@pytest.fixture
def nopath(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(AudioSegment, "converter", "ffmpeg")
    return tmp_path


@pytest.fixture
def exe(nopath):
    pytest.importorskip("imageio_ffmpeg")
    path = utils.extra_ffmpeg()
    if path is None:
        pytest.skip("imageio_ffmpeg has no binary here")
    return path


def sine():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<hh", s, s) for s in
                               (int(8000 * math.sin(i * 0.1)) for i in range(44100))))
    return buf.getvalue()


def script(path, target, log=None):
    path.write_text("#!/bin/sh\n" + ("echo x >> %s\n" % log if log else "") + 'exec "%s" "$@"\n' % target)
    path.chmod(0o755)
    return str(path)


def test_missing(nopath, monkeypatch):
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    assert _ffmpeg.find("ffmpeg") is None
    with pytest.raises(FileNotFoundError, match=r'ffmpeg not found, pydub2 needs it for mp3. install ffmpeg or pip install "pydub2\[ffmpeg\]"$'):
        _ffmpeg.run("ffmpeg", [], None, "mp3")
    with pytest.raises(FileNotFoundError, match=r"no ffmpeg at /nope/ffmpeg, pydub2 needs it for mp3$"):
        _ffmpeg.run("/nope/ffmpeg", [], None, "mp3")
    with pytest.warns(RuntimeWarning, match="Couldn't find ffmpeg or avconv"):
        assert utils.get_encoder_name() == "ffmpeg"


def fake_imageio(monkeypatch, tmp_path):
    def probe():
        raise AssertionError("get_ffmpeg_exe would run a bare ffmpeg")
    pkg = tmp_path / "imageio_ffmpeg"
    (pkg / "binaries").mkdir(parents=True)
    fake = types.ModuleType("imageio_ffmpeg")
    fake.__file__ = str(pkg / "__init__.py")
    fake.get_ffmpeg_exe = probe
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    return pkg / "binaries"


def test_extra_never_probes(nopath, monkeypatch):
    bins = fake_imageio(monkeypatch, nopath)
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    assert utils.extra_ffmpeg() is None
    binary = bins / "ffmpeg-test"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    if sys.platform != "win32":
        assert utils.extra_ffmpeg() == str(binary)
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg")
    assert utils.extra_ffmpeg() is None


def test_extra_is_last(exe, tmp_path):
    assert _ffmpeg.find("ffmpeg") == exe
    assert utils.get_encoder_name() == exe
    assert os.path.basename(utils.get_prober_name()) == "ffprobe"


@pytest.mark.skipif(sys.platform == "win32", reason="shell scripts")
def test_order(exe, tmp_path, monkeypatch):
    on_path = tmp_path / "bin"
    on_path.mkdir()
    script(on_path / "ffmpeg", exe)
    monkeypatch.setenv("PATH", str(on_path))
    assert _ffmpeg.find("ffmpeg") == str(on_path / "ffmpeg")
    assert utils.get_encoder_name() == str(on_path / "ffmpeg")

    log = tmp_path / "log"
    mine = script(tmp_path / "mine", exe, log)
    monkeypatch.setattr(AudioSegment, "converter", mine)
    assert _ffmpeg.find(AudioSegment.converter) == mine
    AudioSegment.from_file(io.BytesIO(AudioSegment.from_wav(io.BytesIO(sine())).export(format="m4a").read()), "m4a")
    assert log.read_text()

    monkeypatch.setattr(AudioSegment, "converter", str(tmp_path / "nothing"))
    with pytest.raises(FileNotFoundError):
        AudioSegment.from_file(io.BytesIO(b"\0" * 100), "m4a")


def test_m4a(exe, tmp_path):
    seg = AudioSegment.from_wav(io.BytesIO(sine()))
    m4a = seg.export(format="m4a").read()
    assert m4a[4:8] == b"ftyp"
    assert seg.export(format="aac").read()[:2] in (b"\xff\xf1", b"\xff\xf9")
    f = tmp_path / "a.m4a"
    f.write_bytes(m4a)
    want = subprocess.run([exe, "-i", str(f), "-vn", "-acodec", "pcm_s16le", "-f", "wav", "-"],
                          capture_output=True).stdout
    want = AudioSegment.from_wav(io.BytesIO(want)).raw_data
    assert len(want) > 100000
    assert AudioSegment.from_file(str(f)).raw_data == want
    with open(f, "rb") as fh:
        assert AudioSegment.from_file(fh, "m4a").raw_data == want
    assert AudioSegment.from_file(io.BytesIO(m4a), "m4a").raw_data == want
    assert mediainfo(str(f))["codec_name"] == "aac"


def set_none(monkeypatch, attr):
    # converter = which("ffmpeg") is a common pydub line, it gives None when nothing is found
    if attr == "converter":
        monkeypatch.setattr(AudioSegment, "converter", None)
    else:
        # on an instance the old ffmpeg name sets the converter of the class
        AudioSegment.silent(10).ffmpeg = None
    assert AudioSegment.converter is None


@pytest.mark.parametrize("attr", ["converter", "ffmpeg"])
def test_converter_none_without_ffmpeg(nopath, monkeypatch, attr):
    from pydub2.__main__ import FFMPEG_FIX, report
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    set_none(monkeypatch, attr)
    found = r'ffmpeg not found, pydub2 needs it for .+\. install ffmpeg or pip install "pydub2\[ffmpeg\]"$'
    with pytest.raises(FileNotFoundError, match=found):
        AudioSegment.from_file(io.BytesIO(b"\0" * 100), "m4a")
    with pytest.raises(FileNotFoundError, match=found):
        AudioSegment.silent(100).export(io.BytesIO(), format="m4a")
    with pytest.raises(FileNotFoundError, match=found):
        mediainfo(io.BytesIO(b"\0" * 100))
    with pytest.raises(FileNotFoundError, match=found):
        utils.mediainfo_json(io.BytesIO(b"\0" * 100))
    seg = AudioSegment.from_wav(io.BytesIO(sine()))
    assert AudioSegment.from_file(seg.export(format="flac"), "flac") == seg
    assert {"for": "m4a, aac, wma and video files", "run": FFMPEG_FIX} in report()["fixes"]


@pytest.mark.parametrize("attr", ["converter", "ffmpeg"])
def test_converter_none_uses_the_extra(exe, monkeypatch, attr):
    set_none(monkeypatch, attr)
    assert _ffmpeg.find(None) == exe
    seg = AudioSegment.from_wav(io.BytesIO(sine()))
    assert abs(len(AudioSegment.from_file(seg.export(format="m4a"), "m4a")) - len(seg)) < 50


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg on PATH")
def test_converter_none_uses_path(monkeypatch):
    set_none(monkeypatch, "converter")
    # Windows spells the same file ffmpeg.exe or ffmpeg.EXE depending on PATHEXT
    assert os.path.normcase(_ffmpeg.find(None)) == os.path.normcase(shutil.which("ffmpeg"))
    seg = AudioSegment.from_wav(io.BytesIO(sine()))
    assert abs(len(AudioSegment.from_file(seg.export(format="m4a"), "m4a")) - len(seg)) < 50
    assert mediainfo(seg.export(format="m4a"))["codec_name"] == "aac"


def test_converter_empty(nopath, monkeypatch):
    # pydub fails here too, with PermissionError from running ""
    monkeypatch.setattr(AudioSegment, "converter", "")
    with pytest.raises(FileNotFoundError, match="^no ffmpeg at '', pydub2 needs it for m4a$"):
        AudioSegment.silent(100).export(io.BytesIO(), format="m4a")
