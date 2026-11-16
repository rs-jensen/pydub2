import io
import json
import os
import subprocess

import pytest

np = pytest.importorskip("numpy")
pydub = pytest.importorskip("pydub")
import pydub2

root = os.environ.get("PYDUB2_CORPUS")
pytestmark = pytest.mark.skipif(not root, reason="set PYDUB2_CORPUS to a dir of audio files")
files = sorted(os.path.join(d, f) for d, _, fs in os.walk(root or "/nonexistent") for f in fs if not f.startswith("."))
LOSSY = {"mp3", "mp2", "mp1", "vorbis", "opus", "aac", "ac3", "eac3", "wmav1", "wmav2", "gsm_ms", "adpcm_g726"}
# 16 bit units, more than this is a shift or a different decode, not rounding
TOL = 328
MODES = ["path", "format", "fileobj", "bytesio", "bytesio_format", "start", "slice"]


def codec(name):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                        "-of", "json", name], capture_output=True)
    try:
        return json.loads(p.stdout)["streams"][0]["codec_name"]
    except (ValueError, KeyError, IndexError):
        return None


def load(lib, name, mode):
    ext = os.path.splitext(name)[1][1:].lower() or None
    AS = lib.AudioSegment
    if mode == "path":
        return AS.from_file(name)
    if mode == "format":
        return AS.from_file(name, format=ext)
    if mode == "fileobj":
        with open(name, "rb") as f:
            return AS.from_file(f)
    if mode == "bytesio":
        return AS.from_file(io.BytesIO(open(name, "rb").read()))
    if mode == "bytesio_format":
        return AS.from_file(io.BytesIO(open(name, "rb").read()), format=ext)
    if mode == "start":
        return AS.from_file(name, start_second=0.5)
    return AS.from_file(name, start_second=0.25, duration=0.5)


def samples(seg):
    dt = {1: np.int8, 2: np.int16, 4: np.int32}[seg.sample_width]
    raw = seg.raw_data
    # pydub keeps a stray odd byte from a broken wav
    return np.frombuffer(raw[:len(raw) - len(raw) % seg.sample_width], dtype=dt).astype(np.int64)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", files, ids=[os.path.relpath(f, root) for f in files] if root else None)
def test_corpus(name, mode, record_property):
    try:
        want = load(pydub, name, mode)
    except Exception as e:
        record_property("oracle", "raises " + type(e).__name__)
        try:
            load(pydub2, name, mode)
        except Exception:
            pass
        return
    try:
        got = load(pydub2, name, mode)
    except Exception as e:
        pytest.fail("pydub reads it, pydub2 raises %s: %s" % (type(e).__name__, str(e)[:200]))
    params = lambda s: (s.frame_rate, s.channels, s.sample_width, int(s.frame_count()))
    assert params(got) == params(want), "rate, channels, width, frames"
    a, b = samples(want), samples(got)
    if codec(name) in LOSSY:
        diff = int(np.abs(a - b).max()) >> (8 * (want.sample_width - 2)) if len(a) and want.sample_width > 1 else 0
        record_property("maxdiff", diff)
        assert diff <= TOL, "max abs diff %d in 16 bit units" % diff
    else:
        assert got.raw_data == want.raw_data, "%d of %d samples differ" % (int((a != b).sum()), len(a))
