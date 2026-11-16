import importlib
import io
import os
import pkgutil
import random
import shutil
import struct
import subprocess
from array import array

import pytest

from pydub2 import AudioSegment, _ffmpeg
from pydub2 import utils as utils2

pydub = pytest.importorskip("pydub")
ffmpeg = shutil.which("ffmpeg")
need_ffmpeg = pytest.mark.skipif(ffmpeg is None or shutil.which("ffprobe") is None,
                                 reason="needs ffmpeg and ffprobe for the oracle")


def make(path, *args):
    subprocess.run([ffmpeg, "-y", "-v", "quiet", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ac", "2"] + list(args) + [str(path)], check=True)
    return path.read_bytes()


def noise(width, channels=2, frames=3000):
    rng = random.Random(width * 10 + channels)
    hi = 2 ** (8 * width - 1)
    vals = [rng.randrange(-hi, hi) for _ in range(frames * channels)]
    vals[:4] = [-hi, hi - 1, -hi, hi - 1]
    return array({2: "h", 4: "i"}[width], vals).tobytes()


def no_ffprobe(monkeypatch, tmp_path):
    bin = tmp_path / "bin"
    bin.mkdir()
    os.symlink(ffmpeg, bin / "ffmpeg")
    monkeypatch.setenv("PATH", str(bin))


@need_ffmpeg
def test_moov_at_the_end_from_bytes(tmp_path):
    data = make(tmp_path / "a.m4a", "-c:a", "aac")
    assert data.find(b"moov") > data.find(b"mdat")
    wav = _ffmpeg.to_wav("ffmpeg", None, data)
    assert len(wav) > 1000
    for fmt in (None, "m4a"):
        a = pydub.AudioSegment.from_file(io.BytesIO(data), format=fmt)
        b = AudioSegment.from_file(io.BytesIO(data), format=fmt)
        assert (a.raw_data, a.sample_width) == (b.raw_data, b.sample_width)


@need_ffmpeg
@pytest.mark.parametrize("name,args", [("a.wma", ["-c:a", "wmav2"]), ("a.webm", ["-c:a", "libvorbis"]),
                                       ("a.m4a", ["-c:a", "aac"])])
@pytest.mark.parametrize("probe", [True, False])
def test_width_follows_the_stream(tmp_path, monkeypatch, name, args, probe):
    path = tmp_path / name
    make(path, *args)
    a = pydub.AudioSegment.from_file(str(path))
    if not probe:
        no_ffprobe(monkeypatch, tmp_path)
    b = AudioSegment.from_file(str(path))
    assert (a.sample_width, a.raw_data) == (b.sample_width, b.raw_data)


def wav_bits(wav):
    i = wav.find(b"fmt ")
    return struct.unpack_from("<H", wav, i + 22)[0]


EXPORTS = [
    ("wav", None, ["-sample_fmt", "s16"], True),
    ("wav", "pcm_s16le", None, True),
    ("wav", "pcm_s24le", None, True),
    ("wav", "pcm_s32le", ["-sample_fmt", "s32"], True),
    ("wav", "pcm_s16le", ["-ar", "44100", "-ac", "2"], True),
    ("flac", None, ["-sample_fmt", "s16"], True),
    ("flac", None, ["-ar", "44100"], False),
    ("wav", None, ["-ac", "1"], False),
    ("wav", None, ["-ar", "22050"], False),
    ("flac", None, ["-sample_fmt", "s32"], False),
]


@need_ffmpeg
@pytest.mark.parametrize("width", [2, 4])
@pytest.mark.parametrize("format,codec,parameters,native", EXPORTS)
def test_pcm_export_options(monkeypatch, width, format, codec, parameters, native):
    data = noise(width)
    a = pydub.AudioSegment(data, sample_width=width, frame_rate=44100, channels=2)
    expected = a.export(io.BytesIO(), format=format, codec=codec, parameters=parameters).read()
    b = AudioSegment(data, sample_width=width, frame_rate=44100, channels=2)
    if native:
        monkeypatch.setattr(_ffmpeg, "run", None)
    got = b.export(io.BytesIO(), format=format, codec=codec, parameters=parameters).read()
    x, y = AudioSegment.from_file(io.BytesIO(expected)), AudioSegment.from_file(io.BytesIO(got))
    assert (x.channels, x.frame_rate, x.sample_width, x.raw_data) == (y.channels, y.frame_rate, y.sample_width, y.raw_data)
    if format == "wav":
        assert wav_bits(expected) == wav_bits(got)


@need_ffmpeg
def test_mediainfo_without_ffprobe(tmp_path, monkeypatch):
    seg = AudioSegment(noise(2), sample_width=2, frame_rate=44100, channels=2)
    paths = []
    for name, kw in [("a.wav", {}), ("b.wav", {"codec": "pcm_s24le"}), ("c.flac", {}), ("d.ogg", {}),
                     ("e.opus", {}), ("f.mp3", {})]:
        paths.append(str(tmp_path / name))
        seg.export(paths[-1], format=name[2:], **kw).close()
    keys = ["codec_name", "codec_type", "codec_tag_string", "format_name", "sample_rate", "channels",
            "sample_fmt", "bits_per_sample"]
    want = [(pydub.utils.mediainfo(p), pydub.utils.mediainfo_json(p)) for p in paths]
    no_ffprobe(monkeypatch, tmp_path)
    for p, (flat, js) in zip(paths, want):
        got = utils2.mediainfo(p)
        assert {k: got.get(k) for k in keys} == {k: flat.get(k) for k in keys}, p
        s1, s2 = js["streams"][0], utils2.mediainfo_json(p)["streams"][0]
        assert {k: s2.get(k) for k in keys[4:]} == {k: s1.get(k) for k in keys[4:]}, p
    with open(paths[0], "rb") as f:
        assert utils2.mediainfo_json(f)["streams"][0]["codec_name"] == "pcm_s16le"


def test_mediainfo_chunks_and_tags_without_ffprobe(tmp_path, monkeypatch):
    no_ffprobe(monkeypatch, tmp_path)
    wav = AudioSegment.silent(100, 8000).export(io.BytesIO(), format="wav").getvalue()
    list_chunk = b"LIST" + struct.pack("<I", 12) + b"INFOfmt abcd"
    got = utils2.mediainfo_json(io.BytesIO(wav[:12] + list_chunk + wav[12:]))["streams"][0]
    assert (got["codec_name"], got["sample_rate"], got["channels"]) == ("pcm_s16le", "8000", 1)
    flac = AudioSegment.silent(100, 8000).export(io.BytesIO(), format="flac").getvalue()
    tag = b"ID3\x03\0\0\0\0\0\x05abcde"
    assert utils2.mediainfo_json(io.BytesIO(tag + flac))["streams"][0]["codec_name"] == "flac"


def public(mod):
    return [n for n in dir(mod) if not n.startswith("__")]


def test_every_pydub_name_exists():
    # audioop and pyaudioop are what the C++ core replaces
    for m in pkgutil.iter_modules(pydub.__path__):
        if m.name == "pyaudioop":
            continue
        try:
            old = importlib.import_module("pydub." + m.name)
        except ImportError:
            continue
        new = importlib.import_module("pydub2." + m.name)
        assert [n for n in public(old) if n != "audioop" and not hasattr(new, n)] == [], m.name
