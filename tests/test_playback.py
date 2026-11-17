import _thread
import ast
import gc
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types

import pytest

from pydub2 import playback
from pydub2.generators import Sine

seg = Sine(440).to_audio_segment(duration=300)
posix = pytest.mark.skipif(os.name == "nt", reason="needs a signal that Windows does not have")


def alive(pid):
    """True while the process runs. A zombie counts as gone, the tests look at returncode for those."""
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            k.CloseHandle(h)
    stat = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(stat.strip()) and "Z" not in stat


def wait_for(cond, t=5):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def interrupt_later(delay):
    # a real SIGINT, interrupt_main does not wake a blocking wait
    send = (lambda: os.kill(os.getpid(), signal.SIGINT)) if os.name != "nt" else _thread.interrupt_main
    threading.Timer(delay, send).start()


FAKE = """import os, signal, sys, time
if os.environ.get("FAKE_STUBBORN"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open({rec!r} + ".tmp", "w") as f:
    f.write(repr((os.getpid(), [{tag!r}] + sys.argv[1:])))
os.replace({rec!r} + ".tmp", {rec!r})
time.sleep(float(os.environ.get("FAKE_SECONDS", "30")))
"""


def make_fake(folder, rec, tag):
    """A ffplay in `folder` that records its pid and arguments in the file `rec`, then sleeps."""
    folder.mkdir()
    script = folder / "ffplay.py"
    script.write_text(FAKE.format(rec=str(rec), tag=tag))
    if os.name == "nt":
        (folder / "ffplay.exe").write_bytes(b"")  # found by which, run through the Popen in fake_ffplay
    else:
        (folder / "ffplay").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        (folder / "ffplay").chmod(0o755)


@pytest.fixture
def fake_ffplay(tmp_path, monkeypatch):
    """A ffplay on PATH that records pid and arguments, then sleeps. No other backend is importable."""
    rec_file = tmp_path / "rec"
    make_fake(tmp_path / "bin", rec_file, "public")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setitem(sys.modules, "simpleaudio", None)
    monkeypatch.setitem(sys.modules, "pyaudio", None)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    real = subprocess.Popen
    started = []

    def popen(cmd, *a, **k):
        if os.name == "nt" and cmd[0].lower().endswith("ffplay.exe"):
            cmd = [sys.executable, cmd[0][:-4] + ".py"] + cmd[1:]  # an .exe cannot be a script
        proc = real(cmd, *a, **k)
        started.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", popen)

    def rec():
        assert wait_for(rec_file.exists)
        pid, args = ast.literal_eval(rec_file.read_text())
        return pid, args[1:]
    rec.tag = lambda: ast.literal_eval(rec_file.read_text())[1][0]
    rec.started = started
    rec.temp_files = lambda: os.listdir(tmp_path / "tmp")
    return rec


def test_ffplay_stop(fake_ffplay):
    h = playback.play_async(seg)
    pid, args = fake_ffplay()
    assert args[:2] == ["-nodisp", "-autoexit"] and os.path.exists(args[-1])
    assert h.is_playing() and h.wait(0.05) is False
    start = time.time()
    h.stop()
    assert time.time() - start < 2
    assert not h.is_playing() and h.wait() is True
    assert not alive(pid) and not os.path.exists(args[-1]) and fake_ffplay.started[0].returncode is not None
    h.stop()  # twice is fine


def test_ffplay_wait_returns_when_it_ends(fake_ffplay, monkeypatch):
    monkeypatch.setenv("FAKE_SECONDS", "0.3")
    h = playback.play_async(seg)
    pid, args = fake_ffplay()
    assert h.wait(5) is True and not h.is_playing()
    assert not os.path.exists(args[-1]) and not alive(pid)
    h.stop()


def test_context_manager_stops(fake_ffplay):
    with playback.play_async(seg) as h:
        pid, args = fake_ffplay()
    assert not h.is_playing() and not alive(pid) and not os.path.exists(args[-1])


def test_dropped_handle_leaves_no_process(fake_ffplay):
    h = playback.play_async(seg)
    pid, args = fake_ffplay()
    del h
    gc.collect()
    assert wait_for(lambda: not alive(pid))
    assert wait_for(lambda: not os.path.exists(args[-1]))


def test_ffplay_from_converter_folder(fake_ffplay, tmp_path, monkeypatch):
    private = tmp_path / "private"
    make_fake(private, tmp_path / "rec", "private")
    monkeypatch.setenv("PATH", str(tmp_path / "nonexistent"))
    s = seg[:]
    s.converter = str(private / "ffmpeg")
    with playback.play_async(s):
        fake_ffplay()
        assert fake_ffplay.tag() == "private"


def test_blocking_play_uses_ffplay_and_cleans_up(fake_ffplay, monkeypatch):
    monkeypatch.setenv("FAKE_SECONDS", "0.2")
    playback.play(seg)
    pid, args = fake_ffplay()
    assert args[:2] == ["-nodisp", "-autoexit"] and not os.path.exists(args[-1]) and not alive(pid)


@posix
def test_interrupt_in_blocking_play(fake_ffplay):
    interrupt_later(0.5)
    with pytest.raises(KeyboardInterrupt):
        playback.play(seg)
    pid, args = fake_ffplay()
    assert wait_for(lambda: not alive(pid)) and not os.path.exists(args[-1])


def in_thread(f, t=10):
    """Run f in a thread, True when it came back within t seconds."""
    th = threading.Thread(target=f, daemon=True)
    th.start()
    th.join(t)
    return not th.is_alive()


def test_stop_returns_when_the_temp_file_is_already_gone(fake_ffplay):
    h = playback.play_async(seg)
    pid, args = fake_ffplay()
    os.remove(args[-1])
    assert in_thread(h.stop) and not h.is_playing() and not alive(pid)


@posix
def test_stop_kills_a_player_that_ignores_sigterm(fake_ffplay, monkeypatch):
    monkeypatch.setenv("FAKE_STUBBORN", "1")
    h = playback.play_async(seg)
    pid, args = fake_ffplay()
    assert in_thread(h.stop) and not alive(pid) and fake_ffplay.temp_files() == []


def test_no_thread_for_the_watcher_leaves_nothing(fake_ffplay, monkeypatch):
    def start(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError):
        playback.play_async(seg)
    assert fake_ffplay.started[0].returncode is not None and fake_ffplay.temp_files() == []


def test_ffplay_missing_leaves_no_temp_file(fake_ffplay, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nonexistent"))
    with pytest.warns(RuntimeWarning), pytest.raises(FileNotFoundError):
        playback.play_async(seg)
    assert fake_ffplay.temp_files() == [] and fake_ffplay.started == []


def test_stop_and_wait_from_many_threads(fake_ffplay):
    for when in ("at once", "after the start", "after the end"):
        h = playback.play_async(seg)
        if when != "at once":
            fake_ffplay()
        if when == "after the end":
            h.stop()
        assert in_thread(lambda: [t.join() for t in [threading.Thread(target=f) for f in [h.stop, h.wait] * 4]
                                  if not t.start()])
        assert not h.is_playing() and fake_ffplay.temp_files() == []
    assert all(p.returncode is not None for p in fake_ffplay.started)


def test_many_handles_leak_nothing(fake_ffplay):
    fds = "/dev/fd" if os.path.isdir("/dev/fd") else None
    playback.play_async(seg).stop()
    before = threading.active_count(), fds and len(os.listdir(fds))
    for i in range(200):
        h = playback.play_async(seg)
        if i % 2:
            h.stop()
        del h  # every other one is only dropped
    assert wait_for(lambda: threading.active_count() == before[0])
    assert before == (threading.active_count(), fds and len(os.listdir(fds)))
    assert fake_ffplay.temp_files() == [] and all(p.returncode is not None for p in fake_ffplay.started)


EXITING = """import sys
sys.modules["simpleaudio"] = sys.modules["pyaudio"] = None
from pydub2 import playback
from pydub2.generators import Sine
keep = playback.play_async(Sine(440).to_audio_segment(duration=300))
class Cycle:
    def __init__(self):
        self.me, self.h = self, playback.play_async(Sine(440).to_audio_segment(duration=300))
Cycle()
"""


@posix  # the Popen of fake_ffplay is not in the child
def test_interpreter_exit_stops_the_players(fake_ffplay, tmp_path):
    env = dict(os.environ, TMPDIR=str(tmp_path / "tmp"), PYTHONPATH=os.pathsep.join(sys.path))
    start = time.time()
    r = subprocess.run([sys.executable, "-c", EXITING], env=env, capture_output=True, text=True, timeout=60)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "") and time.time() - start < 10
    assert fake_ffplay.temp_files() == []
    assert subprocess.run(["/bin/ps", "-A", "-o", "args="], capture_output=True, text=True).stdout.count(
        str(tmp_path / "bin" / "ffplay.py")) == 0


def test_import_starts_nothing():
    code = ("import sys, threading, pydub2.playback\n"
            "assert threading.active_count() == 1 and not {'simpleaudio', 'pyaudio'} & set(sys.modules)")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    assert subprocess.run([sys.executable, "-c", code], env=env).returncode == 0


def stub_simpleaudio(monkeypatch, length):
    made = []

    class Play:
        def __init__(self):
            self.t = time.time() + length
            self.stopped = False

        def is_playing(self):
            return not self.stopped and time.time() < self.t

        def stop(self):
            self.stopped = True

        def wait_done(self):
            while self.is_playing():
                time.sleep(0.01)

    def play_buffer(*a, **k):
        made.append((k, Play()))
        return made[-1][1]

    mod = types.ModuleType("simpleaudio")
    mod.play_buffer = play_buffer
    monkeypatch.setitem(sys.modules, "simpleaudio", mod)
    return made


def test_simpleaudio_handle(monkeypatch):
    made = stub_simpleaudio(monkeypatch, 30)
    h = playback.play_async(seg)
    assert made[0][0] == dict(num_channels=1, bytes_per_sample=2, sample_rate=seg.frame_rate)
    assert h.is_playing() and h.wait(0.05) is False
    h.stop()
    h.stop()
    assert not h.is_playing() and made[0][1].stopped


def test_simpleaudio_ends_by_itself(monkeypatch):
    stub_simpleaudio(monkeypatch, 0.1)
    assert playback.play_async(seg).wait(5) is True


def test_simpleaudio_blocking_play_and_interrupt(monkeypatch):
    made = stub_simpleaudio(monkeypatch, 0.1)
    playback.play(seg)
    assert not made[0][1].is_playing()
    made = stub_simpleaudio(monkeypatch, 30)
    interrupt_later(0.3)
    playback.play(seg)  # play stops the sound and swallows the interrupt, as pydub does
    assert made[0][1].stopped


def stub_pyaudio(monkeypatch, delay, broken=()):
    log = {"chunks": 0, "closed": False, "terminated": False}

    def fail(name):
        if name in broken:
            raise OSError(name)

    class Stream:
        def write(self, data):
            log["chunks"] += 1
            time.sleep(delay)

        def stop_stream(self):
            fail("stop_stream")

        def close(self):
            log["closed"] = True

    class PyAudio:
        def get_format_from_width(self, w):
            return w

        def open(self, **kw):
            fail("open")
            return Stream()

        def terminate(self):
            log["terminated"] = True

    mod = types.ModuleType("pyaudio")
    mod.PyAudio = PyAudio
    monkeypatch.setitem(sys.modules, "simpleaudio", None)
    monkeypatch.setitem(sys.modules, "pyaudio", mod)
    return log


def test_pyaudio_stop(monkeypatch):
    log = stub_pyaudio(monkeypatch, 0.05)
    h = playback.play_async(Sine(440).to_audio_segment(duration=10000))
    assert h.is_playing()
    time.sleep(0.2)
    h.stop()
    h.stop()
    assert not h.is_playing() and log["closed"] and log["terminated"]
    assert log["chunks"] < 20


def test_pyaudio_ends_by_itself(monkeypatch):
    log = stub_pyaudio(monkeypatch, 0)
    with playback.play_async(seg) as h:
        assert h.wait(5) is True
    assert log["chunks"] == 3 and log["closed"]


def test_pyaudio_is_released_when_the_stream_does_not_open(monkeypatch):
    log = stub_pyaudio(monkeypatch, 0, broken=["open"])
    with pytest.raises(OSError):
        playback.play_async(seg)
    assert log["terminated"]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_stop_returns_when_a_backend_fails_in_its_thread(monkeypatch):
    threads = threading.active_count()
    stub_pyaudio(monkeypatch, 0.05, broken=["stop_stream"])
    h = playback.play_async(seg)
    assert in_thread(h.stop) and not h.is_playing()
    made = stub_simpleaudio(monkeypatch, 30)

    def wait_done():
        raise RuntimeError("device lost")
    made_play = playback._play_with_simpleaudio
    monkeypatch.setattr(playback, "_play_with_simpleaudio",
                        lambda seg: setattr(made_play(seg), "wait_done", wait_done) or made[0][1])
    h = playback.play_async(seg)
    assert in_thread(h.stop) and not h.is_playing()
    assert wait_for(lambda: threading.active_count() == threads)  # their tracebacks belong to this test
