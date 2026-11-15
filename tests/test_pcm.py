import random
import struct

import pytest

import _pydub2 as core

audioop = pytest.importorskip("audioop")

CODE = {1: "b", 2: "h", 4: "i"}
LIMITS = {1: (-128, 127), 2: (-32768, 32767), 4: (-2**31, 2**31 - 1)}
WIDTHS = [1, 2, 4]
FACTORS = [0, 1, -1, 0.5, 1.5, 2, -2.7, 0.333, 3.7, 100]


def pack(width, vals):
    return struct.pack("<%d%s" % (len(vals), CODE[width]), *vals)


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


def near(a, b, width):
    # the two products in tomono round differently with and without fma
    n = len(a) // width
    x = struct.unpack("<%d%s" % (n, CODE[width]), a)
    y = struct.unpack("<%d%s" % (n, CODE[width]), b)
    assert len(a) == len(b) and all(abs(i - j) <= 1 for i, j in zip(x, y))


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


@pytest.mark.parametrize("width", WIDTHS)
def test_mono_stereo(width):
    for buf in buffers(width, 2):
        for l, r in [(0.5, 0.5), (1, 0), (0, 1), (1, 1), (-1.5, 2.3), (0.3, 0.7)]:
            near(core.tomono(buf, width, l, r), audioop.tomono(buf, width, l, r), width)
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
    for bad in (0, 3, 5):
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
        core.lin2lin(b"\0" * 4, 2, 3)
