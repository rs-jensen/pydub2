import _thread
import os
import signal
import threading
import time

import pytest

import _pydub2

MIN = 60 * 44100 * 4  # a minute of 44.1 khz stereo


def send():
    if os.name == "nt":
        _thread.interrupt_main()
    else:
        os.kill(os.getpid(), signal.SIGINT)


CALLS = {
    "detect_speech": (2, lambda d: _pydub2.detect_speech(d, 44100, 2, 2, 0.5, 250, 100)),
    "opus": (2, lambda d: _pydub2.encode(d, 44100, 2, 2, "opus")),
    "ogg": (4, lambda d: _pydub2.encode(d, 44100, 2, 2, "ogg")),
}


@pytest.mark.parametrize("name", CALLS)
def test_ctrl_c_stops_a_long_call(name):
    minutes, call = CALLS[name]
    data = os.urandom(minutes * MIN)
    sent = []

    def fire():
        sent.append(time.time())
        send()

    timer = threading.Timer(0.5, fire)
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            call(data)
    finally:
        timer.cancel()
    assert time.time() - sent[0] < 1
    # nothing is left half done
    assert _pydub2.encode(data[:MIN], 44100, 2, 2, "opus")[:4] == b"OggS"
    assert _pydub2.loudness(data[:MIN], 44100, 2, 2) > -100


def test_other_threads_are_not_interrupted():
    data = os.urandom(MIN // 6)
    out = []
    t = threading.Thread(target=lambda: out.append(_pydub2.detect_speech(data, 44100, 2, 2, 0.5, 250, 100)))
    t.start()
    t.join()
    assert len(out) == 1
