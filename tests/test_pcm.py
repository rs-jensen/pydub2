import os
import platform
import random
import subprocess
import sys

import pytest

import _pydub2 as core

audioop = pytest.importorskip("audioop")

LIMITS = {1: (-128, 127), 2: (-32768, 32767), 3: (-2**23, 2**23 - 1), 4: (-2**31, 2**31 - 1)}
WIDTHS = [1, 2, 3, 4]
FACTORS = [0, 1, -1, 0.5, 1.5, 2, -2.7, 0.333, 3.7, 100]


def pack(width, vals):
    return b"".join(v.to_bytes(width, "little", signed=True) for v in vals)


def buffers(width, mult=1, seed=0):
    rnd = random.Random(seed)
    lo, hi = LIMITS[width]
    out = [b"", pack(width, [lo] * mult), pack(width, [hi] * mult), pack(width, [lo, hi] * mult),
           pack(width, [0] * 3 * mult)]
    for n in (1, 2, 7, 500):
        out.append(pack(width, [rnd.randint(lo, hi) for _ in range(n * mult)]))
        out.append(pack(width, [rnd.randint(lo // 50, hi // 50) for _ in range(n * mult)]))
    return out


def same(a, b):
    assert a == b


@pytest.mark.parametrize("width", WIDTHS)
def test_reductions(width):
    for buf in buffers(width):
        assert core.rms(buf, width) == audioop.rms(buf, width)
        assert core.max(buf, width) == audioop.max(buf, width)
        assert core.avg(buf, width) == audioop.avg(buf, width)


@pytest.mark.parametrize("width", WIDTHS)
def test_mul_bias_reverse(width):
    for buf in buffers(width):
        same(core.reverse(buf, width), audioop.reverse(buf, width))
        for f in FACTORS:
            same(core.mul(buf, width, f), audioop.mul(buf, width, f))
        for b in (0, 1, -1, 100, -70000, 2**31 - 1, -2**31):
            same(core.bias(buf, width, b), audioop.bias(buf, width, b))


@pytest.mark.parametrize("width", WIDTHS)
def test_add(width):
    for a, b in zip(buffers(width, seed=1), buffers(width, seed=2)):
        n = min(len(a), len(b))
        same(core.add(a[:n], b[:n], width), audioop.add(a[:n], b[:n], width))
    lo, hi = LIMITS[width]
    a, b = pack(width, [lo, hi, lo, hi]), pack(width, [lo, hi, hi, lo])
    same(core.add(a, b, width), audioop.add(a, b, width))


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("new", WIDTHS)
def test_lin2lin(width, new):
    for buf in buffers(width):
        same(core.lin2lin(buf, width, new), audioop.lin2lin(buf, width, new))


def test_rms_32_bit_sum_like_audioop():
    for vals in ([1608362628, 1608362630, -1608362626], [2017695478, 2017695481, 2017695479, 2017695478]):
        buf = pack(4, vals)
        same(core.rms(buf, 4), audioop.rms(buf, 4))


PROBE = pack(4, [2122901605] * 7)
FUSED, PLAIN = 2122901604, 2122901605


def rms_with(setup):
    code = "import sys, _pydub2\nassert 'audioop' not in sys.modules\n%s\nprint(_pydub2.rms(%r, 4))" % (setup, PROBE)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    return int(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize("want", [FUSED, PLAIN])
def test_rms_follows_the_audioop_that_is_there(want):
    fake = "import types\nm = types.ModuleType('audioop')\nm.rms = lambda d, w: %d\nsys.modules['audioop'] = m" % want
    assert rms_with(fake) == want


def test_rms_without_audioop_follows_the_platform():
    arm = platform.machine().lower() in ("arm64", "aarch64") and sys.platform != "win32"
    fused = arm and (sys.platform == "darwin" or sys.version_info >= (3, 13))
    assert rms_with("sys.modules['audioop'] = None") == (FUSED if fused else PLAIN)


@pytest.mark.parametrize("width", WIDTHS)
def test_mono_stereo(width):
    # some audioop builds fuse the two products in tomono, that is not plain double math
    probe = pack(2, [-27363, 10797])
    same(core.tomono(probe, 2, 0.3, 0.7), pack(2, [-651]))
    fused = audioop.tomono(probe, 2, 0.3, 0.7) != pack(2, [-651])
    for buf in buffers(width, 2):
        for l, r in [(0.5, 0.5), (1, 0), (0, 1), (1, 1), (-1.5, 2.3), (0.3, 0.7)]:
            if not fused:
                same(core.tomono(buf, width, l, r), audioop.tomono(buf, width, l, r))
    for buf in buffers(width):
        for l, r in [(1, 1), (1, 0), (0.5, 2), (-1.3, 3.1), (100, -100)]:
            same(core.tostereo(buf, width, l, r), audioop.tostereo(buf, width, l, r))


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("channels", [1, 2, 3])
def test_ratecv(width, channels):
    pairs = [(44100, 16000), (16000, 44100), (48000, 44100), (8000, 8000), (22050, 44100), (44100, 22050),
             (1, 3), (7, 2), (100, 1)]
    for buf in buffers(width, channels):
        for a, b in pairs:
            same(core.ratecv(buf, width, channels, a, b), audioop.ratecv(buf, width, channels, a, b, None)[0])


def test_errors():
    for bad in (0, 5):
        with pytest.raises(ValueError):
            core.rms(b"\0" * 12, bad)
    with pytest.raises(ValueError):
        core.max(b"\0" * 3, 2)
    with pytest.raises(ValueError):
        core.add(b"\0\0", b"\0\0\0\0", 2)
    with pytest.raises(ValueError):
        core.tomono(b"\0" * 6, 2, 1, 1)
    with pytest.raises(ValueError):
        core.ratecv(b"\0" * 6, 2, 2, 1, 2)
    with pytest.raises(ValueError):
        core.ratecv(b"\0" * 4, 2, 1, 0, 2)
    with pytest.raises(ValueError):
        core.lin2lin(b"\0" * 4, 2, 5)


def test_avg_of_every_other_sample_refuses_a_step_that_wraps():
    data = b"\1\0\2\0\3\0"
    assert core.avg(data, 2, 2, 1) == 2 and core.avg(data, 2, 1 << 62, 2) == 3
    for step, first in ((-1, 0), (0, 0), (1, -1)):
        with pytest.raises(ValueError):
            core.avg(data, 2, step, first)


def test_interleave_refuses_a_size_that_wraps():
    with pytest.raises(MemoryError):
        core.interleave([b"\0" * 4] * 4, 4, 1 << 62)


@pytest.mark.parametrize("channels", [0])
@pytest.mark.parametrize("data", [b"", b"\0" * 8])
def test_no_channels(channels, data):
    for call in (lambda: core.lowpass(data, 2, channels, 0.5), lambda: core.highpass(data, 2, channels, 0.5),
                 lambda: core.compress(data, 2, channels, 1000.0, 2.0, 10.0, 10.0, 100)):
        with pytest.raises(ValueError):
            call()
