# What pydub2 adds

The API is pydub's. This page is about the rest: which formats need ffmpeg, the functions pydub does not have, strict mode and the `doctor` command.

## Formats

| Format | Read | Write |
|---|---|---|
| wav | built in for pcm and float, ffmpeg for a-law, mu-law, adpcm and other codecs | built in |
| flac | built in | built in |
| ogg vorbis | built in | built in |
| opus | built in | built in |
| mp3 | built in | `pydub2-mp3` package, or ffmpeg |
| m4a/aac, wma, aiff, video files | ffmpeg | ffmpeg |

The ffmpeg rows need ffmpeg 3.3 or newer installed. Everything marked built in works without it. That includes
writing tags to flac, ogg and opus files and reading tags with `mediainfo`.
It also includes naming the codec ffmpeg would use anyway: `codec="libvorbis"` for ogg, `"libopus"` for opus,
`"libmp3lame"` for mp3 and `"flac"` for flac. Another codec or a `cover` image needs ffmpeg.

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
`IMAGEIO_FFMPEG_EXE` with an absolute path is honoured, as in `imageio-ffmpeg`.
`imageio-ffmpeg` has no wheel for Alpine Linux (musl). The package has no ffprobe, `mediainfo` then gives the codec, sample format and sample size only.

If ffmpeg is there, pydub2 uses ffmpeg for the things the built in code does not do, such as a
chained vorbis file or an mp3 export with unusual `parameters`.

## New compared with pydub

Read a long file piece by piece. `AudioSegment.stream` yields `chunk_ms` long segments and holds
only the current one in memory. It reads wav, flac, mp3, ogg and opus. A file object or a named pipe is
read whole first.

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

Play in the background and stop it. `playback.play` still blocks, as in pydub. `play_async`
returns a handle with `stop()`, `wait(timeout=None)` (True when the sound has ended) and
`is_playing()`, and leaving a `with` block stops the sound. A dropped handle lets the sound play
to its end, and the interpreter stops what still plays when it exits. It uses simpleaudio, pyaudio or ffplay,
in the order `play` does. The ffplay temp file is removed once the process has exited, also on Windows.

```python
from pydub2 import playback
with playback.play_async(seg) as handle:
    handle.wait(2)  # stopped after two seconds if it is still playing
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

## Exactly like pydub

Set `pydub2.strict = True`, or start Python with `PYDUB2_STRICT=1`, and every load and export that pydub sends to ffmpeg goes to ffmpeg here too, with the command line pydub builds. `raw_data`, `frame_rate`, `channels` and `sample_width` are then the same bytes as pydub 0.25.1 gives, and exported files are the same bytes as pydub writes on the same machine, apart from the random stream serial ffmpeg puts in every ogg and opus file. A wav that pydub reads itself is read the way pydub reads it.

It needs ffmpeg and ffprobe. Without them the call raises `FileNotFoundError` like pydub, with a note that strict mode needs ffmpeg. Those calls lose the speed and memory gains. Effects, resampling and everything else that works on samples stay as fast as before. The limits for [untrusted files](untrusted-files.md) still apply in strict mode. Set the switch once at startup and do not flip it from several threads. See [migrating.md](migrating.md#strict-mode) for when to use it.

## Something does not work

Run this and read what it says. It shows the pydub2 version, which ffmpeg it found and where, and for each thing that is missing the command that fixes it:

    python -m pydub2 doctor

Give it the files that fail and it tells you what each one is, how it loads (built in, through ffmpeg or not at all) and why:

    python -m pydub2 doctor song.m4a upload.mp3

Add `--json` for the same output as JSON. To see what in your own code behaves differently in pydub2, point it at the project. It reads the Python files and never runs them:

    python -m pydub2 doctor --scan .

When you report a problem, paste the output of the first command.

## Building from source

You need CMake 3.28 or newer, a C++17 compiler and network access, because the build downloads the codec libraries.

    pip install .

Run the tests with pytest. Some of them need ffmpeg and pydub installed and are skipped
without them.

    pip install pytest numpy pydub "audioop-lts; python_version >= '3.13'"
    pytest
