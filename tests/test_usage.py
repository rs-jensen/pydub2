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
from pydub2.exceptions import CouldntDecodeError

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


def no_ffprobe(monkeypatch):
    which = utils2.which
    monkeypatch.setattr(utils2, "which", lambda p: None if os.path.basename(p) in ("ffprobe", "avprobe") else which(p))


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
        no_ffprobe(monkeypatch)
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
    ("flac", None, ["-ar", "44100"], True),
    ("wav", "pcm_s24le", ["-sample_fmt", "s32"], True),
    ("wav", "pcm_s32le", None, True),
    ("flac", None, ["-sample_fmt", "s32"], True),
    ("flac", "flac", None, True),
    ("flac", "flac", ["-sample_fmt", "s16"], True),
    ("flac", "flac", ["-sample_fmt", "s32"], True),
    ("wav", None, ["-ac", "1"], False),
    ("wav", None, ["-ar", "22050"], False),
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
@pytest.mark.parametrize("frames", [5, 6])
def test_24_bit_wav_pads_odd_data_like_ffmpeg(monkeypatch, frames):
    data = noise(2, 1, frames)
    expected = pydub.AudioSegment(data, sample_width=2, frame_rate=8000, channels=1).export(
        io.BytesIO(), format="wav", codec="pcm_s24le").read()
    monkeypatch.setattr(_ffmpeg, "run", None)
    got = AudioSegment(data, sample_width=2, frame_rate=8000, channels=1).export(
        io.BytesIO(), format="wav", codec="pcm_s24le").read()
    assert got[got.index(b"data"):] == expected[expected.index(b"data"):]
    assert struct.unpack_from("<I", got, 4)[0] == len(got) - 8
    assert len(got) % 2 == 0


SEPARATOR = [
    ("wav", {"parameters": ["-sample_fmt", "s16"]}),
    ("wav", {"codec": "pcm_s24le", "parameters": ["-sample_fmt", "s32"]}),
    ("wav", {"codec": "pcm_s32le", "parameters": ["-sample_fmt", "s32"]}),
    ("flac", {"parameters": ["-sample_fmt", "s16"]}),
    ("flac", {"parameters": ["-sample_fmt", "s32"]}),
]


@need_ffmpeg
@pytest.mark.parametrize("bitrate", [None, "320k"])
@pytest.mark.parametrize("width", [2, 4])
@pytest.mark.parametrize("format,kw", SEPARATOR)
def test_without_ffmpeg_like_audio_separator(monkeypatch, width, format, kw, bitrate):
    data = noise(width)
    a = pydub.AudioSegment(data, sample_width=width, frame_rate=44100, channels=2)
    expected = a.export(io.BytesIO(), format=format, bitrate=bitrate, **kw).read()
    monkeypatch.setattr(_ffmpeg, "run", None)
    got = AudioSegment(data, sample_width=width, frame_rate=44100, channels=2).export(
        io.BytesIO(), format=format, bitrate=bitrate, **kw).read()
    x, y = AudioSegment.from_file(io.BytesIO(expected)), AudioSegment.from_file(io.BytesIO(got))
    assert (x.sample_width, x.raw_data) == (y.sample_width, y.raw_data)
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
    no_ffprobe(monkeypatch)
    for p, (flat, js) in zip(paths, want):
        got = utils2.mediainfo(p)
        assert {k: got.get(k) for k in keys} == {k: flat.get(k) for k in keys}, p
        s1, s2 = js["streams"][0], utils2.mediainfo_json(p)["streams"][0]
        assert {k: s2.get(k) for k in keys[4:]} == {k: s1.get(k) for k in keys[4:]}, p
    with open(paths[0], "rb") as f:
        assert utils2.mediainfo_json(f)["streams"][0]["codec_name"] == "pcm_s16le"


@need_ffmpeg
def test_mediainfo_of_m4a_without_ffprobe(tmp_path, monkeypatch):
    path = tmp_path / "a.m4a"
    make(path, "-c:a", "aac")
    want = pydub.utils.mediainfo_json(str(path))["streams"][0]
    no_ffprobe(monkeypatch)
    for got in (utils2.mediainfo_json(str(path))["streams"][0], utils2.mediainfo(str(path))):
        assert got["codec_name"] == "aac"
        assert str(got["bits_per_sample"]) == str(want["bits_per_sample"])


def test_mediainfo_chunks_and_tags_without_ffprobe(tmp_path, monkeypatch):
    no_ffprobe(monkeypatch)
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


@need_ffmpeg
def test_corrupt_mp3_message():
    junk = b"\xff\xfb\x90\x00" + random.Random(1).randbytes(6000)
    with pytest.raises(pydub.exceptions.CouldntDecodeError) as old:
        pydub.AudioSegment.from_file(io.BytesIO(junk), format="mp3")
    msgs = []
    for _ in range(2):
        with pytest.raises(CouldntDecodeError) as new:
            AudioSegment.from_file(io.BytesIO(junk), format="mp3")
        msgs.append(str(new.value))
    # all of ffmpeg's output like pydub, and a last line without the addresses that change every run
    assert msgs[0].splitlines()[-1] == msgs[1].splitlines()[-1]
    assert msgs[0].splitlines()[0] == str(old.value).splitlines()[0]
    assert msgs[0].splitlines()[-1].startswith("ffmpeg said: ") and "0x" not in msgs[0].splitlines()[-1]
    assert "Output from ffmpeg/avlib:" in msgs[0] and "mp3" in msgs[0]


# root and Windows ignore the file mode
needs_mode = pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="the file mode does not stop root or Windows")


def error_of(call):
    try:
        call()
    except Exception as e:
        return type(e)


@need_ffmpeg
@needs_mode
@pytest.mark.parametrize("ext", ["wav", "flac", "mp3", "ogg", "opus"])
@pytest.mark.parametrize("hide", ["file", "folder"])
def test_unreadable_input_raises_what_pydub_raises(tmp_path, ext, hide):
    folder = tmp_path / "in"
    folder.mkdir()
    path = folder / ("x." + ext)
    make(path)
    target = path if hide == "file" else folder
    target.chmod(0)
    try:
        old = error_of(lambda: pydub.AudioSegment.from_file(str(path)))
        assert old is PermissionError
        for arg in (str(path), path, str(path).encode()):
            assert error_of(lambda: AudioSegment.from_file(arg)) is old
            assert error_of(lambda: AudioSegment.from_file(arg, format=ext)) is old
            assert error_of(lambda: AudioSegment.from_file_using_temporary_files(arg)) is old
    finally:
        target.chmod(0o700)


@need_ffmpeg
@needs_mode
@pytest.mark.parametrize("fmt", ["wav", "flac", "mp3", "ogg", "opus", "raw"])
@pytest.mark.parametrize("target", ["new file in a folder", "existing file"])
def test_unwritable_output_raises_what_pydub_raises(tmp_path, fmt, target):
    kw = dict(data=noise(2), sample_width=2, frame_rate=8000, channels=2)
    old, new = pydub.AudioSegment(**kw), AudioSegment(**kw)
    folder = tmp_path / "out"
    folder.mkdir()
    path = folder / "x"
    if target == "existing file":
        path.write_bytes(b"keep")
        path.chmod(0o400)
    else:
        folder.chmod(0o500)
    try:
        expected = error_of(lambda: old.export(str(path), format=fmt))
        assert expected is PermissionError
        for arg in (str(path), path):
            assert error_of(lambda: new.export(arg, format=fmt)) is expected
    finally:
        folder.chmod(0o700)
        path.chmod(0o600) if path.exists() else None
    # nothing was written
    assert [(p.name, p.read_bytes()) for p in folder.iterdir()] == ([("x", b"keep")] if path.exists() else [])
