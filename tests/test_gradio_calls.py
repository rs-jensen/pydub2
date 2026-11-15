import array
import io
import os
import shutil

import pytest

pydub = pytest.importorskip("pydub")
import pydub2

from test_audio_segment import make_wav, same, same_mp3

ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


# processing_utils.audio_from_file
@pytest.mark.parametrize("width,channels", [(2, 1), (2, 2), (4, 2)])
def test_audio_from_file(tmp_path, width, channels):
    path = tmp_path / "x.wav"
    path.write_bytes(make_wav(width, channels))
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = S.from_file(str(path), format="wav")
        duration = len(audio)
        audio = audio[int(duration * 0.1):int(duration * 0.75)]
        out.append((audio.frame_rate, audio.channels, len(audio), audio.get_array_of_samples()))
    assert out[0] == out[1]


# processing_utils.audio_to_file
@pytest.mark.parametrize("format", ["wav", "mp3"])
def test_audio_to_file(tmp_path, format):
    if format == "mp3" and not os.path.exists(ff):
        pytest.skip("no ffmpeg")
    data = array.array("h", pydub2.AudioSegment(make_wav(2, 2)).raw_data)
    files = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = S(data.tobytes(), frame_rate=8000, sample_width=data.itemsize, channels=2)
        name = str(tmp_path / (S.__module__ + "." + format))
        file = audio.export(name, format=format)
        file.close()
        files.append(open(name, "rb").read())
    if format == "mp3":
        same_mp3(*files)
    else:
        assert files[0] == files[1]


# audio_stream_encoder
def test_stream_encoder():
    wav = make_wav(4, 2)
    a = pydub.AudioSegment.from_file(io.BytesIO(wav)).set_sample_width(2)
    b = pydub2.AudioSegment.from_file(io.BytesIO(wav)).set_sample_width(2)
    same(a, b)
    assert (a.raw_data, a.frame_rate, a.channels) == (b.raw_data, b.frame_rate, b.channels)
