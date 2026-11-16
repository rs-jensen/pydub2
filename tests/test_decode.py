import array
import io
import math
import os
import platform
import random
import struct
import subprocess
import sys
import time

import pytest

import _pydub2
import pydub2

ff = "/opt/homebrew/bin/ffmpeg"
if not os.path.exists(ff):
    ff = "ffmpeg"
try:
    subprocess.run([ff, "-version"], capture_output=True, check=True)
except (OSError, subprocess.CalledProcessError):
    pytest.skip("no ffmpeg", allow_module_level=True)

AudioSegment = pytest.importorskip("pydub").AudioSegment

# the only ffmpeg for windows arm is x64, and its stereo mp3 comes out half a step higher than on arm, mac arm ffmpeg agrees with us
X64_FF_ON_ARM = sys.platform == "win32" and platform.machine() == "ARM64"
STEREO = "x64 ffmpeg on arm rounds stereo mp3 differently"

RATE = 44100
random.seed(1)
SAMPLES = [0.5 * math.sin(i * 0.06) + 0.1 * random.uniform(-1, 1) for i in range(RATE // 2)]


CLICKS = [0.4 * math.sin(i * 0.05) * (i % 4410 > 200) + (random.uniform(-0.6, 0.6) if i % 4410 < 30 else 0)
          for i in range(RATE * 2)]


def make(tmp_path, name, channels, *args, samples=SAMPLES):
    raw = b"".join(struct.pack("<f", x * (1 if c == 0 else 0.7)) for x in samples for c in range(2))
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


# same float to s16 as ffmpeg, what is left is minimp3 vs ffmpegs synthesis, 1 LSB on up to 1 sample in 100 (the ubuntu runner)
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("args", [["-b:a", "128k"], ["-q:a", "4"], ["-b:a", "128k", "-write_xing", "0"],
                                  ["-ar", "22050", "-b:a", "64k"], ["-ar", "8000", "-q:a", "6"]])
def test_mp3(tmp_path, args, channels):
    if channels == 2 and X64_FF_ON_ARM:
        pytest.skip(STEREO)
    assert lossy(tmp_path, "a.mp3", channels, 1, "-c:a", "libmp3lame", *args) < 0.015  # ffmpeg x86 SIMD rounds differently


# ffmpeg 1.x wrote an Info frame with 8 bytes too few for the whole lame block, dr_mp3 then keeps it as audio
@pytest.mark.parametrize("tag", [b"Info", b"Xing"])
def test_mp3_info_frame(tmp_path, tag):
    if X64_FF_ON_ARM:
        pytest.skip(STEREO)
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "40k", "-ar", "32000", "-write_xing", "0",
                  "-id3v2_version", "0"), "rb").read()
    assert m[:3] == bytes.fromhex("fffb28") and len(m) % 180 == 0
    info = bytearray(180)
    info[:4] = m[:4]
    info[36:40] = tag
    info[40:56] = struct.pack(">IIII", 7, len(m) // 180, len(m) + 180, 0x00010203)
    info[152:165] = b"Lavf55.12.100"
    assert close(*both(save(tmp_path, "info.mp3", bytes(info) + m)), 1) < 0.015


def save(tmp_path, name, data):
    path = str(tmp_path / name)
    with open(path, "wb") as f:
        f.write(data)
    return path


@pytest.mark.parametrize("channels", [1, 2])
def test_vorbis(tmp_path, channels):
    lossy(tmp_path, "a.ogg", channels, 1280, "-c:a", "libvorbis")  # msvc float math, 1152 seen


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


def wav_file(fmt, *chunks):
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def data_chunk(pcm, size=None):
    return b"data" + struct.pack("<I", len(pcm) if size is None else size) + pcm + b"\0" * (len(pcm) % 2)


# pydub reads these wavs itself from a path and through ffmpeg from a file object, each its own way
def test_wav_odd_headers_like_pydub(tmp_path):
    pcm = struct.pack("<%dh" % len(SAMPLES), *(int(x * 30000) for x in SAMPLES))
    f16 = struct.pack("<HHIIHH", 1, 2, RATE, RATE * 4, 4, 16)
    files = [wav_file(f16, data_chunk(pcm, 0)), wav_file(f16, data_chunk(pcm[:4000]), data_chunk(pcm[4000:8000])),
             wav_file(f16, data_chunk(pcm))[:-3], wav_file(f16, data_chunk(pcm + b"\1", len(pcm) + 1)),
             wav_file(struct.pack("<HHIIHH", 3, 1, RATE, RATE * 8, 8, 64), data_chunk(struct.pack("<%dd" % len(SAMPLES), *SAMPLES)))]
    for i, raw in enumerate(files):
        path = str(tmp_path / ("%d.wav" % i))
        with open(path, "wb") as f:
            f.write(raw)
        for src in (lambda: path, lambda: io.BytesIO(raw)):
            for kw in ({}, {"start_second": 0.1}):
                try:
                    want = AudioSegment.from_file(src(), **kw)
                except Exception:
                    continue
                got = pydub2.AudioSegment.from_file(src(), **kw)
                assert (got.sample_width, got.raw_data) == (want.sample_width, want.raw_data)


def test_wav_lies_about_size():
    head = b"RIFF" + struct.pack("<I", 0xfffffff0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
    data, rate, channels, width = _pydub2.decode(head + b"data" + struct.pack("<I", 0xffffff00) + b"\1\0" * 10)
    assert data == b"\1\0" * 10


# a chunk size near 4 GB must not wrap round and send the chunk walk into the chunk, to the fake data chunk in it
def test_wav_huge_chunk_after_data():
    head = b"RIFF" + struct.pack("<I", 0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
    wav = head + b"data" + struct.pack("<I", 4) + b"\1\0\2\0" + b"junk\xff\xff\xff\xff" + b"data\2\0\0\0\x09\0"
    assert _pydub2.decode(wav)[0] == b"\1\0\2\0"


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
        kbps = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320][m[i + 2] >> 4]
        i += 144000 * kbps // 44100 + (m[i + 2] >> 1 & 1)
    return out


# big_values of the first granule of a stereo frame to 511, ffmpeg drops the frame
def big_values(m, at):
    p = (at + 4) * 8 + 32
    m[p // 8:p // 8 + 3] = (int.from_bytes(m[p // 8:p // 8 + 3], "big") | 0x1ff << (15 - p % 8)).to_bytes(3, "big")


def bits(m, p, n):
    v = 0
    for k in range(p, p + n):
        v = v << 1 | m[k // 8] >> (7 - k % 8) & 1
    return v


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
            same_as_ffmpeg(tmp_path, "cut.mp3", m[:starts[k] + cut])


# ffmpeg stops the huffman data at the cut, past it a granule that starts in the bit reservoir reads zeros
def test_mp3_cut_inside_a_granule(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k", samples=CLICKS), "rb").read()
    starts = mp3_frames(m)
    for cut in (40, 60, 100, 150):
        same_as_ffmpeg(tmp_path, "cut.mp3", m[:starts[10] + cut])


# some encoders set scfsi on a long granule after a short one, ffmpeg copies the scalefactors then
def test_mp3_scfsi_after_a_short_granule(tmp_path):
    m = bytearray(open(make(tmp_path, "a.mp3", 1, "-c:a", "libmp3lame", "-b:a", "64k", samples=CLICKS), "rb").read())
    hit = 0
    for at in mp3_frames(m):
        # mono side info: 9 bits main_data_begin, 5 private, 4 scfsi, then 59 per granule
        q = (at + 4) * 8 + 18
        if bits(m, q + 33, 3) == 6 and bits(m, q + 59 + 33, 3) != 6 and bits(m, q + 59 + 29, 4) == 0:
            m[at + 5] |= 3
            m[at + 6] |= 0xc0
            hit += 1
    assert hit
    same_as_ffmpeg(tmp_path, "scfsi.mp3", bytes(m))


# mpeg 2 intensity stereo, the right channel is all zero so every band takes the left one at the position in its
# scalefactors. ffmpeg only calls 16 and up illegal, not the highest value the scalefactor size allows
def test_mp3_lsf_intensity_positions(tmp_path):
    def b(v, n):
        return format(v, "0%db" % n)

    def granule(part, gain, sfc):
        return b(part, 12) + b(0, 9) + b(gain, 8) + b(sfc, 9) + "0" + b(0, 22) + "01"
    # 42 count1 quads of four ones on the left, then the right scalefactors: 7 of 3 bits and 7 of 5 bits
    data = b(0, 10) + granule(42 * 8, 170, 0) + granule(56, 0, 276) + "0" * 8 * 42 + b(7, 3) * 7 + b(20, 5) * 7
    frame = b"\xff\xf3\x40\x50" + int(data, 2).to_bytes(len(data) // 8, "big")
    same_as_ffmpeg(tmp_path, "is.mp3", (frame + bytes(104 - len(frame))) * 4)


# ffmpeg drops the frame after junk unless the junk is all zeros, and a frame with big_values over 288
def test_mp3_junk_between_frames(tmp_path):
    m = bytearray(open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k"), "rb").read())
    starts = mp3_frames(m)
    big_values(m, starts[16])
    out = m[:starts[3]] + b"\1\2" + m[starts[3]:starts[5]] + bytes(3) + m[starts[5]:starts[8] - 2]
    out += m[starts[8]:starts[12]] + b"\7" * 416 + m[starts[12]:]
    same_as_ffmpeg(tmp_path, "junk.mp3", bytes(out))
    assert len(_pydub2.decode(bytes(out))[0]) < len(_pydub2.decode(bytes(m))[0])


def junk_of(kind, n, rng):
    if kind == "nul":
        return bytes(n)
    if kind == "random":
        return rng.randbytes(n)
    return (b"\xff\xfb\x90\x00" + rng.randbytes(n))[:n]


# ffmpeg looks for two headers in a row in the first 64k, junk in front of the first frame or the id3 tag is skipped
@pytest.mark.parametrize("kind", ["nul", "random", "sync"])
@pytest.mark.parametrize("n", [1, 2, 100, 4096, 70000])
def test_mp3_junk_in_front(tmp_path, kind, n):
    rng = random.Random(n)
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k", samples=CLICKS), "rb").read()
    i = 10 + ((m[6] & 127) << 21 | (m[7] & 127) << 14 | (m[8] & 127) << 7 | (m[9] & 127))
    junk = junk_of(kind, n, rng)
    same_as_ffmpeg(tmp_path, "front.mp3", junk + m)
    same_as_ffmpeg(tmp_path, "after.mp3", m[:i] + junk + m[i:])
    same_as_ffmpeg(tmp_path, "twice.mp3", m[:i] + junk + m)
    same_as_ffmpeg(tmp_path, "bare.mp3", junk + m[i:])


def test_mp3_id3_that_is_not_one(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k", samples=CLICKS), "rb").read()
    i = 10 + ((m[6] & 127) << 21 | (m[7] & 127) << 14 | (m[8] & 127) << 7 | (m[9] & 127))
    for size in (i - 11, i - 9, i - 30, i + 90, 0):
        same_as_ffmpeg(tmp_path, "size.mp3", m[:6] + bytes([size >> 21 & 127, size >> 14 & 127, size >> 7 & 127, size & 127]) + m[10:])
    same_as_ffmpeg(tmp_path, "ff.mp3", b"ID3\xff" + m[4:])
    same_as_ffmpeg(tmp_path, "high.mp3", m[:6] + b"\x80" + m[7:])


def test_other_formats_are_not_taken_for_mp3(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k"), "rb").read()
    for data in (bytes(5000), os.urandom(20000), b"\0\0\0\x1cftypM4A " + bytes(3000), b"\0\0\0\x1cftypisom" + m):
        with pytest.raises(RuntimeError):
            _pydub2.decode(data)


def test_mp3_inside_a_container_goes_to_ffmpeg(tmp_path):
    import tarfile
    import zipfile
    pydub2_seg = pydub2.AudioSegment
    mp3 = make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k", samples=CLICKS)
    for fmt, ext in [("mpegts", "ts"), ("mpeg", "mpg"), ("vob", "vob"), ("avi", "avi"), ("flv", "flv"), ("matroska", "mkv"),
                     ("mp4", "mp4")]:
        path = str(tmp_path / ("a." + ext))
        if subprocess.run([ff, "-v", "error", "-y", "-i", mp3, "-c:a", "copy", "-f", fmt, path]).returncode:
            continue
        with pytest.raises(RuntimeError):
            _pydub2.decode(open(path, "rb").read())
        assert pydub2_seg.from_file(path).raw_data == AudioSegment.from_file(path).raw_data
    path = str(tmp_path / "frag.mp4")
    if subprocess.run([ff, "-v", "error", "-y", "-i", mp3, "-c:a", "copy", "-movflags", "frag_keyframe+empty_moov", path]).returncode:
        pytest.skip("this ffmpeg cant write fragmented mp4")
    d = open(path, "rb").read()
    segment = d[d.index(b"moof") - 4:]
    with pytest.raises(RuntimeError):
        _pydub2.decode(segment)
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as z:
        z.write(mp3, "a.mp3")
    with tarfile.open(tmp_path / "a.tar", "w") as t:
        t.add(mp3, "a.mp3")
    for name in ("a.zip", "a.tar"):
        with pytest.raises(RuntimeError):
            _pydub2.decode(open(tmp_path / name, "rb").read())


def patch_streaminfo(m, at, value, size):
    return m[:8 + at] + value.to_bytes(size, "big") + m[8 + at + size:]


# a header that understates the length, or a max block size bigger than the frames, does not change what ffmpeg reads
def test_flac_header_that_lies(tmp_path):
    m = open(make(tmp_path, "a.flac", 2, "-c:a", "flac", samples=CLICKS), "rb").read()
    total = (m[21] & 15) << 32 | int.from_bytes(m[22:26], "big")
    assert total == len(CLICKS)
    short = m[:21] + bytes([m[21] & 0xf0]) + (total // 2).to_bytes(4, "big") + m[26:]
    same_as_ffmpeg(tmp_path, "short.flac", short)
    assert len(_pydub2.decode(short)[0]) == len(_pydub2.decode(m)[0])
    same_as_ffmpeg(tmp_path, "block.flac", patch_streaminfo(m, 2, 8192, 2))
    assert len(_pydub2.decode(patch_streaminfo(m, 2, 8192, 2))[0]) == len(_pydub2.decode(m)[0])


def crc8(d):
    c = 0
    for b in d:
        c ^= b
        for _ in range(8):
            c = (c << 1 ^ 7 if c & 128 else c << 1) & 255
    return c


def flac_frames(m):
    out = []
    for i in range(len(m) - 8):
        if m[i] == 0xff and m[i + 1] == 0xf8 and m[i + 4] < 0x80 and crc8(m[i:i + 5]) == m[i + 5]:
            out.append(i)
    return out


# a bad crc frame is silence here and ffmpeg keeps its samples, the length is the same and nothing after it is lost
def test_flac_damaged_keeps_the_length(tmp_path):
    m = open(make(tmp_path, "a.flac", 2, "-c:a", "flac", "-frame_size", "4608", samples=CLICKS), "rb").read()
    blk = int.from_bytes(m[10:12], "big")
    starts = flac_frames(m) + [len(m)]
    assert len(starts) > 15
    clean = _pydub2.decode(m)[0]
    one = blk * 8
    p = str(tmp_path / "bad.flac")
    for k in (3, 9):
        a, b = starts[k], starts[k + 1]
        for at in (a + (b - a) // 2, b - 1, b - 2):
            d = bytearray(m)
            d[at] ^= 0x55
            open(p, "wb").write(d)
            mine = _pydub2.decode(bytes(d))[0]
            assert len(mine) == len(clean)
            assert mine[:(k * blk - 1) * 8] == clean[:(k * blk - 1) * 8]
            assert mine[(k + 2) * blk * 8:] == clean[(k + 2) * blk * 8:]
            assert len(AudioSegment.from_file(p).raw_data) in (len(clean), len(clean) - one)
    rng = random.Random(3)
    for _ in range(40):
        d = bytearray(m)
        d[rng.randrange(starts[0], len(m))] ^= 1 << rng.randrange(8)
        open(p, "wb").write(d)
        # never stops early, at most the damaged frame and the next one are gone
        assert len(_pydub2.decode(bytes(d))[0]) >= len(clean) - 2 * one
        assert abs(len(_pydub2.decode(bytes(d))[0]) - len(AudioSegment.from_file(p).raw_data)) <= 2 * one


# ffmpeg keeps the channels of the first frame and mixes the rest, a new rate it resamples so that goes to ffmpeg
def test_mp3_channels_change(tmp_path):
    args = ["-c:a", "libmp3lame", "-write_xing", "0", "-id3v2_version", "0"]
    st = open(make(tmp_path, "st.mp3", 2, *args), "rb").read()
    mo = open(make(tmp_path, "mo.mp3", 1, *args), "rb").read()
    same_as_ffmpeg(tmp_path, "stmo.mp3", st + mo)
    same_as_ffmpeg(tmp_path, "most.mp3", mo + st)
    low = open(make(tmp_path, "low.mp3", 2, "-ar", "22050", *args), "rb").read()
    with pytest.raises(RuntimeError):
        _pydub2.decode(st + low)


# ffmpeg wants two frames in a row to start, a single frame after the Info frame is not an mp3 to it
def test_mp3_one_frame(tmp_path):
    m = open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "128k"), "rb").read()
    path = save(tmp_path, "one.mp3", m[:mp3_frames(m)[2]])
    with pytest.raises(Exception):
        AudioSegment.from_file(path)
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        pydub2.AudioSegment.from_file(path)


# a seek right after a frame ffmpeg drops gives the same samples as a read from the start
def test_mp3_seek_after_a_dropped_frame(tmp_path):
    m = bytearray(open(make(tmp_path, "a.mp3", 2, "-c:a", "libmp3lame", "-b:a", "320k", samples=CLICKS), "rb").read())
    for k in (20, 21):
        big_values(m, mp3_frames(m)[k])
    d = _pydub2.Decoder(bytes(m))
    whole = d.read()
    for at in range(18 * 1152, 26 * 1152, 300):
        d.seek(at)
        assert d.read(1500) == whole[at * 4:(at + 1500) * 4]


# the private bits in the side info are no scfsi, set them in every frame
@pytest.mark.parametrize("channels", [1, 2])
def test_mp3_private_bits(tmp_path, channels):
    m = bytearray(open(make(tmp_path, "a.mp3", channels, "-c:a", "libmp3lame", "-b:a", "128k"), "rb").read())
    for at in mp3_frames(m)[1:]:
        m[at + 5] |= 0x7c if channels == 1 else 0x70
    same_as_ffmpeg(tmp_path, "priv.mp3", bytes(m))


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
        # ffmpeg writes no opus for these two layouts
        assert name == "a.opus" and channels in (3, 4), p.stderr
        pytest.skip("this ffmpeg cant write it")
    # libopus and ffmpeg's own opus decoder are up to 245568 apart on 6 channels, vorbis is under 1024 (1152 with msvc)
    close(*both(path), 1280 if name == "a.ogg" else 1 << 19)


def test_mp2_goes_to_ffmpeg(tmp_path):
    with open(make(tmp_path, "a.mp2", 2, "-c:a", "mp2"), "rb") as f:
        with pytest.raises(RuntimeError):
            _pydub2.decode(f.read())


# a cover picture of several MB in the comments must not make opening quadratic
def test_vorbis_big_comment_opens_fast(tmp_path):
    times, outs = [], []
    for mb in (1, 10):
        meta = tmp_path / "meta.txt"
        meta.write_text(";FFMETADATA1\ncomment=" + "A" * (mb << 20) + "\n")
        with open(make(tmp_path, "%d.ogg" % mb, 2, "-i", str(meta), "-map_metadata", "1", "-c:a", "libvorbis"), "rb") as f:
            data = f.read()
        best = 1e9
        for _ in range(3):
            t = time.perf_counter()
            outs.append(_pydub2.decode(data))
            best = min(best, time.perf_counter() - t)
        times.append(best)
    assert all(o == outs[0] for o in outs)
    # linear is about 10 times the 1 MB file, quadratic about 100 times
    assert times[1] < 30 * times[0]


def test_chained_vorbis_goes_to_ffmpeg(tmp_path):
    with open(make(tmp_path, "a.ogg", 2, "-c:a", "libvorbis"), "rb") as f:
        one = f.read()
    with pytest.raises(RuntimeError):
        _pydub2.decode(one + one)


def test_chained_opus_decodes_natively(tmp_path):
    with open(make(tmp_path, "a.opus", 2), "rb") as f:
        one = f.read()
    with open(make(tmp_path, "b.opus", 2), "rb") as f:
        two = f.read()
    assert one[14:18] != two[14:18]
    d = _pydub2.Decoder(one + two)
    assert d.chained
    assert not _pydub2.Decoder(one).chained
    assert len(d.read()) == len(_pydub2.decode(one)[0]) + len(_pydub2.decode(two)[0])


def test_chained_opus_with_another_channel_count_fails(tmp_path):
    with open(make(tmp_path, "a.opus", 2), "rb") as f:
        one = f.read()
    with open(make(tmp_path, "b.opus", 1), "rb") as f:
        two = f.read()
    with pytest.raises(RuntimeError):
        _pydub2.Decoder(one + two).read()


def test_vorbis_part_goes_to_ffmpeg(tmp_path):
    path = make(tmp_path, "a.ogg", 2, "-c:a", "libvorbis")
    for kw in ({"start_second": 0.1}, {"start_second": 0.05, "duration": 0.2}):
        assert pydub2.AudioSegment.from_file(path, **kw).raw_data == AudioSegment.from_file(path, **kw).raw_data


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
    from test_untrusted import peak_mb
    assert peak_mb("try:\n    _pydub2.decode(bytes.fromhex(%r))\nexcept RuntimeError:\n    pass" % SETUP_BOMB.hex()) < 400
