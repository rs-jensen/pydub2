import copy
import decimal
import gc
import io
import multiprocessing
import pickle
import shutil
import struct
import time
import tracemalloc

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
    assert a.raw_data != new.raw_data
    if mode == "L+R":
        assert a.raw_data == pydub.scipy_effects.eq(old, 1000, 400, mode, "peak", 6).raw_data
    if mode == "L":
        assert a.split_to_mono()[1].raw_data == new.split_to_mono()[1].raw_data


GAIN = {"L+R": (7.6, 0), "M+S": (10.6, 3), "L": (5.3, 0), "R": (5.3, 0), "M": (10.6, 3), "S": (3, 3)}


@pytest.mark.parametrize("mode", GAIN)
def test_eq_gain_at_the_centre_and_away(mode):
    pytest.importorskip("scipy")
    import pydub2.scipy_effects
    from pydub2.generators import Sine
    for freq, gain in zip((1000, 6000), GAIN[mode]):
        seg = Sine(freq, sample_rate=44100).to_audio_segment(1000, volume=-20).set_channels(2)
        out = pydub2.scipy_effects.eq(seg, 1000, 400, mode, "peak", 6)
        assert abs((out.dBFS - seg.dBFS) - gain) < 0.3, (mode, freq)


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
    assert raises(lambda: new.speedup(chunk_size=1)) == "ZeroDivisionError" == raises(lambda: old.speedup(chunk_size=1))


def test_rms_of_32_bit_sums_like_the_audioop_of_the_platform():
    audioop = pytest.importorskip("audioop")
    data = (2122901605).to_bytes(4, "little") * 7
    assert pydub2.AudioSegment(data, sample_width=4, frame_rate=16000, channels=1).rms == audioop.rms(data, 4)


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


def decoded(seg, **kw):
    return pydub.AudioSegment.from_file(seg.export(io.BytesIO(), **kw), format="ogg" if kw["format"] == "opus" else kw["format"])


@needs_ff
@pytest.mark.parametrize("fmt", ["mp3", "opus"])
def test_exporting_an_empty_segment_matches_pydub(fmt):
    new, old = (m.AudioSegment.empty().export(io.BytesIO(), format=fmt).read() for m in (pydub2, pydub))
    assert new and old


@needs_ff
@pytest.mark.parametrize("kw", [dict(start_second="0.1"), dict(start_second="00:00:01"),
                                dict(start_second=decimal.Decimal("0.1"))])
def test_start_second_and_duration_as_text_match_pydub(kw):
    wav = io.BytesIO()
    both(*noise(2000, rate=8000))[1].export(wav, format="wav")
    new, old = (m.AudioSegment.from_file(io.BytesIO(wav.getvalue()), format="wav", **kw) for m in (pydub2, pydub))
    assert new.raw_data == old.raw_data


@needs_ff
@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_bitrate_with_stray_whitespace_matches_pydub(fmt):
    new, old = both(*noise(300, rate=48000))
    assert len(decoded(new, format=fmt, bitrate="64k ")) == len(decoded(old, format=fmt, bitrate="64k "))


@needs_ff
@pytest.mark.parametrize("fmt", ["wav", "mp3", "flac", "ogg"])
def test_export_of_a_wav_with_stray_bytes_matches_pydub(fmt, tmp_path):
    data = b"\1\2\3\4" * 1000 + b"\5\6\7"
    path = tmp_path / "stray.wav"
    path.write_bytes(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, 2,
                                 44100, 176400, 4, 16, b"data", len(data)) + data)
    new, old = (m.AudioSegment.from_file(str(path)) for m in (pydub2, pydub))
    assert len(decoded(new, format=fmt)) == len(decoded(old, format=fmt))


def append_loop_peak(m):
    gc.collect()
    tracemalloc.start()
    try:
        out = m.AudioSegment.empty()
        for i in range(40):
            out += m.AudioSegment(bytes([i]) * (1 << 18), sample_width=2, frame_rate=44100, channels=2)
        len(out.raw_data)
        out.reverse()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_append_loop_does_not_keep_the_parts():
    assert append_loop_peak(pydub2) < 1.2 * append_loop_peak(pydub)


JOINS = [lambda a, b: a + b, lambda a, b: sum([a, b]), lambda a, b: a.append(b, crossfade=0)]


@pytest.mark.parametrize("join", JOINS)
def test_joined_segment_after_first_read_has_data_in_dict_and_no_parts(join):
    new, old = both(*noise(300))
    x, y = join(new, new), join(old, old)
    assert x.raw_data == y.raw_data
    assert vars(x)["_data"] == vars(y)["_data"] and "_parts" not in vars(x)
    assert sorted(vars(x)) == sorted(vars(y))


def subclass(m, **body):
    return type("K", (m.AudioSegment,), body)


def data_property(m):
    def get(self):
        return self.__dict__.get("_d", b"")

    def put(self, v):
        self.__dict__["_d"] = v

    return subclass(m, _data=property(get, put))


def own_init(m):
    def __init__(self, data=None, *args, **kwargs):
        self.seen = len(data)
        m.AudioSegment.__init__(self, data, *args, **kwargs)

    return subclass(m, __init__=__init__)


def own_spawn(m):
    def _spawn(self, data, overrides={}):
        out = m.AudioSegment._spawn(self, data, overrides)
        out.seen = len(data)
        return out

    return subclass(m, _spawn=_spawn)


@pytest.mark.parametrize("kind", [data_property, own_init, own_spawn])
@pytest.mark.parametrize("join", JOINS)
def test_joined_subclass_same_as_pydub(kind, join):
    data, rate, width, channels = noise(300)
    new, old = (kind(m)(data, sample_width=width, frame_rate=rate, channels=channels) for m in (pydub2, pydub))
    x, y = join(new, new), join(old, old)
    assert type(x) is type(new) and vars(x) == vars(y)


@pytest.mark.parametrize("dup", [lambda x: pickle.loads(pickle.dumps(x)), copy.copy, copy.deepcopy])
def test_copy_of_summed_segment(dup):
    new, old = both(*noise(300))
    x, y = dup(sum([new, new, new])), dup(sum([old, old, old]))
    assert vars(x) == vars(y)


# pydub empties the file first, pydub2 encodes before it opens the file
@pytest.mark.parametrize("fmt, kw", [("ogg", dict(bitrate="k")), ("flac", dict(tags={"a": "b\0"})),
                                     ("mp3", dict(codec="nonexistent_codec")), ("m4a", dict(parameters=["-nope"]))])
def test_failed_export_keeps_the_old_file(fmt, kw, tmp_path):
    if fmt in ("mp3", "m4a") and ff is None:
        pytest.skip("no ffmpeg")
    path = tmp_path / ("old." + fmt)
    path.write_bytes(b"x" * 1000)
    with pytest.raises(Exception):
        pydub2.AudioSegment.silent(100).export(str(path), format=fmt, **kw)
    assert path.read_bytes() == b"x" * 1000


@needs_ff
@pytest.mark.parametrize("data", [b"", b"RIFF" + b"\0" * 40, b"fLaC" + b"\1" * 40], ids=["empty", "wav", "flac"])
def test_mediainfo_of_a_damaged_file_without_ffmpeg_matches_pydub(data, tmp_path, monkeypatch):
    path = tmp_path / "bad.wav"
    path.write_bytes(data)
    old = pydub.utils.mediainfo_json(str(path)), pydub.utils.mediainfo(str(path))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(pydub2.AudioSegment, "converter", "ffmpeg")
    assert (utils.mediainfo_json(str(path)), utils.mediainfo(str(path))) == old == ({}, {})


def test_stream_says_what_is_wrong_with_the_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        pydub2.AudioSegment.stream(str(tmp_path / "missing.mp3"))
    (tmp_path / "dir.wav").mkdir()
    with pytest.raises(OSError):
        pydub2.AudioSegment.stream(str(tmp_path / "dir.wav"))
    (tmp_path / "empty.wav").write_bytes(b"")
    with pytest.raises(pydub2.exceptions.CouldntDecodeError, match="^cant read this file$"):
        pydub2.AudioSegment.stream(str(tmp_path / "empty.wav"))
    with pytest.raises(pydub2.exceptions.CouldntDecodeError, match="^stream only reads"):
        pydub2.AudioSegment.stream(io.BytesIO(b"\0" * 1000))


@needs_ff
def test_bad_ffmpeg_timeout_and_ffprobe_answer_are_named(tmp_path, monkeypatch):
    path = str(tmp_path / "a.m4a")
    pydub2.AudioSegment.silent(100).export(path, format="m4a")
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", "60")
    for call in (lambda: pydub2.AudioSegment.from_file(path), lambda: pydub2.AudioSegment.silent(100).export(format="m4a")):
        with pytest.raises(ValueError, match="^pydub2.ffmpeg_timeout has to be None or a number"):
            call()
    monkeypatch.setattr(pydub2, "ffmpeg_timeout", None)
    data = b"\0" * 400
    (tmp_path / "c0.wav").write_bytes(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16,
                                                  1, 0, 44100, 176400, 4, 16, b"data", len(data)) + data)
    with pytest.raises(pydub2.exceptions.CouldntDecodeError, match="ffprobe gave no usable answer"):
        pydub2.AudioSegment.from_file(str(tmp_path / "c0.wav"))


@needs_ff
def test_ffmpeg_errors_carry_the_command_and_output_like_pydub():
    def errors(m):
        out = []
        for call in (lambda: m.AudioSegment.silent(10).export(io.BytesIO(), format="nosuchfmt"),
                     lambda: m.AudioSegment.from_file(io.BytesIO(b"garbage" * 100))):
            with pytest.raises(m.exceptions.PydubException) as e:
                call()
            out.append(str(e.value))
        return out
    (new_enc, new_dec), (old_enc, old_dec) = errors(pydub2), errors(pydub)
    assert new_enc.split("Command:[")[0] == old_enc.split("Command:[")[0]
    assert "-f', 'nosuchfmt'" in new_enc and "nosuchfmt" in new_enc.split("Output from ffmpeg/avlib:\n\n")[1]
    assert new_dec.split("ffmpeg version")[0] == old_dec.split("ffmpeg version")[0]
    assert old_dec.splitlines()[-1] in new_dec
