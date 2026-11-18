import _thread
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

import pytest

import _pydub2
import pydub2
from pydub2 import AudioSegment

MIN = 60 * 44100 * 4  # a minute of 44.1 khz stereo


def send():
    if os.name == "nt":
        _thread.interrupt_main()
    else:
        os.kill(os.getpid(), signal.SIGINT)


CALLS = {
    "detect_speech": (20, lambda d: _pydub2.detect_speech(d, 44100, 2, 2, 0.5, 250, 100)),
    "opus": (10, lambda d: _pydub2.encode(d, 44100, 2, 2, "opus")),
    "ogg": (20, lambda d: _pydub2.encode(d, 44100, 2, 2, "ogg")),
    "compress_32_bit": (1, lambda d: _pydub2.compress(d, 4, 2, 1000.0, 2.0, 441.0, 441.0, 4410)),
}


@pytest.mark.parametrize("name", CALLS)
def test_ctrl_c_stops_a_long_call(name):
    if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        pytest.skip("SIGINT is ignored, pytest was started in the background")
    minutes, call = CALLS[name]
    data = os.urandom(MIN) * minutes
    # the whole call would take this long on this machine right now, the time scales with the input
    start = time.perf_counter()
    call(data[:MIN])
    full = (time.perf_counter() - start) * minutes

    timer = threading.Timer(full / 10, send)
    timer.start()
    start = time.perf_counter()
    try:
        with pytest.raises(KeyboardInterrupt):
            call(data)
    finally:
        timer.cancel()
    assert time.perf_counter() - start < full / 2
    # nothing is left half done
    assert _pydub2.encode(data[:MIN], 44100, 2, 2, "opus")[:4] == b"OggS"
    assert _pydub2.loudness(data[:MIN], 44100, 2, 2) > -100


@pytest.fixture(scope="module")
def noise():
    return os.urandom(MIN)


def interrupted(call, full):
    # the interrupt comes a tenth into the time the whole call needs, it has to end well before half of it
    timer = threading.Timer(full / 10, send)
    timer.start()
    start = time.perf_counter()
    try:
        with pytest.raises(KeyboardInterrupt):
            call()
    finally:
        timer.cancel()
    assert time.perf_counter() - start < full / 2


@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_ctrl_c_stops_a_long_decode(fmt, noise, tmp_path):
    if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        pytest.skip("SIGINT is ignored, pytest was started in the background")
    path = str(tmp_path / "long")
    with open(path, "wb") as f:
        f.write(_pydub2.encode(noise * 10, 44100, 2, 2, fmt))
    start = time.perf_counter()
    whole = AudioSegment.from_file(path, format=fmt)
    full = time.perf_counter() - start
    interrupted(lambda: AudioSegment.from_file(path, format=fmt), full)
    assert AudioSegment.from_file(path, format=fmt)._data == whole._data


@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_ctrl_c_stops_a_long_export(fmt, noise, tmp_path):
    if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        pytest.skip("SIGINT is ignored, pytest was started in the background")
    one = AudioSegment(noise, sample_width=2, frame_rate=44100, channels=2)
    start = time.perf_counter()
    one.export(str(tmp_path / "one"), format=fmt).close()
    full = (time.perf_counter() - start) * 20
    out = tmp_path / "out"
    interrupted(lambda: (one * 20).export(str(out), format=fmt), full)
    assert not out.exists()


@pytest.mark.skipif(os.name == "nt" or not shutil.which("pgrep"),
                    reason="interrupt_main does not wake a blocked wait on Windows, and there is no pgrep")
@pytest.mark.parametrize("what", ["export", "decode"])
def test_ctrl_c_stops_ffmpeg(what, noise, tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("no ffmpeg")
    if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        pytest.skip("SIGINT is ignored, pytest was started in the background")
    mark = "interrupt-" + os.urandom(6).hex()
    # a wrapper script that starts a helper of its own once the "go" file is there, it has to go with ffmpeg
    wrapper = tmp_path / "wrapper"
    wrapper.write_text('#!/bin/sh\n[ -e %s/go ] && sh -c "sleep 5; exit 0 # %s" </dev/null >/dev/null 2>&1 &\n%s "$@"\nexit $?\n'
                       % (tmp_path, mark, ffmpeg))
    wrapper.chmod(0o755)
    monkeypatch.setattr(AudioSegment, "converter", str(wrapper))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(pydub2, "strict", True)
    one = AudioSegment(noise, sample_width=2, frame_rate=44100, channels=2)
    if what == "export":
        def call(minutes):
            (one * minutes).export(str(tmp_path / "out"), format="ogg", codec="libvorbis", tags={"title": mark})
    else:
        # chained streams, so the long file is the short one many times
        ogg = _pydub2.encode(noise, 44100, 2, 2, "ogg")
        for minutes in (1, 20):
            (tmp_path / ("%d.ogg" % minutes)).write_bytes(ogg * minutes)

        def call(minutes):
            AudioSegment.from_file(str(tmp_path / ("%d.ogg" % minutes)), format="ogg", parameters=["-metadata", mark])
    start = time.perf_counter()
    call(1)
    full = (time.perf_counter() - start) * 20
    (tmp_path / "out").unlink(missing_ok=True)
    (tmp_path / "go").touch()
    interrupted(lambda: call(20), full)
    assert subprocess.run(["pgrep", "-f", mark]).returncode == 1
    assert not (tmp_path / "out").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("tmp")]


def test_other_threads_are_not_interrupted():
    data = os.urandom(MIN // 6)
    out = []
    t = threading.Thread(target=lambda: out.append(_pydub2.detect_speech(data, 44100, 2, 2, 0.5, 250, 100)))
    t.start()
    t.join()
    assert len(out) == 1
