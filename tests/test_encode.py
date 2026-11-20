import array
import io
import math
import os
import shutil
import subprocess
import wave
import sys

import numpy as np
import pytest

import _pydub2
import pydub2
from pydub2.audio_segment import WAVE_PADS, riff_without_pad

ff = "/opt/homebrew/bin/ffmpeg"
if not os.path.exists(ff):
    ff = shutil.which("ffmpeg")
needs_ff = pytest.mark.skipif(ff is None, reason="no ffmpeg")

codes = {1: "b", 2: "h", 4: "i"}


def sine(rate=44100, channels=1, width=2, secs=1.0, freq=440):
    peak = 2 ** (width * 8 - 1) - 1
    n = int(rate * secs)
    s = [int(peak * 0.5 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
    return array.array(codes[width], [v for v in s for _ in range(channels)]).tobytes()


def ffdecode(data, rate, channels):
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-"],
                       input=data, capture_output=True, check=True)
    return array.array("h", p.stdout)


def probe(data):
    p = subprocess.run([ff, "-i", "-"], input=data, capture_output=True)
    return p.stderr.decode()


def best_corr(x, y):
    # encoders add a little delay at the start, so try a few lags
    a = np.array(x[:8000], float)
    b = np.array(y[:12000], float)
    c = np.correlate(b, a, "valid")
    return c.max() / np.sqrt((a * a).sum() * (b[c.argmax():c.argmax() + 8000] ** 2).sum())


@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("secs", [0, 0.5, 1 / 8000, 3 / 8000])
def test_wav_like_pydub(tmp_path, width, channels, secs):
    pydub = pytest.importorskip("pydub")
    data = sine(8000, channels, width, secs)
    old = pydub.AudioSegment(data, frame_rate=8000, channels=channels, sample_width=width)
    new = pydub2.AudioSegment(data, frame_rate=8000, channels=channels, sample_width=width)
    assert new.export(format="wav").read() == old.export(format="wav").read()
    new.export(str(tmp_path / "new.wav"), format="wav")
    old.export(str(tmp_path / "old.wav"), format="wav")
    assert (tmp_path / "new.wav").read_bytes() == (tmp_path / "old.wav").read_bytes()


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_roundtrip(channels):
    data = sine(44100, channels, 2)
    out = _pydub2.encode(data, 44100, channels, 2, "flac")
    assert out[:4] == b"fLaC"
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s16le", "-"], input=out, capture_output=True, check=True)
    assert p.stdout == data


@needs_ff
@pytest.mark.parametrize("rate", [65535, 65536, 67685, 655351, 1048575])
def test_flac_rates_outside_the_subset(rate):
    data = sine(rate, 1, 2, secs=0.05)
    out = _pydub2.encode(data, rate, 1, 2, "flac")
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s16le", "-"], input=out, capture_output=True, check=True)
    assert p.stdout == data
    assert pydub2.AudioSegment.from_file(io.BytesIO(out), format="flac").frame_rate == rate


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [44100, 22050])
def test_lossy(fmt, channels, rate):
    data = sine(rate, channels)
    out = _pydub2.encode(data, rate, channels, 2, fmt)
    info = probe(out)
    assert ("vorbis" if fmt == "ogg" else "opus") in info
    # opus always decodes at 48k
    assert "%d Hz, %s" % (rate if fmt == "ogg" else 48000, ["mono", "stereo"][channels - 1]) in info
    back = ffdecode(out, rate, channels)
    assert abs(len(back) - len(data) // 2) < rate * channels // 20
    assert best_corr(array.array("h", data)[::channels], back[::channels]) > 0.95


# one tone per channel, the fourth channel of 5.1 and 7.1 is the low frequency one
TONES = [600, 1000, 1400, 100, 2200, 2600, 3000, 3400]


def tone_per_channel(channels, rate=48000):
    t = np.arange(rate) / rate
    return np.stack([0.4 * np.sin(2 * np.pi * f * t) for f in TONES[:channels]], 1)


def loudest(pcm, channels, rate=48000):
    x = np.frombuffer(pcm, "<i2").reshape(-1, channels).astype(float)
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    return [freqs[np.abs(np.fft.rfft(x[:, c])).argmax()] for c in range(channels)]


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
@pytest.mark.parametrize("channels", [2, 5, 6, 7, 8])
def test_lossy_keeps_the_channel_order(fmt, channels):
    data = (tone_per_channel(channels) * 32767).astype("<i2").tobytes()
    out = pydub2.AudioSegment(data, sample_width=2, frame_rate=48000, channels=channels).export(format=fmt).read()
    heard = [loudest(ffdecode(out, 48000, channels).tobytes(), channels)]
    pcm, _, ch, width = _pydub2.decode(out)
    heard.append(loudest((np.frombuffer(pcm, "<i%d" % width) >> (8 * width - 16)).astype("<i2").tobytes(), ch))
    for got in heard:
        assert np.abs(np.array(got) - TONES[:channels]).max() < 30, (got, TONES[:channels])


# ffmpeg's libopus encoder does not keep the order of 5 and 7 channels, so those are not compared for opus
LIKE_PYDUB = [("ogg", c) for c in (2, 5, 6, 7, 8)] + [("opus", c) for c in (2, 6, 8)]


@needs_ff
@pytest.mark.parametrize("fmt,channels", LIKE_PYDUB)
def test_lossy_channel_order_like_pydub(fmt, channels):
    data = (tone_per_channel(channels) * 32767).astype("<i2").tobytes()
    got = loudest(ffdecode(pydub_export(data, 48000, channels, 2, fmt), 48000, channels).tobytes(), channels)
    assert np.abs(np.array(got) - TONES[:channels]).max() < 30, (got, TONES[:channels])


# streams, coupled streams and channel mapping of RFC 7845 family 1
OPUS_FAMILY_1 = {
    3: (2, 1, [0, 2, 1]),
    4: (2, 2, [0, 1, 2, 3]),
    5: (3, 2, [0, 4, 1, 2, 3]),
    6: (4, 2, [0, 4, 1, 2, 3, 5]),
    7: (4, 3, [0, 4, 1, 2, 3, 5, 6]),
    8: (5, 3, [0, 6, 1, 2, 3, 4, 5, 7]),
}


@pytest.mark.parametrize("channels", sorted(OPUS_FAMILY_1))
def test_opus_header_is_mapping_family_1(channels):
    out = _pydub2.encode(b"\0\0" * channels * 4800, 48000, channels, 2, "opus")
    head = out[out.index(b"OpusHead"):]
    streams, coupled, mapping = OPUS_FAMILY_1[channels]
    assert head[9] == channels and head[18] == 1
    assert (head[19], head[20], list(head[21:21 + channels])) == (streams, coupled, mapping)


@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_bitrate_changes_size(fmt):
    data = sine(48000, 2, secs=2, freq=3000)
    low = _pydub2.encode(data, 48000, 2, 2, fmt, bitrate=48000)
    high = _pydub2.encode(data, 48000, 2, 2, fmt, bitrate=256000)
    assert len(high) > len(low) * 1.5


@pytest.mark.parametrize("fmt", ["wav", "flac", "ogg", "opus"])
def test_empty(fmt):
    assert len(_pydub2.encode(b"", 44100, 2, 2, fmt)) > 0


# libopusenc 0.2.1 hit an assert here, in a child so an abort fails one test and not the run
def test_empty_opus_does_not_abort():
    code = (
        "import _pydub2\n"
        "for rate in (8000, 44100, 48000):\n"
        "    for channels in (1, 2, 6):\n"
        "        assert _pydub2.encode(b'', rate, channels, 2, 'opus')[:4] == b'OggS'\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    subprocess.run([sys.executable, "-c", code], env=env, check=True)


def test_unknown_format():
    with pytest.raises(ValueError):
        _pydub2.encode(sine(), 44100, 1, 2, "mp3")


def pcm24(width=3, channels=1, secs=0.5, rate=44100):
    wide = np.frombuffer(sine(rate, channels, 4, secs), "<i4")
    return wide.astype("<i4").view(np.uint8).reshape(-1, 4)[:, 4 - width:].tobytes()


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_24_bit_like_ffmpeg(channels):
    data = pcm24(3, channels)
    out = _pydub2.encode(data, 44100, channels, 3, "flac")
    assert "s32" in probe(out) or "24" in probe(out)
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s32le", "-"], input=out, capture_output=True, check=True)
    assert np.frombuffer(p.stdout, "<i4").view(np.uint8).reshape(-1, 4)[:, 1:].tobytes() == data


def test_flac_24_bit_header():
    out = _pydub2.encode(pcm24(), 44100, 1, 3, "flac")
    # streaminfo: bits per sample minus one is 23
    assert ((out[20] & 1) << 4 | out[21] >> 4) + 1 == 24


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_8_bit_roundtrip(channels):
    data = sine(8000, channels, 1)
    out = _pydub2.encode(data, 8000, channels, 1, "flac")
    p = subprocess.run([ff, "-v", "error", "-i", "-", "-f", "s16le", "-"], input=out, capture_output=True, check=True)
    assert np.frombuffer(p.stdout, "<i2").tolist() == [v * 256 for v in np.frombuffer(data, "i1").tolist()]


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("secs", [0, 0.5, 1 / 8000])
def test_wav_24_bit_like_wave_module(channels, secs):
    data = pcm24(3, channels, secs, 8000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(3)
        w.setframerate(8000)
        w.writeframes(data)
    out = _pydub2.encode(data, 8000, channels, 3, "wav")
    if len(data) % 2 and not WAVE_PADS:
        out = riff_without_pad(out[:-1])
    assert out == buf.getvalue()


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_24_bit_same_samples_as_pydub(channels):
    pydub = pytest.importorskip("pydub")
    data = pcm24(3, channels)
    seg = pydub.AudioSegment(data, frame_rate=44100, channels=channels, sample_width=3)
    cmd = [ff, "-v", "error", "-i", "-", "-f", "s32le", "-"]
    theirs = subprocess.run(cmd, input=seg.export(format="flac").read(), capture_output=True, check=True).stdout
    mine = subprocess.run(cmd, input=_pydub2.encode(data, 44100, channels, 3, "flac"), capture_output=True,
                          check=True).stdout
    assert mine == theirs


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_lossy_24_bit_same_as_16(fmt):
    wide = pcm24(4, 2)
    out = _pydub2.encode(pcm24(3, 2), 44100, 2, 3, fmt)
    ref = _pydub2.encode(wide, 44100, 2, 4, fmt)
    back = ffdecode(out, 44100, 2)
    assert abs(len(back) - len(ffdecode(ref, 44100, 2))) < 2000
    assert best_corr(array.array("h", np.frombuffer(wide, "<i4")[::2] >> 16), back[::2]) > 0.95


@pytest.mark.parametrize("fmt", ["wav", "flac", "ogg", "opus"])
@pytest.mark.parametrize("width", [0, 5, -1, 8])
def test_bad_width(fmt, width):
    with pytest.raises(ValueError):
        _pydub2.encode(b"\0" * 80, 8000, 1, width, fmt)


def music(rate, channels, secs=3):
    rng = np.random.default_rng(5)
    t = np.arange(int(rate * secs)) / rate
    x = sum(np.sin(2 * np.pi * f * t) * (1 + np.sin(2 * np.pi * r * t)) for f, r in [(220, 3), (660, 5), (1500, 7), (3100, 2)])
    x = x / 8 + rng.normal(0, 0.02, len(t))
    y = np.stack([x, np.roll(x, 40)], 1)[:, :channels]
    return (y * 20000).astype("<i2").tobytes()


def pydub_export(data, rate, channels, width, fmt, bitrate=None):
    pydub = pytest.importorskip("pydub")
    seg = pydub.AudioSegment(data, frame_rate=rate, channels=channels, sample_width=width)
    return seg.export(format=fmt, bitrate=bitrate).read()


@needs_ff
@pytest.mark.parametrize("bitrate", [None, "64k", "128k", "192k"])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_lossy_size_like_ffmpeg(fmt, rate, channels, bitrate):
    data = music(rate, channels)
    theirs = len(pydub_export(data, rate, channels, 2, fmt, bitrate))
    mine = len(_pydub2.encode(data, rate, channels, 2, fmt, bitrate=int(bitrate[:-1]) * 1000 if bitrate else 0))
    assert abs(mine / theirs - 1) < 0.06


@needs_ff
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_flac_size_like_ffmpeg(rate, channels):
    data = music(rate, channels)
    # ffmpeg adds 8 kB of padding that we leave out
    theirs = len(pydub_export(data, rate, channels, 2, "flac")) - 8192
    assert abs(len(_pydub2.encode(data, rate, channels, 2, "flac")) / theirs - 1) < 0.03


@needs_ff
@pytest.mark.parametrize("width", [1, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_flac_8_and_32_bit_like_pydub(width, channels):
    data = pcm24(4, channels) if width == 4 else sine(8000, channels, 1)
    rate = 44100 if width == 4 else 8000
    cmd = [ff, "-v", "error", "-i", "-", "-f", "s32le", "-"]
    theirs = subprocess.run(cmd, input=pydub_export(data, rate, channels, width, "flac"), capture_output=True,
                            check=True).stdout
    mine = _pydub2.encode(data, rate, channels, width, "flac")
    assert subprocess.run(cmd, input=mine, capture_output=True, check=True).stdout == theirs
    bits = ((mine[20] & 1) << 4 | mine[21] >> 4) + 1
    assert bits == (24 if width == 4 else 16)


@needs_ff
def test_flac_streaminfo_like_ffmpeg():
    data = music(44100, 2)
    mine = _pydub2.encode(data, 44100, 2, 2, "flac")
    theirs = pydub_export(data, 44100, 2, 2, "flac")
    # 4608 is what ffmpeg 6 and 7 pick at 44100, ffmpeg 8 picks 4096
    assert mine[8:12] == bytes.fromhex("12001200")
    assert mine[20:26] == theirs[20:26]
    assert mine[26:42] == theirs[26:42] != bytes(16)


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [16000, 44100, 48000])
@pytest.mark.parametrize("frames", [5000, 12345, 30001])
def test_lossy_length_like_ffmpeg(fmt, rate, channels, frames):
    data = music(rate, channels)[:frames * 2 * channels]
    mine = _pydub2.decode(_pydub2.encode(data, rate, channels, 2, fmt))
    theirs = _pydub2.decode(pydub_export(data, rate, channels, 2, fmt))
    assert mine[1:] == theirs[1:]
    assert len(mine[0]) == len(theirs[0])


def test_opus_header_and_tags():
    out = _pydub2.encode(sine(48000, 1, 2, 0.1), 48000, 1, 2, "opus")
    assert out[28:36] == b"OpusHead" and int.from_bytes(out[38:40], "little") == 312
    # no padding after the vendor string, like ffmpeg
    assert bytes(64) not in out[:300]


@pytest.mark.parametrize("frames", [1, 5000, 12345, 30001])
def test_vorbis_last_granule_is_the_input_length(frames):
    out = _pydub2.encode(sine(44100, 1, 2, 1)[:frames * 2], 44100, 1, 2, "ogg")
    assert int.from_bytes(out[out.rfind(b"OggS") + 6:][:8], "little") == frames


# what pydub gives back for these, ffmpeg keeps the end padding of a vorbis file for some page layouts
VORBIS_LENGTHS = [(8000, 1, 8000, 8000), (8000, 1, 8001, 8001), (8000, 2, 8000, 8000), (8000, 6, 2049, 2304),
                  (11025, 1, 11025, 11025), (11025, 6, 4097, 4352), (16000, 1, 160, 256), (16000, 2, 12345, 12544),
                  (22050, 2, 1, 256), (22050, 6, 12345, 12544), (32000, 2, 12345, 12416), (32000, 6, 4097, 4224),
                  (44100, 1, 441, 512), (44100, 2, 44100, 44100), (44100, 6, 12345, 12416), (48000, 2, 1001, 1024),
                  (48000, 6, 12345, 12416), (96000, 1, 96001, 96001), (96000, 2, 960, 1024), (96000, 6, 12345, 12416)]


def noise(rate, channels, frames):
    return np.random.default_rng(rate + channels + frames).normal(0, 3000, frames * channels).astype("<i2").tobytes()


@pytest.mark.parametrize("rate, channels, frames, want", VORBIS_LENGTHS)
def test_vorbis_round_trip_length(rate, channels, frames, want):
    seg = pydub2.AudioSegment(noise(rate, channels, frames), frame_rate=rate, channels=channels, sample_width=2)
    assert pydub2.AudioSegment.from_file(seg.export(format="ogg"), format="ogg").frame_count() == want


# libvorbis extrapolates back from the start with the audio it has by then, which can change the packets at the end
@pytest.mark.parametrize("rate, channels, frames, bitrate, kind, want", [
    (16000, 1, 3579, "64k", "sine", 3968), (16000, 2, 4685, "128k", "dc", 4429),
    (22050, 1, 1268, None, "sine", 1664), (22050, 2, 9001, "128k", "dc", 9344)])
def test_vorbis_round_trip_length_fed_like_ffmpeg(rate, channels, frames, bitrate, kind, want):
    x = np.full(frames, 9830) if kind == "dc" else (9830 * np.sin(2 * np.pi * 1000 * np.arange(frames) / rate)).astype(int)
    seg = pydub2.AudioSegment(np.repeat(x, channels).astype("<i2").tobytes(), frame_rate=rate, channels=channels,
                              sample_width=2)
    out = seg.export(format="ogg", bitrate=bitrate)
    assert pydub2.AudioSegment.from_file(out, format="ogg").frame_count() == want
    if ff:
        pydub = pytest.importorskip("pydub")
        theirs = pydub.AudioSegment(seg.raw_data, frame_rate=rate, channels=channels, sample_width=2)
        theirs = theirs.export(format="ogg", bitrate=bitrate, codec="libvorbis")
        assert pydub.AudioSegment.from_file(theirs, format="ogg").frame_count() == want


@needs_ff
@pytest.mark.parametrize("rate, channels, frames, want", VORBIS_LENGTHS)
def test_vorbis_round_trip_length_like_pydub(rate, channels, frames, want):
    pydub = pytest.importorskip("pydub")
    data = noise(rate, channels, frames)
    seg = pydub.AudioSegment(data, frame_rate=rate, channels=channels, sample_width=2)
    theirs = pydub.AudioSegment.from_file(seg.export(format="ogg", codec="libvorbis"), format="ogg")
    mine = pydub2.AudioSegment(data, frame_rate=rate, channels=channels, sample_width=2).export(format="ogg")
    assert pydub2.AudioSegment.from_file(mine, format="ogg").frame_count() == theirs.frame_count() == want


# libvorbis 1.3.7 shifts negative ints and reads past a noise curve from 44.1 kHz up, this fails under -fsanitize=undefined without cmake/vorbis_patch.cmake
@pytest.mark.parametrize("rate", [8000, 22050, 44100, 48000, 96000, 192000])
def test_vorbis_sets_up_at_every_rate(rate):
    out = _pydub2.encode(sine(rate, 2, 2, 0.1), rate, 2, 2, "ogg")
    assert _pydub2.decode(out)[1:3] == (rate, 2)
    assert int.from_bytes(out[out.rfind(b"OggS") + 6:][:8], "little") == rate // 10


def test_opus_refuses_tiny_rates():
    with pytest.raises(RuntimeError):
        _pydub2.encode(b"\0" * 400, 50, 1, 2, "opus")


def flac_blocks(out):
    assert out[:4] == b"fLaC"
    i, found = 4, []
    while True:
        last, kind, size = out[i] >> 7, out[i] & 127, int.from_bytes(out[i + 1:i + 4], "big")
        found.append((kind, size, last))
        i += 4 + size
        if last:
            return found, i


@pytest.mark.parametrize("tags", [[], [(b"title", b"x")], [(b"a", b"b" * 70000), (b"c", b"")]])
@pytest.mark.parametrize("width", [1, 2, 3])
@pytest.mark.parametrize("channels", [1, 2, 6])
def test_flac_metadata_is_well_formed(width, channels, tags):
    data = pcm24(width, channels) if width == 3 else sine(44100, channels, width, 0.3)
    out = _pydub2.encode(data, 44100, channels, width, "flac", 0, tags)
    found, end = flac_blocks(out)
    assert found[0][:2] == (0, 34)
    assert [k for k, _, _ in found].count(0) == 1 and [k for k, _, _ in found].count(4) == 1
    assert [last for _, _, last in found] == [0] * (len(found) - 1) + [1]
    assert out[end:end + 2] == b"\xff\xf8"


@pytest.mark.parametrize("tags", [[], [(b"title", b"x"), (b"artist", b"y")]])
@pytest.mark.parametrize("width", [1, 2, 3])
@pytest.mark.parametrize("channels", [1, 2, 6])
def test_flac_passes_strict_readers(tmp_path, width, channels, tags):
    data = pcm24(width, channels) if width == 3 else sine(44100, channels, width, 0.3)
    path = tmp_path / "a.flac"
    path.write_bytes(_pydub2.encode(data, 44100, channels, width, "flac", 0, tags))
    ran = 0
    for cmd in (["flac", "-t", "-s"], ["metaflac", "--list"], ["ffprobe", "-v", "error"]):
        if shutil.which(cmd[0]):
            p = subprocess.run(cmd + [str(path)], capture_output=True)
            assert p.returncode == 0 and not p.stderr, p.stderr
            ran += 1
    if not ran:
        pytest.skip("no flac or ffprobe")


def test_opus_resampled_inside_like_resampled_before():
    # the resampler inside libopusenc has a neon and a plain version, both have to land where the plain one did
    data = music(44100, 2)
    inside = _pydub2.encode(data, 44100, 2, 2, "opus")
    before = _pydub2.encode(_pydub2.resample(data, 44100, 2, 2, 48000), 48000, 2, 2, "opus")
    assert abs(len(inside) / len(before) - 1) < 0.06

    def pcm(out):
        return array.array("h", (np.frombuffer(_pydub2.decode(out)[0], "<i4") >> 16).astype("<i2").tobytes())

    a, b = pcm(inside), pcm(before)
    assert abs(len(a) - len(b)) < 48000 * 2 // 20
    assert best_corr(b[::2], a[::2]) > 0.95


def without_serial(out):
    # libopusenc picks a random stream serial, which also changes the page checksums
    out, i = bytearray(out), 0
    while out[i:i + 4] == b"OggS":
        n = out[i + 26]
        out[i + 14:i + 18] = out[i + 22:i + 26] = bytes(4)
        i += 27 + n + sum(out[i + 27:i + 27 + n])
    assert i == len(out)
    return bytes(out)


def needs_mp3(fmt):
    if fmt == "mp3" and not pydub2._mp3.available():
        pytest.skip("mp3 is only written without ffmpeg with pydub2-mp3")


# the encoder ffmpeg picks for each format when no codec is given
OWN_CODECS = [("ogg", "libvorbis"), ("opus", "libopus"), ("mp3", "libmp3lame"), ("flac", "flac")]


@pytest.mark.parametrize("fmt, codec", OWN_CODECS)
@pytest.mark.parametrize("width, channels", [(1, 1), (2, 2), (4, 1)])
@pytest.mark.parametrize("extra", [{}, {"bitrate": "96k"}, {"tags": {"title": "x"}}])
def test_own_codec_is_built_in(fmt, codec, width, channels, extra, monkeypatch, tmp_path):
    needs_mp3(fmt)
    monkeypatch.setattr(pydub2.AudioSegment, "converter", str(tmp_path / "no-ffmpeg"))
    seg = pydub2.AudioSegment(sine(44100, channels, width, 0.5), frame_rate=44100, channels=channels, sample_width=width)
    plain = seg.export(format=fmt, **extra).read()
    named = seg.export(format=fmt, codec=codec, **extra).read()
    if fmt == "opus":
        plain, named = without_serial(plain), without_serial(named)
    assert named == plain


@needs_ff
@pytest.mark.parametrize("fmt, codec", OWN_CODECS)
def test_own_codec_like_pydub(fmt, codec):
    needs_mp3(fmt)
    pydub = pytest.importorskip("pydub")
    data = music(44100, 2)
    mine = pydub2.AudioSegment(data, frame_rate=44100, channels=2, sample_width=2).export(format=fmt, codec=codec).read()
    theirs = pydub.AudioSegment(data, frame_rate=44100, channels=2, sample_width=2).export(format=fmt, codec=codec).read()
    a, b = ffdecode(mine, 44100, 2), ffdecode(theirs, 44100, 2)
    if fmt == "flac":
        assert a == b
    else:
        assert abs(len(a) - len(b)) < 44100 * 2 // 20
        assert best_corr(b[::2], a[::2]) > 0.95
    assert abs(len(mine) / len(theirs) - 1) < 0.1


@pytest.mark.parametrize("fmt, kwargs, cause", [
    ("ogg", {"codec": "vorbis"}, "the vorbis codec"),
    ("opus", {"codec": "opus"}, "the opus codec"),
    ("mp3", {"codec": "libshine"}, "the libshine codec"),
    ("flac", {"codec": "pcm_s16le"}, "the pcm_s16le codec"),
    ("wav", {"codec": "pcm_mulaw"}, "the pcm_mulaw codec"),
    ("mp3", {"cover": "cover.png"}, "the cover"),
    ("mp3", {"codec": "libmp3lame", "cover": "cover.png"}, "the cover"),
    ("ogg", {"parameters": ["-ar", "8000"]}, "ogg with these parameters"),
    # these codecs are built in for wav, so the parameters are the cause
    ("wav", {"codec": "pcm_s16le", "parameters": ["-ar", "8000"]}, "wav with these parameters"),
    ("wav", {"codec": "pcm_s24le", "parameters": ["-ac", "2"]}, "wav with these parameters"),
])
def test_ffmpeg_missing_names_the_cause(fmt, kwargs, cause, monkeypatch, tmp_path):
    monkeypatch.setattr(pydub2.AudioSegment, "converter", "ffmpeg")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    if "cover" in kwargs:
        kwargs = dict(kwargs, cover=str(tmp_path / kwargs["cover"]))
    with pytest.raises(FileNotFoundError, match="^ffmpeg not found, pydub2 needs it for %s. install ffmpeg" % cause) as e:
        pydub2.AudioSegment.silent(100).export(io.BytesIO(), format=fmt, **kwargs)
    assert e.value.format == fmt
