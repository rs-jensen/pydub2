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


def mp3_bytes(rate, channels):
    return pydub2.AudioSegment(make_wav(2, channels, rate=rate, secs=1.3)).export(format="mp3").read()


# alignment.audio._decode_mp3_to_mono_float32 and StreamPlayer._play_wav_chunk
@need_ffmpeg
@pytest.mark.parametrize("rate,channels", [(24000, 1), (44100, 2)])
def test_decode_mp3_chunk(rate, channels):
    data = mp3_bytes(rate, channels)
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        segment = S.from_file(io.BytesIO(data), format="mp3")
        out.append((segment.frame_rate, segment.channels, segment.sample_width, len(segment),
                    len(segment.get_array_of_samples()), len(segment.raw_data)))
    assert out[0] == out[1]


# GTTSEngine.synthesize: speed the voice up, then write a wav and read it back with wave
@pytest.mark.parametrize("speed,chunk,crossfade", [(1.25, 100, 10), (1.5, 150, 25), (0.9, 100, 10), (2.0, 50, 0)])
def test_gtts_speedup(tmp_path, speed, chunk, crossfade):
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = S(make_wav(2, 1, rate=24000, secs=1.3))
        audio = audio.speedup(playback_speed=speed, chunk_size=chunk, crossfade=crossfade)
        path = str(tmp_path / (S.__module__ + ".wav"))
        audio.export(path, format="wav")
        with wave.open(path, "rb") as w:
            out.append((w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())))
    assert out[0] == out[1]


# SystemEngine.synthesize: mediainfo tells aiff from wav, an aiff is converted in place
@need_ffmpeg
def test_system_engine_aiff(tmp_path):
    aiff = pydub2.AudioSegment(make_wav(2, 1, rate=22050)).export(format="aiff").read()
    out = []
    for m in (pydub, pydub2):
        path = str(tmp_path / (m.__name__ + ".aiff"))
        with open(path, "wb") as f:
            f.write(aiff)
        info = m.utils.mediainfo(path)
        audio = m.AudioSegment.from_file(path, format="aiff")
        audio.export(path + ".wav", format="wav")
        with open(path + ".wav", "rb") as f:
            out.append((info["format_name"], audio.frame_rate, audio.channels, audio.raw_data, f.read()))
    assert out[0] == out[1]


# example_fast_api convert_audio_to_pcm: mono, 16 bit and a target rate
@pytest.mark.parametrize("fn,rate,channels", [("from_wav", 16000, 1), ("from_wav", 44100, 2), ("from_wav", 24000, 1)])
@pytest.mark.parametrize("target", [24000, 16000])
def test_convert_to_pcm(fn, rate, channels, target):
    data = make_wav(2, channels, rate=rate)
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = getattr(S, fn)(io.BytesIO(data))
        seen = (audio.frame_rate, audio.channels, audio.sample_width)
        audio = audio.set_channels(1).set_sample_width(2)
        if audio.frame_rate != target:
            audio = audio.set_frame_rate(target)
        out.append((seen, audio.raw_data, audio.frame_rate, audio.channels, audio.sample_width))
    assert out[0] == out[1]


@need_ffmpeg
@pytest.mark.parametrize("fn,fmt", [("from_mp3", "mp3"), ("from_ogg", "ogg")])
def test_convert_lossy_to_pcm(fn, fmt):
    data = pydub2.AudioSegment(make_wav(2, 2, rate=22050)).export(format=fmt).read()
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = getattr(S, fn)(io.BytesIO(data)).set_channels(1).set_sample_width(2).set_frame_rate(24000)
        out.append((audio.frame_rate, audio.channels, audio.sample_width, len(audio)))
    assert out[0] == out[1]


# the example servers catch Exception around undecodable input
@pytest.mark.parametrize("data", [b"garbage" * 10, b""])
def test_undecodable_input_is_an_exception(data):
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        try:
            S.from_mp3(io.BytesIO(data))
        except Exception as e:
            out.append(type(e).__name__)
        else:
            out.append(None)
    assert out[0] == out[1] and out[0] is not None
