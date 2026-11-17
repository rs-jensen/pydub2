# pydub2

pydub2 is a drop-in replacement for pydub. It has a C++ core that reads and writes the common
audio formats without ffmpeg, and it works on Python 3.13 and newer where the audioop module is gone.
There are wheels for Python 3.9 to 3.14 on 64 bit Linux, macOS and Windows.

## Install

    pip install pydub2

To move existing code over, change the import:

```python
import pydub2 as pydub
# or
from pydub2 import AudioSegment
```

## Example

The API is the one you know from pydub.

```python
from pydub2 import AudioSegment

song = AudioSegment.from_file("song.wav")
first = song[:2000]
louder = first + 6
louder.fade_out(500).export("intro.flac", format="flac")
```

## Formats

| Format | Read | Write |
|---|---|---|
| wav | built in | built in |
| flac | built in | built in |
| ogg vorbis | built in | built in |
| opus | built in | built in |
| mp3 | built in | `pydub2-mp3` package, or ffmpeg |
| m4a/aac, wma, video files | ffmpeg | ffmpeg |

The ffmpeg rows need ffmpeg 3.3 or newer installed. Everything marked built in works without it. That includes
writing tags to flac, ogg and opus files and reading tags with `mediainfo`.

To write mp3 without ffmpeg, install the small `pydub2-mp3` package, which carries the LAME encoder:

    pip install "pydub2[mp3]"

```python
seg.export("out.mp3", format="mp3")
```

If you have no ffmpeg and do not want to install one, this pulls in a ready made ffmpeg binary from the
`imageio-ffmpeg` package, so m4a, aac, wma and video files work right away:

    pip install "pydub2[ffmpeg]"

pydub2 uses that binary only when it finds no ffmpeg on `PATH` and `AudioSegment.converter` is not set. The binary
is built by others and has its own license, which is the GPL for the builds `imageio-ffmpeg` ships.
There is no wheel for Alpine Linux (musl). The package has no ffprobe, `mediainfo` then gives the codec and sample size only.

If ffmpeg is there, pydub2 uses ffmpeg for the things the built in code does not do, such as a
chained vorbis file or an mp3 export with unusual `parameters`.

## New compared with pydub

Read a long file piece by piece. `AudioSegment.stream` yields `chunk_ms` long segments and holds
only the current one in memory. It reads wav, flac, mp3, ogg and opus.

```python
for chunk in AudioSegment.stream("long.flac", chunk_ms=10000):
    print(chunk.dBFS)
```

Go to and from numpy. `to_numpy()` gives an array of shape `(frames, channels)` in the segment's
integer type. `from_numpy` takes an int8, int16 or int32 array, or floats between -1 and 1, which become 16 bit:

```python
x = seg.to_numpy()
seg2 = AudioSegment.from_numpy(x, seg.frame_rate)
```

Read the stream info of a file with `mediainfo`. For wav, flac, ogg, opus and mp3 this works
without ffprobe.

```python
from pydub2 import utils
utils.mediainfo("song.wav")["sample_rate"]
```

Integrated loudness in LUFS (EBU R128):

```python
seg.loudness()
```

Normalize to a loudness target:

```python
from pydub2 import effects
effects.normalize(seg, target_lufs=-16)
```

The gain is held back when the loudest sample would pass full scale minus `headroom` (0.1 dB by default), so a quiet file with sharp peaks ends up below the target and does not clip.

Resample with libsamplerate, which sounds better than the default ratecv:

```python
seg.set_frame_rate(16000, resample_quality="high")
```

Find where somebody is talking, or split on it. This uses the Silero VAD model built into the
C++ core, so you do not need onnxruntime or torch.

```python
from pydub2 import silence
silence.detect_speech(seg)    # [[start_ms, end_ms], ...]
silence.split_on_speech(seg)  # list of AudioSegment
```

## Compatibility

Samples are kept as the same integer pcm that pydub uses. `raw_data`, `dBFS` and
`get_array_of_samples()` give the same numbers as pydub 0.25.1. A copy of pydub's own test
suite lives in `tests/pydub_suite` and runs against pydub2. Its test audio is made the first time
it runs, so it needs ffmpeg.

There are a few deliberate differences. For example `from_file` decodes a file straight from disk, and mp3 and opus
samples can differ slightly from what ffmpeg gives. They are listed in [docs/differences.md](https://github.com/rs-jensen/pydub2/blob/main/docs/differences.md).

## Exactly like pydub

Set `pydub2.strict = True`, or start Python with `PYDUB2_STRICT=1`, and every load and export that pydub sends to ffmpeg goes to ffmpeg here too, with the command line pydub builds. `raw_data`, `frame_rate`, `channels` and `sample_width` are then the same bytes as pydub 0.25.1 gives, and exported files are the same bytes as pydub writes on the same machine. A wav that pydub reads itself is read the way pydub reads it.

It needs ffmpeg and ffprobe. Without them the call raises the error pydub raises, with a note that strict mode needs ffmpeg. Those calls lose the speed and memory gains. Effects, resampling and everything else that works on samples stay as fast as before. The limits for untrusted files below still apply in strict mode. Set the switch once at startup and do not flip it from several threads. See [docs/migrating.md](https://github.com/rs-jensen/pydub2/blob/main/docs/migrating.md) for when to use it.

## Something does not work?

Run this and read what it says. It shows the pydub2 version, which ffmpeg it found and where, and for each thing that is missing the command that fixes it:

    python -m pydub2 doctor

Give it the files that fail and it tells you what each one is, how it loads (built in, through ffmpeg or not at all) and why:

    python -m pydub2 doctor song.m4a upload.mp3

Add `--json` for the same output as JSON. To see what in your own code behaves differently in pydub2, point it at the project. It reads the Python files and never runs them:

    python -m pydub2 doctor --scan .

When you report a problem, paste the output of the first command.

## More

How pydub2 compares with soundfile, PyAV, pedalboard and the others: [docs/comparison.md](https://github.com/rs-jensen/pydub2/blob/main/docs/comparison.md), including timings and memory use measured with the scripts in [bench/](https://github.com/rs-jensen/pydub2/tree/main/bench).

Moving a project from pydub: [docs/migrating.md](https://github.com/rs-jensen/pydub2/blob/main/docs/migrating.md).

What the releases contain: [CHANGELOG.md](https://github.com/rs-jensen/pydub2/blob/main/CHANGELOG.md).

## Code that imports pydub

Packages that write `import pydub` can use pydub2 without changing their code. Install the add-on:

    pip install pydub2-compat

After that `import pydub` loads pydub2, even when the real pydub is installed. It is opt in, see [compat/README.md](https://github.com/rs-jensen/pydub2/blob/main/compat/README.md).

## Untrusted files

If your service decodes uploads, set a limit on length and on how long ffmpeg may run. Both are off by default.

```python
import pydub2
pydub2.max_duration = 3600   # seconds, longer files raise CouldntDecodeError
pydub2.ffmpeg_timeout = 60   # seconds for every ffmpeg and ffprobe call
seg = pydub2.AudioSegment.from_file(upload)
```

`max_duration` is checked while the file is read, so a header that lies about the length does not
get around it. `stream` checks it too.

Never build `format`, `codec`, `parameters`, `cover` or `AudioSegment.converter` from user input, they
go to ffmpeg as they are. Do not show exception text to end users, ffmpeg errors can hold paths on your
server. Numbers such as `silent(duration)`, `seg * n` and `set_frame_rate(n)` use memory in proportion to
the number, so keep them in bounds. For the rest, run decoding in a worker with a memory and time limit.

## Status

This is an early release. The wheels are built and tested in CI on Linux, macOS and Windows.

## Building from source

You need CMake 3.28 or newer, a C++17 compiler and network access, because the build downloads the codec libraries.

    pip install .

Run the tests with pytest. Some of them need ffmpeg and pydub installed and are skipped
without them.

    pip install pytest numpy pydub "audioop-lts; python_version >= '3.13'"
    pytest

### Building from source without network

The default build downloads the codec libraries. To build offline, for example in a distro package, install the development files for flac, vorbis, opus, opusfile, libopusenc and libsamplerate (with their ogg dependencies) and pkg-config, then link them instead:

    CMAKE_ARGS="-DPYDUB2_SYSTEM_LIBS=ON" pip install --no-build-isolation .

Or pass `-C cmake.define.PYDUB2_SYSTEM_LIBS=ON` to pip or `python -m build`. Nothing is downloaded then, and the wheel links the system libraries dynamically. Use the library versions your distro patches. The wheels on PyPI are built with the versions listed in [THIRD_PARTY.md](https://github.com/rs-jensen/pydub2/blob/main/THIRD_PARTY.md), and some behaviour depends on them, for example libopusenc before 0.3 aborts on an empty opus export.

Without the option you can also unpack the sources yourself and set `FETCHCONTENT_FULLY_DISCONNECTED=ON` with `FETCHCONTENT_SOURCE_DIR_OGG`, `_VORBIS`, `_FLAC`, `_OPUS`, `_OPUSFILE`, `_OPUSENC` and `_SAMPLERATE` in `CMAKE_ARGS`. CMake does not run the opusfile patch (`cmake/opusfile_patch.cmake`) on a source directory you give it, so apply it yourself with `cmake -P` from the opusfile directory.

## License

MIT. The Python layer is ported from [pydub](https://github.com/jiaaro/pydub) by James Robert,
which is also MIT. This is a separate project and is not affiliated with pydub. The libraries bundled in the C++ core are listed in
[THIRD_PARTY.md](https://github.com/rs-jensen/pydub2/blob/main/THIRD_PARTY.md).
