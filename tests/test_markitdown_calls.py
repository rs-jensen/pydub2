import io
import os
import shutil
import wave

import pytest

pydub = pytest.importorskip("pydub")
import pydub2

from test_audio_segment import make_wav

ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
need_ffmpeg = pytest.mark.skipif(not os.path.exists(ff), reason="no ffmpeg")


def source(fmt):
    wav = make_wav(2, 1, rate=22050, secs=1.5)
    if fmt == "wav":
        return wav
    return pydub2.AudioSegment(wav).export(format=fmt if fmt != "mp4" else "ipod").read()


# transcribe_audio: decode the stream, write a wav to a BytesIO and rewind it for speech_recognition
@pytest.mark.parametrize("fmt", [
    "wav",
    pytest.param("mp3", marks=need_ffmpeg),
    pytest.param("mp4", marks=need_ffmpeg),
])
def test_transcribe_audio(fmt):
    data = source(fmt)
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        stream = io.BytesIO(data)
        audio = S.from_file(stream, format=fmt)
        target = io.BytesIO()
        audio.export(target, format="wav")
        target.seek(0)
        with wave.open(target) as w:
            out.append((audio.frame_rate, audio.channels, audio.sample_width, len(audio),
                        w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth(), target.tell()))
    assert out[0] == out[1]
