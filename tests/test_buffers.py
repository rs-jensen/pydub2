import array

import numpy as np
import pytest

import pydub2

pydub = pytest.importorskip("pydub")

RAW = array.array("h", [(i * 37) % 2000 - 1000 for i in range(400)])
KW = dict(sample_width=2, frame_rate=8000, channels=1)

DATA = {
    "bytes": lambda: RAW.tobytes(),
    "bytearray": lambda: bytearray(RAW.tobytes()),
    "memoryview": lambda: memoryview(RAW.tobytes()),
    "memoryview slice": lambda: memoryview(RAW.tobytes())[10:],
    "memoryview stride": lambda: memoryview(RAW.tobytes())[::2],
    "array h": lambda: RAW,
    "array B": lambda: array.array("B", range(100)),
    "int16": lambda: np.array(RAW, dtype="int16"),
    "int16 2d": lambda: np.array(RAW, dtype="int16").reshape(200, 2),
    "int16 step": lambda: np.array(RAW, dtype="int16")[::2],
    "big endian": lambda: np.array(RAW, dtype=">i2"),
    "float32": lambda: np.array(RAW, dtype="float32"),
    "uint8": lambda: np.arange(100, dtype="uint8"),
    "bool": lambda: np.array([True, False] * 50),
    "fortran": lambda: np.asfortranarray(np.arange(20, dtype="int16").reshape(10, 2)),
    "list": lambda: [1, 2, 3, 4],
    "str": lambda: "abcd",
    "empty": lambda: b"",
}


def outcome(make):
    try:
        r = make()
        return bytes(r.raw_data), r.sample_width, r.channels, r.frame_rate
    except Exception as e:
        return type(e).__name__


@pytest.mark.parametrize("name", DATA)
@pytest.mark.parametrize("how", ["new", "spawn", "spawn_overrides"])
def test_buffer_data_like_pydub(name, how):
    def run(lib):
        base = lib.AudioSegment(RAW.tobytes(), **KW)
        data = DATA[name]()
        return outcome({
            "new": lambda: lib.AudioSegment(data=data, **KW),
            "spawn": lambda: base._spawn(data),
            "spawn_overrides": lambda: base._spawn(data, overrides={"frame_rate": 4000}),
        }[how])
    assert run(pydub2) == run(pydub)


def seg(lib, width=2, channels=1, frames=400):
    code = {1: "b", 2: "h", 4: "i"}[width]
    vals = [(i * 37) % 100 - 50 for i in range(frames * channels)]
    return lib.AudioSegment(array.array(code, vals).tobytes(), sample_width=width, frame_rate=8000, channels=channels)


@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [1, 2, 3])
@pytest.mark.parametrize("override", [None, "f", "d", "B", "q"])
def test_array_typecodes_like_pydub(width, channels, override):
    got = [seg(lib, width, channels).get_array_of_samples(*([override] if override else [])) for lib in (pydub2, pydub)]
    assert (got[0].typecode, got[0].itemsize, got[0].tobytes()) == (got[1].typecode, got[1].itemsize, got[1].tobytes())
    assert np.asarray(got[0]).dtype == np.asarray(got[1]).dtype


@pytest.mark.parametrize("frames", [[400, 400], [400, 399], [400, 397], [400, 384], [399, 400], [400, 400, 400], [400]])
@pytest.mark.parametrize("width", [1, 2, 4])
def test_from_mono_audiosegments_like_pydub(frames, width):
    got = [outcome(lambda lib=lib: lib.AudioSegment.from_mono_audiosegments(*[seg(lib, width, 1, n) for n in frames]))
           for lib in (pydub2, pydub)]
    assert got[0] == got[1]
