import json
import os
import shutil
import subprocess

import pytest

pydub = pytest.importorskip("pydub")
np = pytest.importorskip("numpy")
import pydub2

ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
need_ffmpeg = pytest.mark.skipif(not os.path.exists(ff), reason="no ffmpeg")


def stems(channels):
    t = np.arange(44100) / 44100
    data = np.stack([np.sin(2 * np.pi * 440 * t), 0.5 * np.sin(2 * np.pi * 660 * t)], 1)
    data = (data * 32767).astype(np.int16)
    return data if channels == 2 else data[:, 0]


def probe(path):
    out = subprocess.run([shutil.which("ffprobe") or ff.replace("ffmpeg", "ffprobe"), "-v", "quiet",
                          "-print_format", "json", "-show_streams", path], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return s["codec_name"], s.get("sample_fmt"), s["sample_rate"], s["channels"]


# CommonSeparator.write_audio_pydub
@need_ffmpeg
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("ext,depth", [("wav", 16), ("wav", 24), ("wav", 32), ("flac", 16), ("flac", 24), ("flac", 32)])
def test_write_lossless_stem(tmp_path, channels, ext, depth):
    data = stems(channels)
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = S(np.ascontiguousarray(data).reshape(-1).tobytes(), frame_rate=44100, sample_width=2, channels=channels)
        params = {"format": ext}
        if depth == 16:
            params["parameters"] = ["-sample_fmt", "s16"]
        else:
            params["parameters"] = ["-sample_fmt", "s32"]
            if ext == "wav":
                params["codec"] = "pcm_s24le" if depth == 24 else "pcm_s32le"
        path = str(tmp_path / (S.__module__ + "." + ext))
        audio.export(path, **params).close()
        back = S.from_file(path)
        out.append((probe(path), back.frame_rate, back.channels, back.sample_width, back.raw_data))
    assert out[0] == out[1]


@need_ffmpeg
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("ext,format,bitrate", [
    ("mp3", "mp3", "320k"), ("mp3", "mp3", "192k"), ("ogg", "ogg", None), ("opus", "opus", None),
    ("m4a", "mp4", None), ("m4a", "mp4", "192k"), ("mka", "matroska", None), ("aiff", "aiff", None),
])
def test_write_other_stem(tmp_path, channels, ext, format, bitrate):
    data = stems(channels)
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = S(np.ascontiguousarray(data).reshape(-1).tobytes(), frame_rate=44100, sample_width=2, channels=channels)
        params = {"format": format}
        if bitrate:
            params["bitrate"] = bitrate
        path = str(tmp_path / (S.__module__ + "." + ext))
        audio.export(path, **params).close()
        back = S.from_file(path)
        codec, _, rate, ch = probe(path)
        out.append((codec, rate, ch, back.frame_rate, back.channels, back.sample_width, abs(len(back) - 1000) < 150))
        if ext == "aiff":
            out[-1] += (back.raw_data,)
    assert out[0] == out[1]


# AudioChunker.split_audio and merge_chunks
@need_ffmpeg
@pytest.mark.parametrize("ext", ["flac", "wav", "mp3"])
def test_split_and_merge_chunks(tmp_path, ext):
    src = tmp_path / ("in." + ext)
    pydub2.AudioSegment(stems(2).tobytes(), frame_rate=44100, sample_width=2, channels=2).export(str(src), format=ext).close()
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        audio = S.from_file(str(src))
        total = len(audio)
        step = 300
        paths = []
        for i in range((total + step - 1) // step):
            path = str(tmp_path / ("%s.%d.%s" % (S.__module__, i, ext)))
            audio[i * step:min(i * step + step, total)].export(path, format=ext)
            paths.append(path)
        combined = S.empty()
        for path in paths:
            combined += S.from_file(path)
        merged = str(tmp_path / (S.__module__ + ".merged." + ext))
        combined.export(merged, format=ext).close()
        back = S.from_file(merged)
        seen = (len(paths), total, back.frame_rate, back.channels, back.sample_width)
        out.append(seen + ((back.raw_data,) if ext != "mp3" else (abs(len(back) - total) < 200 * len(paths),)))
    assert out[0] == out[1]


def test_empty_segment():
    out = []
    for S in (pydub.AudioSegment, pydub2.AudioSegment):
        e = S.empty()
        out.append((len(e), e.frame_rate, e.channels, e.sample_width, e.raw_data, (e + e).raw_data))
    assert out[0] == out[1]
