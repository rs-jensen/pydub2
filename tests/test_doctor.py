import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import types

import pytest

import pydub2
from pydub2 import AudioSegment, _ffmpeg, _mp3, utils
from pydub2.__main__ import FFMPEG_FIX, check_file, check_files, check_safely, main, scan, what
from pydub2.exceptions import CouldntDecodeError
from pydub2.generators import Sine


@pytest.fixture
def noffmpeg(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(AudioSegment, "converter", "ffmpeg")
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    # files are checked in another process, this hides the ffmpeg extra there too
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "none")


def run(capsys, *args):
    code = main(["doctor", *args])
    return code, capsys.readouterr().out


def tone(path, format="flac"):
    Sine(440).to_audio_segment(duration=1000).export(str(path), format=format).close()
    return path


def test_report(capsys):
    code, out = run(capsys)
    assert code == 0
    assert out.startswith("pydub2 " + pydub2.__version__)
    code, out = run(capsys, "--json")
    r = json.loads(out)
    assert r["ok"] and r["read_natively"] == ["wav", "flac", "ogg", "opus", "mp3"] and r["strict"] is False
    assert r["pydub"]["resolves_to"] in (None, "pydub", "pydub2")


def test_report_without_ffmpeg(capsys, noffmpeg, monkeypatch):
    code, out = run(capsys, "--json")
    r = json.loads(out)
    assert code == 0 and r["ffmpeg"] is None
    assert 'pip install "pydub2[ffmpeg]"' in [f["run"] for f in r["fixes"]]
    code, out = run(capsys)
    assert "ffmpeg: none found" in out and 'missing, m4a, aac, wma and video files: pip install "pydub2[ffmpeg]"' in out
    monkeypatch.setattr(pydub2, "strict", True)
    code, out = run(capsys, "--json")
    assert code == 1 and json.loads(out)["strict"] is True


def test_report_old_ffmpeg(capsys, tmp_path, monkeypatch):
    exe = tmp_path / "ffmpeg"
    exe.write_text("#!/bin/sh\necho 'ffmpeg version 2.8.1 Copyright'\n")
    exe.chmod(0o755)
    monkeypatch.setattr(AudioSegment, "converter", str(exe))
    if sys.platform == "win32":
        pytest.skip("a shell script is no ffmpeg on windows")
    code, out = run(capsys, "--json")
    r = json.loads(out)
    assert code == 1 and r["ffmpeg"]["version"] == "2.8.1" and not r["ffmpeg"]["ok"]
    assert r["ffmpeg"]["from"] == "AudioSegment.converter"


def test_files(capsys, tmp_path):
    good = tone(tmp_path / "a.flac")
    data = good.read_bytes()
    cut = tmp_path / "cut.flac"
    cut.write_bytes(data[:len(data) * 2 // 3])
    playlist = tmp_path / "list.mp3"
    playlist.write_text("#EXTM3U\n#EXTINF:1,x\nhttp://example.com/a.mp3\n")
    text = tmp_path / "text.wav"
    text.write_text("this is not audio\n" * 50)
    code, out = run(capsys, "--json", str(good), str(cut), str(playlist), str(text), str(tmp_path / "none.wav"))
    a, b, c, d, e = json.loads(out)
    assert code == 1
    assert a["loads"] and a["how"] == "natively" and a["format"] == "flac" and a["channels"] == 1 and a["rate"] == 44100
    assert abs(a["duration"] - 1) < 0.01 and a["sample_width"] == 2 and a["note"] is None
    assert b["loads"] and b["reason"] == "truncated" and "last" in b["note"]
    assert not c["loads"] and c["reason"] == "playlist_refused" and c["format"] == "playlist"
    assert not d["loads"] and d["format"] is None and "not audio" in d["note"]
    assert not e["loads"] and e["reason"] == "cant_open"
    code, out = run(capsys, str(good))
    assert code == 0 and out.startswith(str(good) + ": flac, loads natively, 1.00 s, 44100 Hz, 1 channel, 16 bit")


@pytest.mark.skipif(not (_mp3.available() or shutil.which("ffmpeg")), reason="no way to write mp3")
def test_junk_before_mp3(capsys, tmp_path):
    tone(tmp_path / "a.mp3", "mp3")
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"\0" * 3000 + (tmp_path / "a.mp3").read_bytes())
    code, out = run(capsys, "--json", str(junk))
    r = json.loads(out)[0]
    assert code == 0 and r["loads"] and "about 3000 bytes of junk: ok" in r["note"]


def test_needs_ffmpeg(capsys, noffmpeg, tmp_path):
    m4a = tmp_path / "a.m4a"
    m4a.write_bytes(b"\0\0\0\x18ftypM4A \0\0\0\0M4A isom" + b"\0" * 100)
    code, out = run(capsys, str(m4a))
    assert code == 1
    assert out == '%s: m4a needs ffmpeg, none found: pip install "pydub2[ffmpeg]"\n' % m4a
    with pytest.raises(FileNotFoundError, match="ffmpeg not found") as e:
        AudioSegment.from_file(str(m4a))
    assert (e.value.format, e.value.reason, e.value.hint) == ("m4a", "needs_ffmpeg", 'pip install "pydub2[ffmpeg]"')


def test_error_attributes(tmp_path, monkeypatch):
    playlist = tmp_path / "list.m3u8"
    playlist.write_text("#EXTM3U\nhttp://example.com/a.mp3\n")
    with pytest.raises(CouldntDecodeError) as e:
        AudioSegment.from_file(str(playlist))
    assert str(e.value) == "cant decode this file"
    assert (e.value.format, e.value.reason, e.value.hint) == ("playlist", "playlist_refused", None)
    good = tone(tmp_path / "a.flac")
    monkeypatch.setattr(pydub2, "max_duration", 0.5)
    with pytest.raises(CouldntDecodeError, match="^this file is longer than max_duration$") as e:
        AudioSegment.from_file(str(good))
    assert e.value.reason == "too_long"
    assert CouldntDecodeError("x").reason is None and CouldntDecodeError("x").format is None


def test_sniff():
    assert utils.sniff(b"RIFF\0\0\0\0WAVEfmt ") == "wav"
    assert utils.sniff(b"fLaC") == "flac"
    assert utils.sniff(b"OggS" + b"\0" * 24 + b"OpusHead") == "opus"
    assert utils.sniff(b"OggS" + b"\0" * 24 + b"\x01vorbis") == "ogg"
    assert utils.sniff(b"ID3\x04") == "mp3" and utils.sniff(b"\xff\xfb\x90\x00") == "mp3"
    assert utils.sniff(b"\xff\xf1\x50\x80") == "aac"
    assert utils.sniff(b"\0\0\0\x18ftypisom") == "mp4" and utils.sniff(b"\0\0\0\x18ftypM4A ") == "m4a"
    assert utils.sniff(b"  \n#EXTM3U\n") == "playlist"
    assert utils.sniff(b"hello") is None and utils.sniff(b"") is None and utils.sniff(b"\xff") is None


SOURCE = '''import pydub
from pydub import AudioSegment
from pydub.playback import play
import audioop

AudioSegment.converter = "/opt/ffmpeg"
AudioSegment.ffprobe = "/opt/ffprobe"
pydub.utils.get_prober_name = lambda: "x"

song = AudioSegment.from_file("a.m4a")
other = AudioSegment.from_file(path, format="ogg")
song.export("out.wav", format="wav", parameters=["-ar", "16000"])
song.export("out.m4a", format="m4a", codec="aac")
AudioSegment.from_file("http://example.com/a.mp3")
info = pydub.utils.mediainfo("a.wav")
try:
    pass
except audioop.error:
    pass
mocker.patch("pydub.utils.get_prober_name")
'''


def test_scan(capsys, tmp_path):
    (tmp_path / "app.py").write_text(SOURCE)
    (tmp_path / "plain.py").write_text("song.export('x.m4a')\nimport json\n")
    (tmp_path / "broken.py").write_text("def (:\n")
    (tmp_path / "nul.py").write_bytes(b"import pydub\0\n")
    for d in ("venv", "node_modules", ".git"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.py").write_text("import pydub\n")
    (tmp_path / "env").mkdir()
    (tmp_path / "env" / "pyvenv.cfg").write_text("")
    (tmp_path / "env" / "x.py").write_text("import pydub\n")
    r = scan(str(tmp_path))
    assert (r["files"], r["files_using_pydub"]) == (4, 1)
    got = {(f["line"], f["kind"]) for f in r["findings"]}
    assert {(1, "import"), (2, "import"), (3, "import"), (3, "note"), (4, "note"), (6, "note"), (7, "change"),
            (8, "note"), (10, "ffmpeg"), (11, "strict"), (12, "strict"), (13, "ffmpeg"), (13, "strict"),
            (14, "change"), (15, "note"), (18, "change"), (20, "change")} <= got
    assert all(f["file"].endswith("app.py") for f in r["findings"])
    assert r["need_change"] == r["imports"] + 4 and r["imports"] == 3
    code, out = run(capsys, "--scan", str(tmp_path))
    assert code == 0 and "app.py:7: AudioSegment.ffprobe does nothing" in out
    assert out.splitlines()[-3] == "1 of 4 python files use pydub, 2 could not be read and are not counted"
    code, out = run(capsys, "--json", "--scan", str(tmp_path))
    assert json.loads(out)["need_change"] == r["need_change"]


def test_scan_no_pydub(capsys, tmp_path):
    (tmp_path / "a.py").write_text("from pydub2 import AudioSegment\nAudioSegment.from_file('a.m4a')\n")
    code, out = run(capsys, "--scan", str(tmp_path))
    assert code == 0 and out.splitlines()[0] == "0 of 1 python files use pydub"


def test_scan_and_files_do_not_mix(tmp_path):
    with pytest.raises(SystemExit):
        main(["doctor", "--scan", str(tmp_path), "a.wav"])


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(pydub2.__file__)))
posix = pytest.mark.skipif(sys.platform == "win32", reason="no fifos or unix sockets")


def cli(*args, **env):
    # a new process with a timeout, so a doctor that blocks fails instead of hanging the tests
    e = dict(os.environ, PYTHONPATH=ROOT, **env)
    return subprocess.run([sys.executable, "-m", "pydub2", "doctor", *args], capture_output=True, timeout=60, env=e)


def unix_socket(path):
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(path))
    return s


@posix
def test_files_that_are_not_files(tmp_path):
    os.mkfifo(tmp_path / "pipe.wav")
    sock = unix_socket(tmp_path / "sock.wav")
    try:
        p = cli("--json", str(tmp_path / "pipe.wav"), str(tmp_path / "sock.wav"), str(tmp_path), os.devnull)
    finally:
        sock.close()
    rs = json.loads(p.stdout)
    assert p.returncode == 1 and [r["reason"] for r in rs] == ["not_a_file"] * 4
    assert [r["note"] for r in rs] == ["is a fifo, not read", "is a socket, not read", "is a directory, not read",
                                       "is a device, not read"]


@posix
def test_scan_skips_what_is_not_a_file(tmp_path):
    (tmp_path / "a.py").write_text("import pydub\n")
    os.mkfifo(tmp_path / "pipe.py")
    sock = unix_socket(tmp_path / "sock.py")
    try:
        p = cli("--json", "--scan", str(tmp_path))
    finally:
        sock.close()
    r = json.loads(p.stdout)
    assert p.returncode == 0 and (r["files"], r["files_using_pydub"]) == (3, 1)
    assert [(os.path.basename(u["file"]), u["why"]) for u in r["unreadable"]] == [
        ("pipe.py", "is a fifo, not read"), ("sock.py", "is a socket, not read")]


@pytest.fixture
def broken_mp3(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pydub2_mp3", types.SimpleNamespace(path=lambda: str(tmp_path / "none.so")))
    _mp3.lib.cache_clear()
    yield
    _mp3.lib.cache_clear()


def test_mp3_library_that_does_not_load(capsys, broken_mp3):
    code, out = run(capsys, "--json")
    r = json.loads(out)
    assert code == 1 and not r["ok"] and r["pydub2_mp3"] is False and "mp3" not in r["write_natively"]
    assert [f["run"] for f in r["fixes"]] == ["pip install --force-reinstall pydub2-mp3"]
    code, out = run(capsys)
    assert "(pydub2-mp3 broken)" in out and "missing, pydub2-mp3 is installed but does not load" in out


def test_never_a_traceback(capsys, monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(sys.modules["pydub2.__main__"], "report", boom)
    code, out = run(capsys, "--json")
    assert code == 2 and json.loads(out) == {"error": "RuntimeError: x"}
    good = tone(tmp_path / "\xe9.flac")
    p = cli(str(good), PYTHONIOENCODING="ascii")
    assert p.returncode == 0 and p.stderr == b""
    assert p.stdout.endswith(b"?.flac: flac, loads natively, 1.00 s, 44100 Hz, 1 channel, 16 bit\n")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_format_of_files_only_ffmpeg_reads(tmp_path):
    names = ["ac3", "au", "caf", "w64", "wv", "mp2"]
    for n in names:
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=0.5", str(tmp_path / ("a." + n))], check=True)
    # a wav behind an id3 tag, and an mp2 named mp3
    id3 = tmp_path / "id3.mp3"
    id3.write_bytes(b"ID3\4\0\0\0\0\0\x0a" + b"\0" * 10 + tone(tmp_path / "t.wav", "wav").read_bytes())
    mp2 = tmp_path / "mp2.mp3"
    mp2.write_bytes((tmp_path / "a.mp2").read_bytes())
    rs = json.loads(cli("--json", *[str(tmp_path / ("a." + n)) for n in names], str(id3), str(mp2)).stdout)
    assert [(r["format"], r["how"]) for r in rs] == [(n, "through ffmpeg") for n in names + ["wav", "mp2"]]
    unknown = tmp_path / "x.bin"
    unknown.write_bytes(b"\x01\x02" * 100)
    r = check_file(str(unknown))
    assert r["format"] is None and not r["loads"]


def wav_head(tag, size):
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + size, b"WAVE", b"fmt ", 16, tag, 1, 8000, 8000, 1, 8,
                       b"data", size)


def test_advice_matches_the_cause(capsys, noffmpeg, tmp_path):
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    text = tmp_path / "notes.txt"
    text.write_text("no audio here\n" * 20)
    broken = tmp_path / "broken.bin"
    broken.write_bytes(b"RIFF\0\0\0\0WAVEjunkjunk" + b"\1" * 100)
    alaw = tmp_path / "alaw.wav"
    alaw.write_bytes(wav_head(6, 8000) + b"\x55" * 8000)
    code, out = run(capsys, "--json", str(empty), str(text), str(broken), str(alaw))
    assert [(r["reason"], r["hint"], r["note"]) for r in json.loads(out)] == [
        ("empty", None, "file is empty"),
        ("not_audio", None, "file is text, not audio"),
        ("cant_decode", None, "cant decode wav, the file is damaged"),
        ("needs_ffmpeg", FFMPEG_FIX, "wav needs ffmpeg, none found: " + FFMPEG_FIX)]


def test_strict_mode(capsys, noffmpeg, monkeypatch, tmp_path):
    wav, flac = tone(tmp_path / "a.wav", "wav"), tone(tmp_path / "a.flac")
    monkeypatch.setattr(pydub2, "strict", True)
    r = check_file(str(wav))
    assert r["loads"] and r["how"] == "natively"
    assert check_file(str(flac))["reason"] == "needs_ffmpeg"
    code, out = run(capsys, "--json")
    r = json.loads(out)
    assert r["read_natively"] == r["write_natively"] == ["wav"]


@pytest.mark.parametrize("format", ["wav", "mp3", "ogg", "opus", "flac"])
def test_truncated(tmp_path, format):
    if format == "mp3" and not (_mp3.available() or shutil.which("ffmpeg")):
        pytest.skip("no way to write mp3")
    # opus pages hold about a second, a cut must leave whole ones
    whole = tmp_path / ("a." + format)
    Sine(440).to_audio_segment(duration=5000).export(str(whole), format=format).close()
    data = whole.read_bytes()
    cut = tmp_path / ("cut." + format)
    cut.write_bytes(data[:len(data) * 2 // 3])
    r = check_file(str(whole))
    assert r["loads"] and r["reason"] is None and r["note"] is None
    r = check_file(str(cut))
    assert r["loads"] and r["reason"] == "truncated"
    if format in ("wav", "mp3", "flac"):
        assert abs(float(r["note"].split()[2]) - (5 - r["duration"])) < 0.01
    else:
        assert r["note"] == "truncated, the end is missing"


def test_scan_lists_what_it_cant_read(capsys, tmp_path, monkeypatch):
    (tmp_path / "old.py").write_text("import pydub\nprint 'hi'\n")
    (tmp_path / "big.py").write_text("x = 1\n" * 400000)
    code, out = run(capsys, "--scan", str(tmp_path))
    assert code == 0 and out.splitlines()[:3] == [
        "%s: larger than 2 MB" % (tmp_path / "big.py"),
        "%s: cant parse line 2: Missing parentheses in call to 'print'. Did you mean print(...)?" % (tmp_path / "old.py"),
        "0 of 2 python files use pydub, 2 could not be read and are not counted"]
    # a single file works too
    r = scan(str(tmp_path / "old.py"))
    assert r["files"] == 1 and r["unreadable"][0]["file"] == str(tmp_path / "old.py")
    # a directory it cant list
    (tmp_path / "sub").mkdir()
    real = os.scandir

    def scandir(path="."):
        if os.path.basename(path) == "sub":
            raise PermissionError(13, "Permission denied", path)
        return real(path)
    monkeypatch.setattr(os, "scandir", scandir)
    assert {"file": str(tmp_path / "sub"), "why": "cant list: Permission denied"} in scan(str(tmp_path))["unreadable"]


def test_scan_missing_path(capsys, tmp_path):
    code, out = run(capsys, "--json", "--scan", str(tmp_path / "nope"))
    assert code == 2 and "no directory or file" in json.loads(out)["error"]
    code, out = run(capsys, "--scan", str(tmp_path / "nope"))
    assert code == 2 and out == ""


def test_scan_order(tmp_path, monkeypatch):
    for d in "abc":
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.py").write_text("import pydub\n")
    real = os.scandir

    class Reversed:
        # what os.walk needs from scandir: an iterator that is also a context manager
        def __init__(self, path):
            with real(path) as it:
                self.it = iter(sorted(it, key=lambda e: e.name, reverse=True))

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.it)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(os, "scandir", Reversed)
    assert [f["file"] for f in scan(str(tmp_path))["findings"]] == [str(tmp_path / d / "x.py") for d in "abc"]


def test_doctor_patches_nothing(tmp_path, monkeypatch):
    # a load in another thread, or one that is interrupted, must never meet a wrapped _ffmpeg
    flac, real, seen = tone(tmp_path / "a.flac"), _ffmpeg.to_wav, []
    load = AudioSegment.from_file.__func__

    def from_file(cls, *args, **kwargs):
        seen.append(_ffmpeg.to_wav)
        t = threading.Thread(target=lambda: seen.append(_ffmpeg._seen.get()))
        t.start()
        t.join()
        return load(cls, *args, **kwargs)
    monkeypatch.setattr(AudioSegment, "from_file", classmethod(from_file))
    assert check_file(str(flac))["how"] == "natively"
    assert seen == [real, None] and _ffmpeg._seen.get() is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_how_a_file_loads(tmp_path):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=0.5", str(tmp_path / "a.au")], check=True)
    assert check_file(str(tmp_path / "a.au"))["how"] == "through ffmpeg"
    assert check_file(str(tone(tmp_path / "a.flac")))["how"] == "natively"
    assert _ffmpeg._seen.get() is None


def test_each_file_gets_its_own_result(capsys, monkeypatch, tmp_path):
    good = str(tone(tmp_path / "a.flac"))
    code, out = run(capsys, "--json", good, "a\0b.wav", good)
    rs = json.loads(out)
    assert code == 1 and [r["loads"] for r in rs] == [True, False, True]
    assert rs[1]["reason"] == "cant_open" and "null" in rs[1]["note"]
    # a fault in doctor itself stays with the file it happened on
    monkeypatch.setattr(sys.modules["pydub2.__main__"], "open_regular", lambda path: 1 / 0)
    r = check_safely(good)
    assert (r["file"], r["loads"], r["reason"]) == (good, False, "doctor_failed") and "ZeroDivisionError" in r["note"]


def fake_ffmpeg(tmp_path, monkeypatch, body):
    exe = tmp_path / "ffmpeg"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(0o755)
    monkeypatch.setattr(AudioSegment, "converter", str(exe))
    m4a = tmp_path / "a.m4a"
    m4a.write_bytes(b"\0\0\0\x18ftypM4A \0\0\0\0M4A isom" + b"\0" * 100)
    return str(m4a)


@posix
def test_a_file_that_hangs(tmp_path, monkeypatch):
    m4a = fake_ffmpeg(tmp_path, monkeypatch, "echo $$ > %s\nexec sleep 600\n" % (tmp_path / "pid"))
    a, b = check_files([m4a, str(tone(tmp_path / "a.flac"))], timeout=5)
    assert (a["loads"], a["reason"]) == (False, "timeout") and "5 s" in a["note"]
    assert b["loads"] and b["how"] == "natively"
    # the ffmpeg that hung is gone too
    with pytest.raises(ProcessLookupError):
        for _ in range(50):
            os.kill(int((tmp_path / "pid").read_text()), 0)
            time.sleep(0.1)


@posix
def test_a_file_that_kills_the_check(tmp_path, monkeypatch):
    m4a = fake_ffmpeg(tmp_path, monkeypatch, "kill -9 $PPID\n")
    a, b = check_files([m4a, str(tone(tmp_path / "a.flac"))])
    assert (a["loads"], a["reason"], a["note"]) == (False, "doctor_failed", "the check of this file crashed")
    assert b["loads"]


def test_reader_that_leaves(tmp_path):
    good = str(tone(tmp_path / "a.flac"))
    e = dict(os.environ, PYTHONPATH=ROOT)
    p = subprocess.Popen([sys.executable, "-m", "pydub2", "doctor", "--json"] + [good] * 600, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=e)
    p.stdout.read(1)
    p.stdout.close()
    assert p.stderr.read() == b"" and p.wait(timeout=60) == 0


def test_command_line(capsys, tmp_path):
    # an empty --scan is a path that is not there, not the install report
    code, out = run(capsys, "--json", "--scan", "")
    assert code == 2 and "no directory or file" in json.loads(out)["error"]
    with pytest.raises(SystemExit) as e:
        main(["doctor", "--timeout", "0", "a.wav"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["doctor", "--help"])
    assert e.value.code == 0 and "exit codes:" in capsys.readouterr().out


def named(data):
    return what(io.BytesIO(data), data[:65536])


def test_format_is_not_guessed_from_two_bytes(tmp_path):
    assert named("no audio".encode("utf-16")) is None
    assert named(b"\xff" * 5000) is None and named(b"\xff\xfb" + bytes(range(256)) * 20) is None
    # a frame header with nothing behind it, and one with a second frame where it must be
    one = b"\xff\xfb\x90\x00" + b"\0" * 413
    assert named(one + b"\0" * 500) is None and named(one + one) == "mp3"
    assert named(b"\xff\xfd\x90\x00" + b"\0" * 413 + b"\xff\xfd\x90\x00") == "mp2"
    adts = b"\xff\xf1\x50\x80\x02\x1f\xfc" + b"\0" * 9
    assert named(adts + b"\0" * 20) is None and named(adts + adts) == "aac"
    assert named(b"\x0b\x77\0\0\0\x40" + b"\0" * 50) == "ac3" and named(b"\x0b\x77\0\0\0\x80" + b"\0" * 50) == "eac3"
    assert named(b"\0\0\0\x14ftypqt  " + b"\0" * 50) == "mov" and named(b"\0\0\0\x14ftyp3gp4" + b"\0" * 50) == "3gp"
    # tags without end are not followed without end
    assert named(b"ID3\4\0\0\0\0\0\0" * 5000) is None


def test_advice_without_ffmpeg(noffmpeg, tmp_path):
    ogg = tone(tmp_path / "a.ogg", "ogg").read_bytes()
    chain = tmp_path / "chain.ogg"
    chain.write_bytes(ogg + ogg)
    cut = tmp_path / "cut.ogg"
    cut.write_bytes(ogg[:len(ogg) // 20])
    midi = tmp_path / "a.mid"
    midi.write_bytes(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + b"MTrk" + struct.pack(">I", 4) + b"\0\xff\x2f\0")
    jpeg = tmp_path / "a.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xe0\0\x10JFIF\0" + bytes(range(256)) * 4)
    a, b, c, d = [check_file(str(p)) for p in (chain, cut, midi, jpeg)]
    # ffmpeg reads a chained file, so that is what is missing. nothing reads the cut one
    assert (a["reason"], a["hint"]) == ("needs_ffmpeg", FFMPEG_FIX) and a["note"].startswith("ogg needs ffmpeg")
    assert (b["reason"], b["hint"], b["note"]) == ("cant_decode", None, "cant decode ogg, the file is damaged")
    assert (c["reason"], c["hint"]) == ("not_audio", None) and "midi" in c["note"]
    assert d["format"] is None and d["reason"] == "needs_ffmpeg" and d["note"].startswith("format not known")


def test_wav_size_off_by_less_than_a_frame(tmp_path):
    data = bytearray(tone(tmp_path / "a.wav", "wav").read_bytes())
    at = data.index(b"data") + 4
    size = struct.unpack_from("<I", data, at)[0]
    struct.pack_into("<I", data, at, size + 1)
    (tmp_path / "plus1.wav").write_bytes(data)
    r = check_file(str(tmp_path / "plus1.wav"))
    assert r["loads"] and r["reason"] is None and r["note"] is None
    struct.pack_into("<I", data, at, size + 2)
    (tmp_path / "plus2.wav").write_bytes(data)
    assert check_file(str(tmp_path / "plus2.wav"))["reason"] == "truncated"
