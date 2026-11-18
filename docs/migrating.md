# Moving from pydub to pydub2

For most projects the move is one changed import.

Install it with pip:

    pip install pydub2

## Scan your code first

Before you change anything, see what in your project is affected. This reads your Python files, never runs them, and prints each line that behaves differently in pydub2 with one sentence about it:

    python -m pydub2 doctor --scan path/to/project

It ends with how many files use pydub, how many findings need a change, and how many strict mode (below) covers. `python -m pydub2 doctor` on its own checks the install, and `python -m pydub2 doctor file.m4a` checks single files.

## Change the import

Replace `pydub` with `pydub2` in your imports. The module names under it are the same.

```python
from pydub2 import AudioSegment
from pydub2.generators import Sine
from pydub2.silence import split_on_silence

tone = Sine(440).to_audio_segment(duration=1000)
print(len(tone))
```

If you do not want to touch every line, rename it once:

```python
import pydub2 as pydub
```

You can leave pydub installed while you move. The two packages do not share anything.

## Code you cannot edit

Other packages write `import pydub` and you cannot change that. For this there is a second package, `pydub2-compat`. Install it after pydub2, in the same environment:

    pip install pydub2-compat

It is one `.pth` file and a small import hook. After that, `import pydub` gives you pydub2, even when the real pydub is installed. Nothing is copied into a `pydub` folder, and pydub2 is only imported when something imports `pydub`.

```python
import pydub
import pydub2

print(pydub.AudioSegment is pydub2.AudioSegment)
```

This prints `True` with the compat package installed. The same goes for `pydub.utils`, `pydub.silence`, `pydub.effects`, `pydub.generators` and the other modules.

To turn it off for one run, set `PYDUB2_COMPAT=0` in the environment. The snippet above then prints `False` and the real pydub loads. To turn it off for good, run `pip uninstall pydub2-compat`. If pydub2 cannot be imported, the hook prints one line to stderr and the real pydub is used.

Things to know. `pip list` and `importlib.metadata.version("pydub")` still report the real pydub. `pydub.pyaudioop` and `pydub.audioop` do not exist, because pydub2 does not need audioop. The hook needs the `.pth` file to be read, so it does nothing under `python -S`. The full list is in [compat/README.md](../compat/README.md).

## mp3

pydub2 reads mp3 on its own. To write mp3 without ffmpeg, add the encoder package. It holds only the LAME library, and it is its own package because LAME is LGPL and pydub2 is MIT.

    pip install "pydub2[mp3]"

Your export call does not change:

```python
from pydub2 import AudioSegment
from pydub2.generators import Sine

seg = Sine(440).to_audio_segment(duration=1000)
seg.export("tone.mp3", format="mp3", bitrate="128k").close()
print(len(AudioSegment.from_file("tone.mp3")))
```

This works with no ffmpeg on `PATH`. Without the encoder package and without ffmpeg, the same export raises `FileNotFoundError: ffmpeg not found, pip install pydub2-mp3 to write mp3 without it`.

## ffmpeg and AudioSegment.converter

pydub2 reads wav, flac, ogg, opus and mp3 without ffmpeg, and writes wav, flac, ogg and opus without it. Everything else still goes to ffmpeg: m4a and aac, wma, aiff, video files, and mp3 writing if you did not install the encoder package. pydub2 never needs ffprobe to read a file. It asks ffmpeg when it must.

`AudioSegment.converter` is still there. Set it to a full path when ffmpeg is not on `PATH`:

```python
from pydub2 import AudioSegment
from pydub2.generators import Sine

AudioSegment.converter = "/opt/homebrew/bin/ffmpeg"
seg = Sine(440).to_audio_segment(duration=500)
seg.export("tone.m4a", format="ipod").close()
```

Use your own path there. It is used for reading and exporting, and also for the `ffmpeg -i` call behind `utils.mediainfo`. The path can be absolute, relative or contain spaces. ffprobe is looked up on `PATH` first and then in the folder of `converter`, so a private ffmpeg with its ffprobe beside it needs nothing else. With no ffprobe anywhere, `from_file` and `mediainfo` use `ffmpeg -i`.

Without `converter`, ffmpeg, ffprobe and ffplay are looked up on `PATH` only. pydub also finds one in the current directory, which pydub2 does not. As in pydub, setting `AudioSegment.ffmpeg` or `AudioSegment.ffprobe` does nothing, set `converter`. In pydub a private ffmpeg is not enough for reading m4a, because the ffprobe call ignores `converter` and fails with `FileNotFoundError: 'ffprobe'`.

## What behaves differently

[docs/differences.md](differences.md) has the full list. These are the ones you are most likely to notice.

mp3, ogg and opus samples can differ a little from what pydub gets through ffmpeg, because pydub2 uses other decoders. The length is the same. On my test files wav, flac and m4a came out identical, and mp3, ogg and opus differed in the samples. Opus at a low bitrate can differ by a lot, up to about a quarter of full scale.

A path given to `from_file` is decoded straight from disk, so loading takes about the size of the decoded audio in memory. Wav files over 4 GB load. pydub refuses them.

`parameters` passed to `export` are placed before the output file, so they take effect. In pydub they did not.

`export("x.m4a")` and `export("x.aac")` work when ffmpeg is installed. In pydub 0.25.1 both raise `CouldntEncodeError`.

Where pydub raises `audioop.error` or `wave.Error`, for example `set_sample_width(5)` or `set_frame_rate(0)`, pydub2 raises `ValueError`.

A URL passed as a file name is not fetched. pydub hands it to ffmpeg. pydub2 looks for a file with that name.

pydub2 prints no warning at import when ffmpeg is missing.

## Strict mode

If the sample differences above worry you, turn on strict mode and change nothing else:

    PYDUB2_STRICT=1 python your_script.py

or `pydub2.strict = True` in code. The variable is on for 1, true, yes, y, on and t in any case, and off for 0, false, no, n, off, f and empty. Any other value gives a warning and leaves strict mode off. Loading, partial loading, `mediainfo` and exporting then go through ffmpeg and ffprobe like in pydub, with the same command line, so the decoded samples and the exported bytes are the same as pydub's. A wav that pydub reads itself is read the way pydub reads it, also where pydub reads it wrongly (float or a-law wav with an extensible header). When loading, `parameters` go after the output file like in pydub, where ffmpeg ignores them, and `export` without `format` writes mp3 whatever the file name says. `export(format="m4a")` and `"aac"` raise `CouldntEncodeError` as in pydub.

It costs two things. ffmpeg and ffprobe have to be installed, and without them the call raises `FileNotFoundError` like pydub, with one sentence added about strict mode. And those calls are as slow and as heavy on memory as in pydub. ffmpeg puts a random stream serial in ogg vorbis and opus files, so two exports of the same audio differ in a few bytes, in pydub as well.

Turn it off when you have looked at the differences and are happy with them. A good order is to switch the import with strict on, run your tests, then turn strict off and run them again.

Strict mode does not cover the new functions (`stream`, `detect_speech`, `from_numpy`), the error class where pydub raises `audioop.error` (pydub2 raises `ValueError`), and calls that hang in pydub.

The limits for untrusted files stay on in strict mode. URLs, playlists and the demuxer list are refused as before, so a playlist that pydub would load still raises, and `max_duration`, `max_bytes`, `max_file_size` and `ffmpeg_timeout` still apply. Only the `file:` prefix is dropped, so a file whose name has a colon in it, like `http:x.mp3`, fails to load, as in pydub. Set the switch once at startup and do not flip it from several threads.

One known exception: pydub reads a 24 bit 192 kHz flac with 6 or 8 channels from a file object or `BytesIO` with a different number of frames from one run to the next. Strict mode runs the same ffmpeg calls, so it can differ from pydub there by chance.

## Check your own code

Run your tests with both. If you use `pydub2-compat`, that is one variable:

    pytest
    PYDUB2_COMPAT=0 pytest

The first run uses pydub2 and the second uses the real pydub. If you changed your imports instead, run the tests once on the old commit and once on the new one. Do not run a comparison against pydub with the compat package on, because it would compare pydub2 with itself.

To compare the audio itself on your own files, load each file with both libraries and look at the samples. Run this with `PYDUB2_COMPAT=0` if the compat package is installed:

```python
import sys
import pydub
import pydub2

for path in sys.argv[1:]:
    a = pydub.AudioSegment.from_file(path)
    b = pydub2.AudioSegment.from_file(path)
    same = a.raw_data == b.raw_data
    print(path, len(a), len(b), "same samples" if same else "samples differ")
```

Give it a few files of each format you use. A different length is worth a bug report. For mp3, ogg and opus, "samples differ" with the same length is expected.

## Troubleshooting

`ModuleNotFoundError: No module named 'pyaudioop'` on Python 3.13 or newer. That is pydub 0.25.1, which needs the removed `audioop` module. pydub2 does not need audioop, so there is nothing to install. `import pydub2` works on 3.13 and newer as it is.

`RuntimeWarning: Couldn't find ffmpeg or avconv - defaulting to ffmpeg, but may not work` when you import pydub. pydub2 prints nothing at import. If a call really needs ffmpeg and cannot find it, you get `FileNotFoundError: ffmpeg not found, pydub2 needs it for ...` at that call, with no warning. Install ffmpeg, run `pip install "pydub2[ffmpeg]"` to get one with pip, or use a format pydub2 handles itself. The extra is only used when no ffmpeg is on `PATH` and `AudioSegment.converter` is not set. `imageio-ffmpeg` has no wheel for Alpine Linux.

`FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`. In pydub this happens for every file that is not wav. pydub2 does not run ffprobe to read a file, so reading wav, flac, ogg, opus and mp3 works without it, and m4a and the like work with only ffmpeg installed. `utils.mediainfo` and `mediainfo_json` fall back to `ffmpeg -i` for formats pydub2 cannot read itself, and return only the codec, the stream index, the sample format and the sample size keys.

`FileNotFoundError` when exporting mp3 or m4a. For mp3, install `"pydub2[mp3]"` or ffmpeg. For m4a and aac you need ffmpeg. The error message says which one.

`CouldntDecodeError: Decoding failed. ffmpeg returned error code ...`. pydub2 raises the same exception with the same text, and adds the last line ffmpeg said at the end. When ffmpeg is not installed and the file is one of the five formats pydub2 reads itself, you get `CouldntDecodeError: cant read this file`.

Static noise after exporting an array. If you build an `AudioSegment` from float32 bytes with `sample_width=4`, both pydub and pydub2 read them as 32 bit integers and you get noise. Use `from_numpy`, which takes floats between -1 and 1 as floats:

```python
import numpy as np
from pydub2 import AudioSegment

t = np.arange(44100) / 44100
x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
seg = AudioSegment.from_numpy(x, 44100)
print(round(seg.dBFS, 1))
```

`play()` still needs simpleaudio, PyAudio or ffplay. It is pydub's code and I have not changed how it works.
