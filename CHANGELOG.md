# Changelog

What changed in each release.

## [0.0.2] - 2026-11-17

### Added

- `python -m pydub2 doctor` prints the pydub2 version, the formats that work without ffmpeg, which ffmpeg and ffprobe are used and where they come from, strict mode, whether `import pydub` gives pydub2 or the real pydub, and the command that fixes each missing piece. It exits with 1 when the basics do not work. `--json` prints the same as JSON.
- `python -m pydub2 doctor FILE...` says what each file is from its first bytes, whether it loads built in or through ffmpeg, and why it does not when it fails (no ffmpeg, damaged, empty, text, a playlist, longer than `max_duration`, not a regular file). It notices a truncated wav, flac, ogg and opus file, a truncated mp3 that has a Xing or Info header, and an mp3 with junk in front.
- `python -m pydub2 doctor --scan DIR` reads the Python files under DIR with `ast`, without running them, and lists what behaves differently in pydub2, with file and line. Files it cannot read or parse are listed, and a path that does not exist is an error.
- `CouldntDecodeError`, `CouldntEncodeError` and the `FileNotFoundError` for a missing ffmpeg have the attributes `format`, `reason` and `hint` for tools. The messages are the same as before.
- `playback.play_async` returns a handle with `stop()`, `wait()` and `is_playing()`. A dropped handle lets the sound play to its end, sounds still playing are stopped when the interpreter exits. `playback.play` is unchanged.
- `pydub2.strict = True`, or `PYDUB2_STRICT=1` in the environment, makes loads, partial loads, `mediainfo` and exports use ffmpeg and ffprobe like pydub, so the samples and the exported bytes are the same as pydub 0.25.1 gives. It needs ffmpeg, and raises pydub's error with a note when it is missing. The limits for untrusted files (no URLs or playlists, `max_duration`, `ffmpeg_timeout`) stay on. It is off by default.
- `pip install "pydub2[ffmpeg]"` pulls an ffmpeg binary from the `imageio-ffmpeg` package. pydub2 uses it for m4a, aac, wma and video files when `AudioSegment.converter` is not set and no ffmpeg is on `PATH`.

### Changed

- Loading ogg vorbis, opus and flac needs about half the memory it did: the peak is now close to the size of the decoded audio. 10 minutes of ogg now peak at 237 MiB and of flac at 133 MiB, about half of before. This also holds with `pydub2.max_duration` set.
- `detect_speech` needs a fixed amount of extra memory, not 3 times the audio. On 40 minutes it went from 1.3 GB to 19 MB. The ranges are the same.
- Writing opus from 44.1 kHz audio on arm64 is about 40 percent faster. 10 minutes now take about 6.2 s.
- `overlay` is 3 times faster and `normalize` and `apply_gain` are 1.9 times faster on 60 s of audio.
- The `FileNotFoundError` for a missing ffmpeg says what it looked for, and suggests the `ffmpeg` extra only when `AudioSegment.converter` is not set.
- A `pydub2.max_duration` that is negative, `nan` or not a number raises `ValueError` when a load starts, where it raised `CouldntDecodeError` or loaded raw input without a limit. One too large to matter, like `1e14`, is no limit.
- `normalize(seg, target_lufs=...)` stops the gain at full scale minus `headroom`, so a quiet file with high peaks ends below the target and does not clip.

### Fixed

- `rms`, `dBFS`, `detect_silence`, `compress_dynamic_range` and `normalize` of 32 bit audio now match the audioop you have. The first call asks that audioop, so macOS arm64 and the Pythons that fuse the sum give pydub's numbers where they were off by 1 (`rms`) or 1e-8 dB (`dBFS`).
- A wav export of 8 or 24 bit audio with an odd number of bytes is what the `wave` module of the running Python writes, so there is no pad byte before Python 3.15.
- A damaged flac file whose header says the maximum block size is 0 no longer makes the seek compute an infinite byte offset, which is undefined in C.
- A damaged ogg vorbis file whose last granule position is close to the 64 bit limits no longer overflows a signed integer when its length is worked out, which is undefined in C++.
- The Windows wheels no longer need the Visual C++ redistributable. `import pydub2` failed with "DLL load failed" on a machine without it.
- On Windows, Ctrl-C reaches ffmpeg again when you run from a console, and ffmpeg is stopped when a call is interrupted or times out.
- `export` no longer fails on Windows when a virus scanner still holds the temporary file ffmpeg wrote.
- `import pydub` with pydub2-compat no longer fails under `pythonw` when pydub2 cannot be imported.
- A converter path in another case, like `C:\ffmpeg\bin\FFMPEG.EXE`, is found on Windows.
- `AudioSegment.stream` lets go of the file when the generator ends or is closed, so it can be deleted on Windows.
- A tag too long for the Windows command line raises `CouldntEncodeError` and not a raw `OSError`.
- `AudioSegment.stream` now closes the file when the generator is closed before the first chunk, which left it open on Python 3.12.
- On x86 the mp3 encoder of pydub2-mp3 is built with the options of LAME's own configure, so 24 and 32 bit audio and some VBR settings give the same bytes as ffmpeg's libmp3lame. They were different whenever the low 16 bits of the samples were not zero.
- A segment built with `+`, `sum()` or `append(crossfade=0)` drops its list of parts once `raw_data` or `_data` is read, so it no longer holds its audio twice, and several threads or a signal handler can read it while it is joined. `_data` appears in `vars(seg)` after the first read, as in pydub. Subclasses that define `_data`, `__init__`, `_spawn`, `__new__`, `__setattr__` or `__getattr__` get the joined data as in pydub.
- An ogg vorbis file cut before its first audio page ends raises `CouldntDecodeError` like pydub, where a few frames were returned.
- A failed `export` leaves an existing file as it was. The file is opened once the audio is encoded, where pydub empties it first.
- `utils.mediainfo` and `mediainfo_json` without ffmpeg return `{}` for a damaged wav, flac, mp3, ogg or opus file like pydub, where they said ffmpeg was not found.
- `AudioSegment.stream` raises `FileNotFoundError` for a missing file and says "cant read this file" for a damaged one, where it said it only reads wav, flac, mp3, ogg and opus.
- A `pydub2.ffmpeg_timeout` that is not a number above 0 raises `ValueError` naming it, and a load that ffprobe gives broken JSON for says so, where both said "cant decode this file" with Python's text.
- On Linux and macOS `ffmpeg_timeout` and Ctrl-C stop the whole process group, so an ffmpeg behind a wrapper script (snap, conda, docker) is no longer left running.
- `CouldntEncodeError` and `CouldntDecodeError` from ffmpeg have pydub's text again, with the return code, the command for an export and all of ffmpeg's output. A decode error ends with the line `ffmpeg said: ...` as before.
- `playback.play_async` called from two threads at once no longer raises `RuntimeError: Set changed size during iteration`.
- A bitrate that ffmpeg cannot read, like `"k"` or `"1e400k"`, raises `CouldntEncodeError` on export, as with pydub, and not `ValueError` or `OverflowError`.
- `AudioSegment.stream` takes a `start_second` or `duration` under 0.0001 (written `1e-05` by Python) and cuts it like a longer one, where it raised `ValueError`.
- An ffmpeg that exits with 0 and writes no file gives an empty export, as with pydub, and not `FileNotFoundError`.
- `AudioSegment.from_file` reads a named pipe (fifo) once and decodes the bytes, where it waited for ever for a second writer.
- `PYDUB2_STRICT=y` and `t` turn strict mode on, and a value that is neither a yes nor a no gives a warning at import, where strict mode stayed off without a word.
- `export` with a `cover` that is not mp3 and a picture, or not a string, raises the `AttributeError` of pydub with its text.
- A missing attribute on an `AudioSegment` says `'AudioSegment' object has no attribute 'x'` like pydub, where it said only `x`. An `AttributeError` inside a property of a subclass keeps its own message.
- `overlay` with a `times` that is not a number works when the overlaid audio is as long as the rest, and raises `TypeError` when it has to loop, as pydub does. It raised `TypeError` every time.
- `detect_silence` with a `silence_thresh` that is not a number raises `ValueError` for a string like `"x"`, as pydub does, and not `TypeError`.

### Security

- pydub2 no longer starts an `ffmpeg`, `ffprobe` or `ffplay` from the current folder when none is found on PATH. It says ffmpeg is not found. The ffmpeg extra does not look in the current folder either.
- A float wav whose block align is smaller than its channels and sample size say no longer makes the decoder count a frame per block align and set aside up to 1024 times the decoded size, 1 GB for a 1 MB file with 256 channels.
- Writing opus from audio at a rate over 768 kHz raises `CouldntEncodeError`. The rate comes from the file header, an ogg vorbis file can say 1 GHz, and libopusenc took minutes per channel to resample from that.
- `pydub2.max_bytes` limits the size of the decoded audio, off by default. `max_duration` alone does not bound memory, a 301 kB flac that says 8 channels at 192 kHz asked for 22 GB under a limit of an hour. Both limits now hold for raw and pcm input, in strict mode and in `stream`, are checked from the header before anything is read where it gives the length, and take `inf`. They are read once at the start of a load. A file object is still read into memory whole before it is decoded, except for raw and pcm input.

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

- `AudioSegment.stream(file, chunk_ms=10000, start_second=None, duration=None)` reads a long wav, flac, mp3, ogg or opus file in pieces and holds only the current piece in memory.
- `AudioSegment.from_numpy(array, frame_rate)` and `to_numpy()` move audio to and from numpy. Arrays are int8, int16 or int32, or floats between -1 and 1.
- `seg.loudness()` gives integrated loudness in LUFS (EBU R128), and `effects.normalize(seg, target_lufs=-16)` normalizes to a target.
- `silence.detect_speech` and `silence.split_on_speech` find speech with the Silero VAD model, which is built into the extension. They need no onnxruntime and no torch.
- `set_frame_rate(rate, resample_quality="high")` resamples with libsamplerate.
- `split_on_silence` and `split_on_speech` can return the `(start, end)` range of each chunk with `return_ranges=True`.
- `from_mono_audiosegments(..., fill_up=True)` pads shorter segments with silence.

### Compatibility with pydub

- The Python API is pydub 0.25.1's. `raw_data`, `dBFS`, slicing, `len` and `get_array_of_samples()` give the same numbers. Samples are kept as integer pcm, like in pydub.
- A copy of pydub's own test suite runs against pydub2.
- Python 3.13 and newer work without `audioop`. pydub2 does not need it.
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
- Wheels are built for Python 3.9 to 3.14 (Windows arm64 from 3.11) and the free threaded 3.14 build, on 64 bit Linux, macOS and Windows.
- pydub2 is MIT. LAME is LGPL, so it is in the separate `pydub2-mp3` package and loaded at runtime. See [docs/licensing.md](docs/licensing.md).
- numpy is not required to install pydub2. You only need it for `to_numpy` and `from_numpy`.
