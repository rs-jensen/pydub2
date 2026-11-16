import io
import multiprocessing
import shutil
import struct
import time

import numpy as np
import pytest

import pydub2
from pydub2 import effects, utils

pydub = pytest.importorskip("pydub")
import pydub.effects
ff = shutil.which("ffmpeg")
needs_ff = pytest.mark.skipif(ff is None, reason="no ffmpeg")


def noise(ms=700, channels=1, rate=8000, width=2, seed=0):
    rng = np.random.default_rng(seed)
    n = rate * ms // 1000
    amp = np.repeat(rng.choice([0.01, 0.05, 0.6, 0.9], size=n // 400 + 1), 400)[:n]
    x = (rng.standard_normal(n * channels).reshape(n, channels) * amp[:, None] * 2 ** (8 * width - 3))
    return x.astype({1: "i1", 2: "<i2", 4: "<i4"}[width]).tobytes(), rate, width, channels


def both(*args):
    data, rate, width, channels = args
    kw = dict(sample_width=width, frame_rate=rate, channels=channels)
    return pydub2.AudioSegment(data=data, **kw), pydub.AudioSegment(data=data, **kw)


@pytest.mark.parametrize("args", [noise(), noise(channels=2, seed=1), noise(width=4, seed=2), noise(width=1, seed=3)])
def test_slow_loops_same_bytes(args):
    new, old = both(*args)
    for name, kw in [("low_pass_filter", dict(cutoff=900)), ("high_pass_filter", dict(cutoff=300)),
                     ("compress_dynamic_range", dict(threshold=-30.0, ratio=3.0, attack=2.0, release=20.0))]:
        assert getattr(effects, name)(new, **kw).raw_data == getattr(pydub.effects, name)(old, **kw).raw_data


def test_filter_empty_audio():
    for name in ("low_pass_filter", "high_pass_filter"):
        assert len(getattr(effects, name)(pydub2.AudioSegment.silent(10)[:0], 1000)) == 0


def overlay_empty_loop():
    seg = pydub2.AudioSegment.silent(100)
    assert seg.overlay(seg[:0], loop=True).raw_data == seg.raw_data


def test_overlay_empty_loop_does_not_hang():
    p = multiprocessing.get_context("spawn").Process(target=overlay_empty_loop)
    p.start()
    p.join(20)
    alive = p.is_alive()
    p.kill()
    assert not alive and p.exitcode == 0


def test_tiny_crossfade():
    seg = pydub2.AudioSegment.silent(100)
    assert len(seg.append(seg, crossfade=0.5)) == 200


def test_mediainfo_file_object():
    wav = pydub2.AudioSegment.silent(300).export(io.BytesIO(), format="wav").read()
    assert float(utils.mediainfo(io.BytesIO(wav))["duration"]) == pytest.approx(0.3, abs=0.01)


@needs_ff
def test_file_named_like_an_option(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pydub2.AudioSegment.silent(300).export("-x.m4a", format="m4a")
    assert len(pydub2.AudioSegment.from_file("-x.m4a")) > 250
    assert float(utils.mediainfo("-x.m4a")["duration"]) > 0.25


@needs_ff
def test_wma_format_name(tmp_path):
    seg = pydub2.AudioSegment.silent(500)
    out = seg.export(tmp_path / "x.wma", format="wma")
    assert len(pydub2.AudioSegment.from_file(out.name, "wma")) > 450


def test_append_in_a_loop():
    new, old = both(*noise(500, rate=22050))
    acc = new + new
    assert acc.raw_data == (old + old).raw_data
    t = time.perf_counter()
    for _ in range(2000):
        acc += new
    assert time.perf_counter() - t < 5
    assert len(acc.raw_data) == len(new.raw_data) * 2002


def test_24_bit_data_same_as_pydub():
    rng = np.random.default_rng(0)
    data = rng.integers(0, 256, 3 * 2 * 5000, dtype=np.uint8).tobytes()
    kw = dict(sample_width=3, frame_rate=8000, channels=2)
    assert pydub2.AudioSegment(data=data, **kw).raw_data == pydub.AudioSegment(data=data, **kw).raw_data


@pytest.mark.parametrize("mode", ["L+R", "M+S", "L", "R", "M", "S"])
def test_eq_channel_modes(mode):
    pytest.importorskip("scipy")
    import pydub.scipy_effects
    import pydub2.scipy_effects
    new, old = both(*noise(500, channels=2, seed=5))
    a = pydub2.scipy_effects.eq(new, 1000, 400, mode, "peak", 6)
    assert len(a) == len(new)
    if mode == "L+R":
        assert a.raw_data == pydub.scipy_effects.eq(old, 1000, 400, mode, "peak", 6).raw_data
    if mode == "L":
        assert a.split_to_mono()[1].raw_data == new.split_to_mono()[1].raw_data


@needs_ff
def test_wav_tags_with_codec():
    new, old = both(*noise(300))
    d = new.export(io.BytesIO(), format="wav", codec="pcm_s16le", tags={"title": "abc"}).getvalue()
    assert b"abc" in d
    assert d == old.export(io.BytesIO(), format="wav", codec="pcm_s16le", tags={"title": "abc"}).getvalue()


def chained_opus(tmp_path, second=1):
    for name, ch in (("a", 2), ("b", second)):
        pydub2.AudioSegment.silent(500).set_channels(ch).export(tmp_path / (name + ".opus"), format="opus")
    path = tmp_path / "chain.opus"
    path.write_bytes((tmp_path / "a.opus").read_bytes() + (tmp_path / "b.opus").read_bytes())
    return path


@needs_ff
def test_chained_opus_falls_back_to_ffmpeg(tmp_path):
    path = chained_opus(tmp_path)
    assert pydub2.AudioSegment.from_file(path).raw_data == pydub.AudioSegment.from_file(path).raw_data


def test_chained_opus_with_other_channels_needs_ffmpeg(tmp_path, monkeypatch):
    path = chained_opus(tmp_path)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(pydub2.AudioSegment, "converter", "ffmpeg")
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        pydub2.AudioSegment.from_file(path)
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        list(pydub2.AudioSegment.stream(path))


def test_chained_opus_without_ffmpeg(tmp_path, monkeypatch):
    path = chained_opus(tmp_path, 2)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(pydub2.AudioSegment, "converter", "ffmpeg")
    assert len(pydub2.AudioSegment.from_file(path)) == 1000
    assert len(sum(pydub2.AudioSegment.stream(path))) == 1000


@needs_ff
def test_chained_opus_goes_to_ffmpeg_when_there(tmp_path, monkeypatch):
    path = chained_opus(tmp_path, 2)
    seen = []
    to_wav = pydub2._ffmpeg.to_wav
    monkeypatch.setattr(pydub2._ffmpeg, "to_wav", lambda *a: seen.append(1) or to_wav(*a))
    assert len(pydub2.AudioSegment.from_file(path)) == 1000
    assert seen


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_corrupt_mp3_without_ffmpeg(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(pydub2.AudioSegment, "converter", "ffmpeg")
    with pytest.raises(pydub2.exceptions.CouldntDecodeError):
        pydub2.AudioSegment.from_file(io.BytesIO(b"not an mp3" * 100), "mp3")
    with pytest.raises(FileNotFoundError):
        pydub2.AudioSegment.from_file(io.BytesIO(b"not an m4a" * 100), "m4a")


@needs_ff
def test_flac_of_32_bit_like_pydub():
    new, old = both(*noise(300, width=4))
    a = pydub2.AudioSegment.from_file(new.export(io.BytesIO(), format="flac"), "flac")
    b = pydub.AudioSegment.from_file(old.export(io.BytesIO(), format="flac"), "flac")
    assert a.sample_width == b.sample_width and a.raw_data == b.raw_data


def test_rf64_info_without_ffprobe(monkeypatch):
    monkeypatch.setattr(utils, "_has_prober", lambda: False)
    pcm = bytes(400)
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 8000, 16000, 2, 16)
    ds64 = struct.pack("<4sIQQQI", b"ds64", 28, 0, len(pcm), len(pcm) // 2, 0)
    body = b"WAVE" + ds64 + fmt + struct.pack("<4sI", b"data", 0xFFFFFFFF) + pcm
    info = utils.mediainfo(io.BytesIO(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body))
    assert info["codec_name"] == "pcm_s16le"


def test_wav_ignores_bitrate():
    seg = pydub2.AudioSegment.silent(100)
    assert seg.export(io.BytesIO(), format="wav", bitrate="abc").getvalue()[:4] == b"RIFF"
    assert pydub.AudioSegment.silent(100).export(io.BytesIO(), format="wav", bitrate="abc").getvalue()[:4] == b"RIFF"


@pytest.mark.parametrize("kw", [dict(seek_step=0), dict(seek_step=-1), dict(seek_step=-3), dict(seek_step=1.5),
                                dict(min_silence_len=-5, seek_step=-1), dict(min_silence_len=-5)])
@pytest.mark.parametrize("n", [0, 1, 300])
def test_detect_silence_odd_steps(n, kw):
    from pydub import silence as old_silence
    from pydub2 import silence as new_silence
    new, old = both(b"\x01\x00" * n, 8000, 2, 1)

    def run(f, seg):
        try:
            return f(seg, **kw)
        except Exception as e:
            return "raises"

    for name in ("detect_silence", "detect_nonsilent"):
        assert run(getattr(new_silence, name), new) == run(getattr(old_silence, name), old)


def test_speedup_one_frame():
    new, old = both(b"\x7f", 3, 1, 1)
    assert pydub.AudioSegment(data=b"\x7f", sample_width=1, frame_rate=3, channels=1).speedup().raw_data == b""
    assert new.speedup().raw_data == b""


def test_wav_with_float_rate():
    new, old = both(bytes(20), 4, 2, 1)
    a = new._spawn(new._data, overrides={"frame_rate": 3.5}).export(io.BytesIO(), format="wav").getvalue()
    b = old._spawn(old._data, overrides={"frame_rate": 3.5}).export(io.BytesIO(), format="wav").getvalue()
    assert a == b


def test_errors_are_value_errors():
    seg = pydub2.AudioSegment(b"\xff\xff\xff\x7f", sample_width=4, frame_rate=100, channels=1)
    with pytest.raises(ValueError):
        pydub2.AudioSegment(b"\x00\x00\x00\x80", sample_width=4, frame_rate=100, channels=1).remove_dc_offset()
    with pytest.raises(ValueError):
        seg.set_sample_width(5)
    with pytest.raises(ValueError):
        seg.set_frame_rate(0)
    with pytest.raises(ValueError):
        pydub2.AudioSegment(b"\x01" * 7717, sample_width=1, frame_rate=8000, channels=1).set_channels(3).get_dc_offset()


@pytest.mark.parametrize("rate", [3.5, 7.9, 44100.0, 44100.5])
def test_float_frame_rate_silence(rate):
    from pydub import silence as old_silence
    from pydub2 import silence as new_silence
    for data in (bytes(2000), noise(50)[0], b"\x01\x00" * 500 + bytes(1000)):
        new, old = both(data, rate, 2, 1)
        for name in ("detect_silence", "detect_nonsilent"):
            assert getattr(new_silence, name)(new, 5, -30) == getattr(old_silence, name)(old, 5, -30)
        a = [c.raw_data for c in new_silence.split_on_silence(new, 5, -30)]
        b = [c.raw_data for c in old_silence.split_on_silence(old, 5, -30)]
        assert a == b


def raises(f):
    try:
        f()
    except Exception as e:
        return type(e).__name__
    return None


def test_strip_silence_at_7_hz_returns_where_pydub_divides_by_zero():
    new, old = both(b"\x80\x80", 7, 1, 1)
    kw = dict(silence_len=20, silence_thresh=-3.5, padding=10)
    assert raises(lambda: old.strip_silence(**kw)) == "ZeroDivisionError"
    assert new.strip_silence(**kw).raw_data == b"\xff\x80"


@pytest.mark.parametrize("args", [(bytes(2050), 7, 1, 2), noise(300)])
def test_speedup_chunk_size_1_raises_like_pydub(args):
    new, old = both(*args)
    start = time.time()
    assert raises(lambda: new.speedup(chunk_size=1)) == "ZeroDivisionError" == raises(lambda: old.speedup(chunk_size=1))
    assert time.time() - start < 5


def test_rms_of_32_bit_does_not_fuse_the_squares():
    data = (2122901605).to_bytes(4, "little") * 7
    total = 0.0
    for _ in range(7):
        total += 2122901605.0 * 2122901605.0
    assert pydub2.AudioSegment(data, sample_width=4, frame_rate=16000, channels=1).rms == int((total / 7) ** 0.5)


@pytest.mark.parametrize("call", [lambda s: s.set_sample_width(-1), lambda s: s.set_sample_width(-5), lambda s: s.set_sample_width(5),
                                  lambda s: s.set_channels(0), lambda s: s.set_channels(-1), lambda s: s.set_frame_rate(-5),
                                  lambda s: s.set_frame_rate(0)])
def test_bad_width_channels_rate_are_value_errors(call):
    new, old = both(bytes(400), 8000, 2, 1)
    assert raises(lambda: call(new)) == "ValueError"
    assert raises(lambda: call(old)) in ("ValueError", "error")


@pytest.mark.parametrize("name, args", [("rms", (-1,)), ("max", (-1,)), ("mul", (-1, 1.0)), ("lowpass", (-1, 1, 0.5)),
                                        ("highpass", (2, -1, 0.5)), ("add", (b"\0" * 8, -1)), ("lin2lin", (2, -1)),
                                        ("lin2lin", (-1, 2)), ("tomono", (-1, 0.5, 0.5)), ("tostereo", (-1, 0.5, 0.5)),
                                        ("downmix", (2, -1)), ("reverse", (-1,)), ("avg", (-1,)), ("bias", (-1, 1)),
                                        ("ratecv", (-1, 1, 8000, 4000)), ("ratecv", (2, -1, 8000, 4000))])
def test_negative_width_or_channels_in_the_core(name, args):
    import _pydub2
    with pytest.raises(ValueError):
        getattr(_pydub2, name)(bytes(16), *args)


@pytest.mark.parametrize("kw", [dict(playback_speed=1), dict(playback_speed=0.5), dict(crossfade=-1), dict(playback_speed=0.5, chunk_size=500)])
def test_speedup_with_a_negative_crossfade_matches_pydub(kw):
    new, old = both(*noise(100, rate=22050))
    assert raises(lambda: new.speedup(**kw)) == raises(lambda: old.speedup(**kw))
    if not raises(lambda: old.speedup(**kw)):
        assert new.speedup(**kw).raw_data == old.speedup(**kw).raw_data
