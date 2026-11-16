import array
import math
import os
import random
import struct
import subprocess
import sys

import pytest

import _pydub2

ff = "/opt/homebrew/bin/ffmpeg"
if not os.path.exists(ff):
    ff = "ffmpeg"
try:
    subprocess.run([ff, "-version"], capture_output=True, check=True)
except (OSError, subprocess.CalledProcessError):
    pytest.skip("no ffmpeg", allow_module_level=True)

AudioSegment = pytest.importorskip("pydub").AudioSegment

RATE = 44100
random.seed(1)
SAMPLES = [0.5 * math.sin(i * 0.06) + 0.1 * random.uniform(-1, 1) for i in range(RATE // 2)]


def make(tmp_path, name, channels, *args):
    raw = b"".join(struct.pack("<f", x * (1 if c == 0 else 0.7)) for x in SAMPLES for c in range(2))
    path = str(tmp_path / name)
    cmd = [ff, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", "-",
           "-ac", str(channels), *args, path]
    p = subprocess.run(cmd, input=raw, capture_output=True)
    if b"Encoder not found" in p.stderr:
        pytest.skip("this ffmpeg lacks the encoder")
    assert p.returncode == 0, p.stderr
    return path


def both(path):
    with open(path, "rb") as f:
        data, rate, channels, width = _pydub2.decode(f.read())
    a = AudioSegment.from_file(path)
    assert (rate, channels, width) == (a.frame_rate, a.channels, a.sample_width)
    return data, a.raw_data, width


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("codec", ["pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"])
def test_wav(tmp_path, codec, channels):
    mine, theirs, width = both(make(tmp_path, "a.wav", channels, "-c:a", codec))
    assert mine == theirs


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("fmt", ["s16", "s32"])
def test_flac(tmp_path, fmt, channels):
    mine, theirs, width = both(make(tmp_path, "a.flac", channels, "-c:a", "flac", "-sample_fmt", fmt))
    assert mine == theirs


def close(mine, theirs, width, limit):
    assert len(mine) == len(theirs)
    code = "h" if width == 2 else "i"
    d = [abs(x - y) for x, y in zip(array.array(code, mine), array.array(code, theirs))]
    assert max(d) <= limit
    return sum(1 for x in d if x) / len(d)


def lossy(tmp_path, name, channels, limit, *args):
    return close(*both(make(tmp_path, name, channels, *args)), limit)


# same float to s16 as ffmpeg, what is left is minimp3 vs ffmpegs synthesis, 1 LSB on a few samples
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("args", [["-b:a", "128k"], ["-q:a", "4"], ["-b:a", "128k", "-write_xing", "0"],
                                  ["-ar", "22050", "-b:a", "64k"], ["-ar", "8000", "-q:a", "6"]])
def test_mp3(tmp_path, args, channels):
    assert lossy(tmp_path, "a.mp3", channels, 1, "-c:a", "libmp3lame", *args) < 0.02


# these have an Info frame too short for a whole lame block
@pytest.mark.parametrize("name", ["test1.mp3", "test2.mp3", "test3.mp3", "party.mp3"])
def test_mp3_info_frame(name):
    path = os.path.join(os.path.dirname(__file__), "pydub_suite", "data", name)
    if not os.path.exists(path):
        pytest.skip("no data file")
    assert close(*both(path), 1) < 0.005


@pytest.mark.parametrize("channels", [1, 2])
def test_vorbis(tmp_path, channels):
    lossy(tmp_path, "a.ogg", channels, 1024, "-c:a", "libvorbis")


# ffmpeg keeps or cuts the end padding by its own rule, short files are one page and come out odd
@pytest.mark.parametrize("n", [1, 1000, 1024, 1025, 4096, 12345, 44100, 65535])
@pytest.mark.parametrize("rate, args", [(44100, ["-ac", "2"]), (48000, ["-ac", "1", "-q:a", "8"])])
def test_vorbis_length(tmp_path, n, rate, args):
    path = str(tmp_path / "a.ogg")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=a=0.3:r=%d:seed=%d" % (rate, n),
                    "-af", "atrim=end_sample=%d" % n, *args, "-c:a", "libvorbis", path], check=True)
    close(*both(path), 1024)


# a sine starts on a short block, one page
def test_vorbis_short_first_block(tmp_path):
    path = str(tmp_path / "a.ogg")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "aevalsrc=0.3*sin(700*t):s=48000:d=0.5",
                    "-c:a", "libvorbis", path], check=True)
    close(*both(path), 1024)


# a download cut off in the middle of a page
def test_vorbis_cut_off(tmp_path):
    path = str(tmp_path / "a.ogg")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=a=0.3:d=5", "-c:a", "libvorbis",
                    path], check=True)
    with open(path, "rb") as f:
        whole = f.read()
    for cut in (len(whole) // 2, len(whole) - 100):
        same_as_ffmpeg(tmp_path, "cut.ogg", whole[:cut], 1024)


# ffmpeg uses its own opus decoder, not libopus, so this is only close
@pytest.mark.parametrize("channels", [1, 2])
def test_opus(tmp_path, channels):
    lossy(tmp_path, "a.opus", channels, 1 << 16, "-c:a", "libopus")


def test_opus_rate(tmp_path):
    with open(make(tmp_path, "a.opus", 2, "-c:a", "libopus"), "rb") as f:
        assert _pydub2.decode(f.read())[1] == 48000


@pytest.mark.parametrize("junk", [b"", b"abc", bytes(range(256)) * 40, b"RIFF\0\0\0\0WAVE" + b"x" * 100,
                                  b"OggS" + b"\0" * 100, b"fLaC" + b"\0" * 100,
                                  b"OggS" + b"\xff" * 36])
def test_garbage(junk):
    with pytest.raises(RuntimeError):
        _pydub2.decode(junk)


def test_wav_lies_about_size():
    head = b"RIFF" + struct.pack("<I", 0xfffffff0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
    data, rate, channels, width = _pydub2.decode(head + b"data" + struct.pack("<I", 0xffffff00) + b"\1\0" * 10)
    assert data == b"\1\0" * 10


def test_empty_wav():
    head = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, 8000, 32000, 4, 16)
    assert _pydub2.decode(head + b"data" + struct.pack("<I", 0)) == (b"", 8000, 2, 2)


def test_truncated(tmp_path):
    for name, args in [("a.wav", []), ("a.flac", []), ("a.mp3", []), ("a.ogg", ["-c:a", "libvorbis"]), ("a.opus", [])]:
        with open(make(tmp_path, name, 2, *args), "rb") as f:
            whole = f.read()
        for cut in [1, 30, 60, 300, len(whole) // 2, len(whole) - 1]:
            try:
                _pydub2.decode(whole[:cut])
            except RuntimeError:
                pass


def mp3_frames(m):
    i = 10 + ((m[6] & 127) << 21 | (m[7] & 127) << 14 | (m[8] & 127) << 7 | (m[9] & 127)) if m[:3] == b"ID3" else 0
    out = []
    while i + 4 <= len(m) and m[i] == 0xff:
        out.append(i)
        i += 144000 * 128 // 44100 + (m[i + 2] >> 1 & 1)
    return out


def same_as_ffmpeg(tmp_path, name, data, limit=1):
    path = str(tmp_path / name)
    with open(path, "wb") as f:
        f.write(data)
    close(*both(path), limit)


def test_mp3_padding_where_the_tag_says(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k"), "rb").read()
    starts = mp3_frames(m)
    for k in (10, len(starts) - 3):
        same_as_ffmpeg(tmp_path, "cut.mp3", m[:starts[k]])


def test_mp3_second_tag_and_junk(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k"), "rb").read()
    tag = b"ID3\4\0\0\0\0\0\x0a" + b"\0" * 10
    same_as_ffmpeg(tmp_path, "tag.mp3", tag + m)
    i = 10 + ((m[6] & 127) << 21 | (m[7] & 127) << 14 | (m[8] & 127) << 7 | (m[9] & 127))
    same_as_ffmpeg(tmp_path, "pad.mp3", m[:i] + b"\0" * 64 + m[i:])


def test_mp3_end(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k"), "rb").read()
    starts = mp3_frames(m)
    ape = b"APETAGEX" + struct.pack("<IIII", 2000, 32 + 10, 1, 1 << 31) + bytes(8)
    tags = [b"TAG" + bytes(125), ape[:32] + b"\0" * 10 + ape, b"\1" * 3000]
    for k in (len(starts) - 1, len(starts) - 5):
        for tail in tags:
            same_as_ffmpeg(tmp_path, "tag.mp3", m[:starts[k]] + tail)
        for cut in (4, 40, 200):
            same_as_ffmpeg(tmp_path, "cut.mp3", m[:starts[k] + cut], 328)


def test_mp3_starts_inside_the_bit_reservoir(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0"), "rb").read()
    starts = mp3_frames(m)
    for k in (3, 5, 11, 12):
        same_as_ffmpeg(tmp_path, "cut.mp3", m[starts[k]:])


@pytest.mark.parametrize("channels", [3, 4, 5, 6, 7, 8])
@pytest.mark.parametrize("name, codec", [("a.ogg", "libvorbis"), ("a.opus", "libopus")])
def test_channel_order(tmp_path, channels, name, codec):
    expr = "|".join("0.3*sin(%d*t)" % (700 * (c + 1)) for c in range(channels))
    path = str(tmp_path / name)
    p = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "aevalsrc=%s:s=48000:d=0.5" % expr,
                        "-c:a", codec, path], capture_output=True)
    if p.returncode:
        pytest.skip("this ffmpeg cant write it")
    close(*both(path), 1 << 20)


def test_mp2_goes_to_ffmpeg(tmp_path):
    with open(make(tmp_path, "a.mp2", 2, "-c:a", "mp2"), "rb") as f:
        with pytest.raises(RuntimeError):
            _pydub2.decode(f.read())


def test_chained_vorbis_goes_to_ffmpeg(tmp_path):
    with open(make(tmp_path, "a.ogg", 2, "-c:a", "libvorbis"), "rb") as f:
        one = f.read()
    with pytest.raises(RuntimeError):
        _pydub2.decode(one + one)


def test_chained_opus_goes_to_ffmpeg(tmp_path):
    with open(make(tmp_path, "a.opus", 2), "rb") as f:
        one = f.read()
    with open(make(tmp_path, "b.opus", 2), "rb") as f:
        two = f.read()
    assert one[14:18] != two[14:18]
    with pytest.raises(RuntimeError):
        _pydub2.decode(one + two)


def test_seek_back_after_the_end(tmp_path):
    for name, args in [("a.ogg", ["-c:a", "libvorbis"]), ("a.mp3", []), ("a.opus", []), ("a.flac", []), ("a.wav", [])]:
        d = _pydub2.Decoder(make(tmp_path, name, 2, *args))
        whole = d.read()
        d.seek(0)
        assert d.read() == whole
        at = len(whole) // (2 * d.width) // 2
        d.seek(at)
        assert d.read(1000) == whole[at * 2 * d.width:(at + 1000) * 2 * d.width]
        d.seek(2 ** 64 - 1)
        assert d.read(10) == b""


def test_read_size_is_not_trusted(tmp_path):
    d = _pydub2.Decoder(make(tmp_path, "a.wav", 2, "-c:a", "pcm_s16le"))
    assert len(d.read(2 ** 62)) == d.frames * 4
    d.seek(0)
    assert len(d.read(2 ** 40)) == d.frames * 4


@pytest.mark.parametrize("channels, bits", [(8, 24), (2, 16)])
def test_flac_header_claims_too_much(channels, bits):
    import tracemalloc
    head = (44100 << 44 | (channels - 1) << 41 | (bits - 1) << 36 | (1 << 36) - 1).to_bytes(8, "big")
    flac = b"fLaC\x80\0\0\x22" + struct.pack(">HH", 4096, 4096) + b"\0" * 6 + head + b"\0" * 16
    tracemalloc.start()
    try:
        _pydub2.decode(flac)
    except RuntimeError:
        pass
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 100 << 20


@pytest.mark.parametrize("rate", [1, 3, 20, 85])
def test_opus_encode_tiny_rate(rate):
    with pytest.raises(RuntimeError):
        _pydub2.encode(bytes(2000), rate, 1, 2, "opus")


# an rf64 header that says less data than the file holds, fuzzer find. the extra byte after the last frame was handed out
RF64_HEAD = bytes.fromhex(
    "52463634ffffffff57415645647336341c0000005445000000000000e84400000000000011a300000000000000000000666d742028000000"
    "feff010044ac000010b102000400200016002000000000000100000000001000800000aa00389b7166616374040000003a11000064617461e8440000")


def test_wav_stray_byte_after_the_last_frame():
    d = _pydub2.Decoder(RF64_HEAD + bytes(34933))
    d.seek(d.frames - 1)
    assert len(d.read(7)) == 4
    d.seek(d.frames)
    assert d.read(1) == b""
    assert len(_pydub2.decode(RF64_HEAD + bytes(34933))[0]) % 4 == 0


# a 221 byte vorbis file whose setup header asks stb for gigabytes
SETUP_BOMB = bytes.fromhex(
    "4f676753000200000000fffffffffe4d9d0cffffffff04504b34011e01766f726269730000000001401f000000000000408300000000000099"
    "014f67675300000000000000000000fe4d9d0c010000006a5953040b3fffffffffffffffffffb503766f726269730c3b656e636f6465723d4c"
    "61766336312e31392e313031206c6962766f726269730105766f726269731242435601000001000c5214210001a194d64a2ab195925a6cadd5"
    "d85a8c3594d2622929b5904a6cadb5585b6c35a696626cb1d558528b")


def test_vorbis_setup_bomb():
    code = "import resource, sys, _pydub2\ntry:\n    _pydub2.decode(bytes.fromhex(sys.argv[1]))\nexcept RuntimeError:\n    pass\n" \
           "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    out = subprocess.run([sys.executable, "-c", code, SETUP_BOMB.hex()], capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert int(out.stdout) / (1 << 20 if sys.platform == "darwin" else 1 << 10) < 400
