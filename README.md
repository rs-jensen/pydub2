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
| mp3 | built in | ffmpeg, for now |
| m4a/aac, wma, video files | ffmpeg | ffmpeg |

The ffmpeg rows only work if ffmpeg is installed. Everything marked built in works without it.

## What is new

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
suite lives in `tests/pydub_suite` and runs against pydub2.

There are a few deliberate differences, for example files that fall back to ffmpeg come out as
16 bit. They are listed in [docs/differences.md](https://github.com/rs-jensen/pydub2/blob/main/docs/differences.md).

## Untrusted files

If your service decodes uploads, set a limit on length and on how long ffmpeg may run. Both are off by default.

    import pydub2
    pydub2.max_duration = 3600   # seconds, longer files raise CouldntDecodeError
    pydub2.ffmpeg_timeout = 60   # seconds for every ffmpeg and ffprobe call
    seg = pydub2.AudioSegment.from_file(upload)

Never build `format`, `codec`, `parameters`, `cover` or `AudioSegment.converter` from user input, they
go to ffmpeg as they are. Do not show exception text to end users, ffmpeg errors can hold paths on your
server. Numbers such as `silent(duration)`, `seg * n` and `set_frame_rate(n)` use memory in proportion to
the number, so keep them in bounds. For the rest, run decoding in a worker with a memory and time limit.
The source package downloads its codec libraries when it is built, so building needs network access.

## Status

This is early. The version is 0.1.0. It is built and tested on macOS arm64 and Linux so far.
Windows wheels are untested.

Planned next: streaming for long files, and an mp3 encoder in the wheel so mp3 export does not
need ffmpeg.

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
