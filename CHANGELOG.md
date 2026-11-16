# Changelog

What changed in each release.

## [Unreleased]

### Faster and lighter

- Loading ogg vorbis, opus and flac needs about half the memory it did: the peak is now close to the size of the decoded audio. 10 minutes of ogg went from 454 to 234 MiB and of flac from 244 to 126 MiB.
- This also holds with `pydub2.max_duration` set.
- `detect_speech` needs a fixed amount of extra memory, not 3 times the audio. On 40 minutes it went from 1.3 GB to 19 MB. The ranges are the same.
- Writing opus from 44.1 kHz audio on arm64 is about 40 percent faster. 10 minutes took 9.2 s and now take 5.3 s.
- `overlay` is 3 times faster and `normalize` and `apply_gain` are 1.9 times faster on 60 s of audio.

### Fixed

- The Windows wheels no longer need the Visual C++ redistributable. `import pydub2` failed with "DLL load failed" on a machine without it.
- pydub2 no longer starts an `ffmpeg`, `ffprobe` or `ffplay` from the current folder when none is found on PATH. It says ffmpeg is not found.
- On Windows, Ctrl-C reaches ffmpeg again when you run from a console, and ffmpeg is stopped when a call is interrupted or times out.
- `export` no longer fails on Windows when a virus scanner still holds the temporary file ffmpeg wrote.
- `import pydub` with pydub2-compat no longer fails under `pythonw` when pydub2 cannot be imported.
- A converter path in another case, like `C:\ffmpeg\bin\FFMPEG.EXE`, is found on Windows.
- `AudioSegment.stream` lets go of the file when the generator ends or is closed, so it can be deleted on Windows.
- A tag too long for the Windows command line raises `CouldntEncodeError` and not a raw `OSError`.
- `effects.normalize(seg, target_lufs=...)` no longer clips a quiet file with high peaks. The gain stops at full scale minus `headroom`.

## [0.0.1] - 2026-11-16

The first release.

### Reading and writing

- wav, flac, ogg vorbis, opus and mp3 are read without ffmpeg. wav, flac, ogg vorbis and opus are written without ffmpeg.
- mp3 can be written without ffmpeg when the small `pydub2-mp3` package is installed (`pip install "pydub2[mp3]"`). It carries the LAME encoder.
- Tags of flac, ogg and opus files can be written with `tags=` without ffmpeg. `mediainfo` reads the tags of wav, flac, ogg, opus and mp3 files without ffprobe.
- `mediainfo` and `mediainfo_json` work without ffprobe for wav, flac, ogg, opus and mp3.
- `from_file` decodes a path straight from disk. Wav files over 4 GB load.
- Loading part of a file with `start_second` or `duration` seeks in wav, flac, mp3 and opus files instead of running ffmpeg.
- Exporting wav or flac at the current rate and channel count with `codec` pcm_s16le, pcm_s24le or pcm_s32le is done without ffmpeg.
- `export` takes the format from the file extension when `format` is left out. `m4a` and `aac` work as formats, `bitrate` can be an int, and `cover` can be a `pathlib.Path`.
- Everything else, such as m4a and aac, wma, aiff and video files, still goes through ffmpeg when it is installed.

### New functions

- `AudioSegment.stream(path, chunk_ms=10000)` reads a long wav, flac, mp3, ogg or opus file in pieces and holds only the current piece in memory.
- `AudioSegment.from_numpy(array, frame_rate)` and `to_numpy()` move audio to and from numpy. Arrays are int8, int16 or int32, or floats between -1 and 1.
- `seg.loudness()` gives integrated loudness in LUFS (EBU R128), and `effects.normalize(seg, target_lufs=-16)` normalizes to a target.
- `silence.detect_speech` and `silence.split_on_speech` find speech with the Silero VAD model, which is built into the extension. They need no onnxruntime and no torch.
- `set_frame_rate(rate, resample_quality="high")` resamples with libsamplerate.
- `split_on_silence` and `split_on_speech` can return the `(start, end)` range of each chunk with `return_ranges=True`.
- `from_mono_audiosegments(..., fill_up=True)` pads shorter segments with silence.

### Compatibility with pydub

- The Python API is pydub 0.25.1's. `raw_data`, `dBFS`, slicing, `len` and `get_array_of_samples()` give the same numbers. Samples are kept as integer pcm, like in pydub.
- A copy of pydub's own test suite runs against pydub2.
- Python 3.13 and newer work without `audioop`. pydub2 does not use it.
- The optional `pydub2-compat` package makes `import pydub` load pydub2, even when pydub is installed. `PYDUB2_COMPAT=0` turns it off.
- ffmpeg, ffprobe and ffplay are looked up on `PATH`, never in the current directory. ffprobe and ffplay are also found next to `AudioSegment.converter`. `AudioSegment.converter` still works.
- No warning at import when ffmpeg is missing.
- `parameters` given to `export` are placed before the output file, so they take effect.
- mp3, ogg and opus samples can differ slightly from what ffmpeg gives, because other decoders are used. The lengths are the same. All differences are listed in [docs/differences.md](docs/differences.md).
- Fixes for things that fail or hang in pydub: `stereo_to_ms` and `ms_to_stereo`, `overlay(empty, loop=True)`, `append` with a crossfade under 1 ms, `low_pass_filter` and `high_pass_filter` on empty audio, `mediainfo` of a file object, file names that start with `-`, and `format="wma"`.
- Files pydub reads wrongly are read properly: wav with a-law, mu-law or float samples in WAVE_FORMAT_EXTENSIBLE, flac with 24 bit at 192 kHz and 6 or 8 channels, and wav with 12 bit samples.
- `speedup`, `+=`, `sum()`, `compress_dynamic_range`, `low_pass_filter` and `high_pass_filter` are faster on long audio, with the same output.
- Where pydub raises `audioop.error` or `wave.Error`, pydub2 raises `ValueError`.

### Safety for untrusted files

- `pydub2.max_duration` limits the length of what is decoded and `pydub2.ffmpeg_timeout` limits every ffmpeg and ffprobe call. Both are off by default. The length limit is checked while reading, so a header that lies about the length does not get around it.
- File names go to ffmpeg with a `file:` prefix and a protocol whitelist, so a name like `concat:a.wav` is a file name. ffmpeg and ffprobe are also given a list of the demuxers they may use, without hls, dash, concat and the other ones that open further files or network addresses.
- A URL given as a file name is not fetched.
- The C++ core holds no global mutable state, so several threads can decode at the same time.
- opusfile 0.12 is patched at build time for CVE-2022-47021.
- [SECURITY.md](SECURITY.md) says how to report a problem. The README has advice for services that decode uploads.

### Packaging

- One wheel of about 3 MB per platform and Python version, built from C++17 with nanobind. The codec libraries are built in and listed in [THIRD_PARTY.md](THIRD_PARTY.md).
- Wheels are built for Python 3.9 to 3.14 and the free threaded 3.14 build, on 64 bit Linux, macOS and Windows.
- pydub2 is MIT. LAME is LGPL, so it is in the separate `pydub2-mp3` package and loaded at runtime. See [docs/licensing.md](docs/licensing.md).
- numpy is not required to install pydub2. You only need it for `to_numpy` and `from_numpy`.
