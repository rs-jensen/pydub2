import io
import math
import os
import struct
import wave

import pytest

from pydub2 import _ffmpeg

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
