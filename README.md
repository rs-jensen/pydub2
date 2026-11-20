# pydub2

[![PyPI](https://img.shields.io/pypi/v/pydub2)](https://pypi.org/project/pydub2/)
[![tests](https://github.com/rs-jensen/pydub2/actions/workflows/test.yml/badge.svg)](https://github.com/rs-jensen/pydub2/actions/workflows/test.yml)

pydub2 is an audio library for Python with the API of pydub 0.25.1 and a C++ core. It reads and writes wav, flac, ogg, opus and mp3 without ffmpeg, and it works on Python 3.13 and 3.14, where the `audioop` module that pydub needs is gone.

It is an independent project. The API and most of the Python code come from [pydub](https://github.com/jiaaro/pydub) by James Robert, see the end of this page.

## Try it

    pip install pydub2

```python
from pydub2 import AudioSegment

song = AudioSegment.from_file("song.wav")
intro = song[:2000].fade_out(500) + 6  # first 2 seconds, fade out, 6 dB louder
intro.export("intro.flac", format="flac")
```

Code that says `import pydub`, yours or inside another package, can keep saying it. Install the compat package and `import pydub` loads pydub2, even when the real pydub is installed too:

    pip install pydub2-compat

```python
from pydub import AudioSegment  # this is pydub2 now

AudioSegment.from_file("song.wav").export("song.ogg", format="ogg")
```

`PYDUB2_COMPAT=0` in the environment gives you the real pydub back for a run. If you can edit the code, `import pydub2 as pydub` works without the compat package.

pydub2 reads mp3 on its own. To write mp3 without ffmpeg, add the LAME encoder. It is LGPL, so it lives in its own small package:

    pip install "pydub2[mp3]"

```python
intro.export("intro.mp3", format="mp3", bitrate="192k")
```

## Why you might want it

- It works on Python 3.13 and 3.14 as it is. pydub 0.25.1 fails to import there unless you also install `audioop-lts`.
- wav, flac, ogg, opus and mp3 need no ffmpeg and no ffprobe. m4a, aac, wma, aiff and video files still go to ffmpeg, and `pip install "pydub2[ffmpeg]"` fetches one from PyPI if you have none.
- A file is decoded straight from disk into the result, so loading needs about the size of the decoded audio and little more.
- The sample loops are in C++, so the slow parts of pydub are a lot faster.

Ten minutes of 16 bit stereo:

| | pydub 0.25.1 | pydub2 | measured on |
|---|---|---|---|
| `apply_gain` | 0.176 s | 0.065 s | Linux x86-64 |
| `fade` over the whole file | 1.632 s | 0.174 s | Linux x86-64 |
| `detect_silence` | 70 s | 0.14 s | Apple M2 Pro, macOS 15.7 |
| peak memory, `from_file` of an mp3 | 305 MiB | 102 MiB | Intel Xeon 2.1 GHz, Linux 6.18 |

pydub's memory is its Python process. The ffmpeg it starts uses another 50 MiB on top. Not every call is faster: opus export, and reading 10 seconds out of the middle of an ogg file, take longer than in pydub. Every row and how it was measured is in [docs/comparison.md](https://github.com/rs-jensen/pydub2/blob/main/docs/comparison.md).

It also does a few things pydub does not, like reading a long file in pieces, loudness in LUFS, time stretch and pitch shift, and finding speech with a built in Silero VAD model:

```python
from pydub2 import silence

for chunk in AudioSegment.stream("long.flac", chunk_ms=10000):  # one piece in memory at a time
    print(chunk.loudness(), silence.detect_speech(chunk))

seg.time_stretch(1.25)  # faster, same pitch
seg.pitch_shift(-3)     # three semitones down, same length
```

## What is different, and what is missing

The samples are the same integer pcm as in pydub, so `raw_data`, `len`, `dBFS` and `get_array_of_samples()` give pydub's numbers for wav and flac. A copy of pydub's own test suite runs against pydub2 and passes.

mp3, ogg and opus are decoded by other decoders than ffmpeg's. The length is the same, but samples can differ a little, and opus at low bitrates can differ a lot. If you need pydub's exact samples, turn on strict mode. Loads and exports then go through ffmpeg with the command lines pydub uses, so you need ffmpeg and ffprobe installed:

```python
import pydub2

pydub2.strict = True  # or PYDUB2_STRICT=1 in the environment
```

Where pydub raises `audioop.error` or `wave.Error`, pydub2 raises `ValueError`. A few calls that hang or crash in pydub work here. All of it is in [docs/differences.md](https://github.com/rs-jensen/pydub2/blob/main/docs/differences.md). To see which lines of your own project are affected, run this. It reads your files and never runs them:

    python -m pydub2 doctor --scan .

Not there yet:

- m4a, aac, wma, aiff and video files need ffmpeg.
- No type hints yet. There is no `py.typed`.

pydub2 is young code. The first release was in November 2026 and one person maintains it, me. So far I have checked it like this:

- More than 480,000 random chains of operations, run through pydub and pydub2 and compared.
- The C++ decoders, encoders and resampler fuzzed under AddressSanitizer and UndefinedBehaviorSanitizer. Inputs that once failed are kept as tests.
- pydub's test suite, and the calls gradio, markitdown, audio-separator and RealtimeTTS make, run as tests.
- CI on Linux, macOS and Windows for every push, and every wheel is tested on its own platform before it is published.

If you find a difference from pydub that is not on the differences page, please open an issue.

## Platforms

There are wheels for CPython 3.9 to 3.14 and for the free threaded 3.14t, on:

- Linux x86-64 and arm64, glibc and musl (Alpine)
- macOS, Intel and Apple silicon
- Windows x86-64, and Windows arm64 from Python 3.11

`pydub2-mp3` has a wheel for the same platforms that works with every Python version, and `pydub2-compat` is pure Python. There are no 32 bit wheels. Anywhere else pip builds from source, which needs CMake 3.28 or newer, a C++17 compiler and network access to download the codec libraries.

## More

- [Moving a project from pydub](https://github.com/rs-jensen/pydub2/blob/main/docs/migrating.md)
- [What pydub2 adds: streaming, numpy, loudness, speech detection and more](https://github.com/rs-jensen/pydub2/blob/main/docs/features.md)
- [pydub2 next to pydub, soundfile, PyAV and others, with timings](https://github.com/rs-jensen/pydub2/blob/main/docs/comparison.md)
- [Every difference from pydub 0.25.1](https://github.com/rs-jensen/pydub2/blob/main/docs/differences.md)
- [Decoding files from other people](https://github.com/rs-jensen/pydub2/blob/main/docs/untrusted-files.md)
- [Changelog](https://github.com/rs-jensen/pydub2/blob/main/CHANGELOG.md)
- [Bundled libraries](https://github.com/rs-jensen/pydub2/blob/main/THIRD_PARTY.md) and [licensing](https://github.com/rs-jensen/pydub2/blob/main/docs/licensing.md)

To report a bug, run `python -m pydub2 doctor` and paste its output into a [new issue](https://github.com/rs-jensen/pydub2/issues), with the file that fails if you can share it. `python -m pydub2 doctor song.m4a` tells you how a single file loads and why it does not.

For a security problem, please do not open a public issue. [SECURITY.md](https://github.com/rs-jensen/pydub2/blob/main/SECURITY.md) says how to reach me privately.

## Thanks to pydub

pydub2 exists because pydub is so pleasant to use. Its API, most of its Python code and its test suite come from [pydub](https://github.com/jiaaro/pydub) by James Robert, under the MIT license. Thank you, James. pydub2 is not affiliated with pydub. If pydub works for you today, there is no need to switch.

pydub2 is MIT too. The libraries built into it are MIT, BSD or public domain. LAME in `pydub2-mp3` is LGPL.
