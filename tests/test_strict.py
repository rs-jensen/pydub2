import io
import os
import shutil
import subprocess
import sys

import pytest

pydub = pytest.importorskip("pydub")
import pydub2
import pydub.utils
import pydub2.utils

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

FORMATS = {
    "wav8": ("wav", "pcm_u8", []), "wav16": ("wav", "pcm_s16le", []), "wav24": ("wav", "pcm_s24le", []),
    "wav32": ("wav", "pcm_s32le", []), "wavf32": ("wav", "pcm_f32le", []), "flac": ("flac", "flac", []),
    "mp3cbr": ("mp3", "libmp3lame", ["-b:a", "128k"]), "mp3vbr": ("mp3", "libmp3lame", ["-q:a", "4"]),
    "ogg": ("ogg", "libvorbis", []), "opus": ("opus", "libopus", []), "m4a": ("m4a", "aac", []),
}
CASES = [(k, ch) for k in FORMATS for ch in (1, 2, 6)]
BITEXACT = ["-fflags", "+bitexact"]


@pytest.fixture(autouse=True)
def strict(monkeypatch):
    monkeypatch.setattr(pydub2, "strict", True)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("strict")
    out = {}
    for (kind, ch) in CASES:
        ext, codec, extra = FORMATS[kind]
        src = "|".join("sin(2*PI*%d*t)*0.5" % (200 + 100 * i) for i in range(ch))
        path = str(d / ("%s_%d.%s" % (kind, ch, ext)))
        p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc=%s:s=24000:d=0.4" % src,
                            "-c:a", codec, *extra, path], capture_output=True)
        if p.returncode == 0:
            out[(kind, ch)] = path
    return out


def outcome(fn):
    try:
        seg = fn()
    except Exception as e:
        return type(e).__name__
    return seg.raw_data, seg.frame_rate, seg.channels, seg.sample_width


def modes(path):
    ext = os.path.splitext(path)[1][1:]

    def fileobj(lib, **kw):
        with open(path, "rb") as f:
            return lib.AudioSegment.from_file(f, **kw)

    def bytesio(lib, **kw):
        return lib.AudioSegment.from_file(io.BytesIO(open(path, "rb").read()), **kw)
    return {
        "path": lambda lib: lib.AudioSegment.from_file(path),
        "format": lambda lib: lib.AudioSegment.from_file(path, format=ext),
        "fileobj": fileobj,
        "bytesio": bytesio,
        "bytesio_format": lambda lib: bytesio(lib, format=ext),
        "start": lambda lib: lib.AudioSegment.from_file(path, start_second=0.1),
        "duration": lambda lib: lib.AudioSegment.from_file(path, duration=0.15),
        "both": lambda lib: lib.AudioSegment.from_file(path, start_second=0.1, duration=0.15),
        "both_fileobj": lambda lib: fileobj(lib, start_second=0.1, duration=0.15),
        "from_mp3": lambda lib: lib.AudioSegment.from_mp3(path),
    }


@pytest.mark.parametrize("mode", list(modes("x.wav")))
@pytest.mark.parametrize("case", CASES, ids=["%s-%dch" % c for c in CASES])
def test_load(files, case, mode):
    if case not in files:
        pytest.skip("ffmpeg cant make this file")
    run = modes(files[case])[mode]
    want = outcome(lambda: run(pydub))
    assert outcome(lambda: run(pydub2)) == want


@pytest.mark.parametrize("case", [("wav16", 2), ("mp3cbr", 2), ("opus", 2), ("flac", 6), ("m4a", 2)])
def test_mediainfo(files, case):
    if case not in files:
        pytest.skip("ffmpeg cant make this file")
    path = files[case]
    assert pydub2.utils.mediainfo(path) == pydub.utils.mediainfo(path)
    assert pydub2.utils.mediainfo_json(path) == pydub.utils.mediainfo_json(path)


def ogg_pages(b):
    # ffmpeg picks a random stream serial for ogg, so the serial and the crc that covers it are zeroed
    b, i = bytearray(b), 0
    while b[i:i + 4] == b"OggS":
        b[i + 14:i + 18] = b[i + 22:i + 26] = bytes(4)
        i += 27 + b[i + 26] + sum(b[i + 27:i + 27 + b[i + 26]])
    return bytes(b)


VARIANTS = {"plain": {}, "bitrate": {"bitrate": "48k"}, "tags": {"tags": {"title": "t", "artist": "a"}},
            "parameters": {"parameters": ["-ar", "16000"]}, "bitexact": {"parameters": BITEXACT}}


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("format", ["wav", "mp3", "ogg", "flac", "opus", "ipod"])
def test_export(files, format, variant):
    kw = VARIANTS[variant]
    want = outcome_bytes(pydub, files[("wav16", 2)], format, kw)
    got = outcome_bytes(pydub2, files[("wav16", 2)], format, kw)
    if variant != "bitexact":
        want, got = [ogg_pages(x) if isinstance(x, bytes) else x for x in (want, got)]
    assert got == want


def outcome_bytes(lib, path, format, kw):
    seg = lib.AudioSegment.from_file(path)
    try:
        return seg.export(io.BytesIO(), format=format, **kw).read()
    except Exception as e:
        return type(e).__name__


def test_export_without_format(tmp_path):
    seg = pydub2.AudioSegment.silent(300, 24000)
    # pydub writes mp3 whatever the name says
    assert seg.export(str(tmp_path / "a.wav")).read()[:4] != b"RIFF"


def test_no_ffmpeg(files, monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(pydub2.AudioSegment, "converter", "ffmpeg")
    for fn in (lambda: pydub2.AudioSegment.from_file(files[("flac", 2)]),
               lambda: pydub2.AudioSegment.from_file(files[("flac", 2)], codec="libopus"),
               lambda: pydub2.AudioSegment.silent(300).export(io.BytesIO(), format="flac"),
               lambda: pydub2.AudioSegment.silent(300).export(io.BytesIO(), format="mp3"),
               lambda: pydub2.utils.mediainfo(files[("flac", 2)])):
        with pytest.raises(FileNotFoundError, match="strict mode needs ffmpeg"):
            fn()
    assert len(pydub2.AudioSegment.from_file(files[("wav16", 2)])) > 0


@pytest.mark.parametrize("value, want", [("1", True), ("0", False), (None, False)])
def test_env(value, want):
    env = {k: v for k, v in os.environ.items() if k != "PYDUB2_STRICT"}
    if value:
        env["PYDUB2_STRICT"] = value
    out = subprocess.run([sys.executable, "-c", "import pydub2; print(pydub2.strict)"], env=env,
                         capture_output=True, text=True)
    assert out.stdout.strip() == str(want)
