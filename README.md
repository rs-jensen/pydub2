# pydub2

pydub2 is a drop-in replacement for pydub. It has a C++ core that reads and writes the common
audio formats without ffmpeg, and it works on Python 3.13 and newer where the audioop module is gone.

## Install

It is not on PyPI yet. For now install it from the repo:

    pip install git+https://github.com/rs-jensen/pydub2

Once it is released it will be `pip install pydub2`.

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

The ffmpeg rows only work if ffmpeg is installed. Everything marked built in works without it,
including `tags=` for flac, ogg and opus and reading them back with `mediainfo` (mp3 tags too, without ffprobe).
mp3 writing needs no ffmpeg once you install the small `pydub2-mp3` package, which carries the
LAME encoder:

    pip install pydub2[mp3]

```python
seg.export("out.mp3", format="mp3")
```

If ffmpeg is there, pydub2 uses ffmpeg for the things the built in code does not do, such as a
chained vorbis file or an mp3 export with unusual `parameters`.

## What is new

Read a long file piece by piece. `AudioSegment.stream` yields `chunk_ms` long segments and holds
only the current one in memory. It reads wav, flac, mp3, ogg and opus.

```python
for chunk in AudioSegment.stream("long.flac", chunk_ms=10000):
    print(chunk.dBFS)
```

Go to and from numpy. `to_numpy()` gives an array of shape `(frames, channels)` in the segment's
integer type, and `from_numpy` takes an int8, int16 or int32 array, or floats between -1 and 1:

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

There are a few deliberate differences, for example `from_file` decodes a file straight from disk and
mp3 and opus samples can differ slightly from what ffmpeg gives. They are listed in [docs/differences.md](https://github.com/rs-jensen/pydub2/blob/main/docs/differences.md).

## More

How pydub2 compares with soundfile, PyAV, pedalboard and the others: [docs/comparison.md](https://github.com/rs-jensen/pydub2/blob/main/docs/comparison.md).

Moving a project from pydub: [docs/migrating.md](https://github.com/rs-jensen/pydub2/blob/main/docs/migrating.md).

What the releases contain: [CHANGELOG.md](https://github.com/rs-jensen/pydub2/blob/main/CHANGELOG.md).

## Use it as pydub

Packages that write `import pydub` can use pydub2 without changing their code. Install the
`pydub2-compat` package from the `compat` folder of this repo. Then `import pydub` loads pydub2, even
when the real pydub is installed. It is opt in, see [compat/README.md](https://github.com/rs-jensen/pydub2/blob/main/compat/README.md).

## Untrusted files

If your service decodes uploads, set a limit on length and on how long ffmpeg may run. Both are off by default.

    import pydub2
    pydub2.max_duration = 3600   # seconds, longer files raise CouldntDecodeError
    pydub2.ffmpeg_timeout = 60   # seconds for every ffmpeg and ffprobe call
    seg = pydub2.AudioSegment.from_file(upload)

`max_duration` is checked while the file is read, so a header that lies about the length does not
get around it. `stream` checks it too.

Never build `format`, `codec`, `parameters`, `cover` or `AudioSegment.converter` from user input, they
go to ffmpeg as they are. Do not show exception text to end users, ffmpeg errors can hold paths on your
server. Numbers such as `silent(duration)`, `seg * n` and `set_frame_rate(n)` use memory in proportion to
the number, so keep them in bounds. For the rest, run decoding in a worker with a memory and time limit.
The source package downloads its codec libraries when it is built, so building needs network access.

## Status

This is early. The version is 0.1.0. It is built and tested on macOS arm64 and Linux so far.
Windows wheels are untested.

## Building from source

You need CMake 3.28 or newer and a C++17 compiler.

    pip install .

Run the tests with pytest. Some of them need ffmpeg and pydub installed and are skipped
without them.

    pip install pytest numpy pydub audioop-lts
    pytest

## License

MIT. The Python layer is ported from [pydub](https://github.com/jiaaro/pydub) by James Robert,
which is also MIT. This is a separate project and is not affiliated with pydub. The libraries bundled in the C++ core are listed in
[THIRD_PARTY.md](https://github.com/rs-jensen/pydub2/blob/main/THIRD_PARTY.md).
