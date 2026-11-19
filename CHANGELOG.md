# Changelog

What changed in each release.

## [0.1.0] - 2026-11-19

### Added

- `python -m pydub2 doctor` prints the pydub2 version, the formats that work without ffmpeg, which ffmpeg and ffprobe are used and where they come from, strict mode, whether `import pydub` gives pydub2 or the real pydub, and the command that fixes each missing piece. It exits with 1 when the basics do not work. `--json` prints the same as JSON.
- `python -m pydub2 doctor FILE...` says what each file is from its first bytes, whether it loads built in or through ffmpeg, and why it does not when it fails (no ffmpeg, damaged, empty, text, a playlist, longer than `max_duration`, not a regular file). It notices a truncated wav, flac, ogg and opus file, a truncated mp3 that has a Xing or Info header, and an mp3 with junk in front.
- `python -m pydub2 doctor --scan DIR` reads the Python files under DIR with `ast`, without running them, and lists what behaves differently in pydub2, with file and line. Files it cannot read or parse are listed, and a path that does not exist is an error. A file with a very long chain like `a.b.c...` is read on every platform, also where `ast.parse` gives up on it.
- `CouldntDecodeError`, `CouldntEncodeError` and the `FileNotFoundError` for a missing ffmpeg have the attributes `format`, `reason` and `hint` for tools.
- `playback.play_async` returns a handle with `stop()`, `wait()` and `is_playing()`. A dropped handle lets the sound play to its end, sounds still playing are stopped when the interpreter exits. `playback.play` is unchanged.
- `pydub2.strict = True`, or `PYDUB2_STRICT=1` in the environment (also `true`, `yes`, `y`, `on` and `t`), makes loads, partial loads, `mediainfo` and exports use ffmpeg and ffprobe like pydub, so the samples and the exported bytes are the same as pydub 0.25.1 gives. It needs ffmpeg, and raises pydub's error with a note when it is missing. The limits for untrusted files (no URLs or playlists, `max_duration`, `max_bytes`, `max_file_size`, `ffmpeg_timeout`) stay on. A value of `PYDUB2_STRICT` that is neither a yes nor a no gives a warning at import and strict mode stays off. It is off by default.
- `pydub2.max_file_size` refuses a file larger than it with `CouldntDecodeError` before the file is read into memory. A path is checked from its size, a file object from its size left to read (a wav and `mediainfo` count from the start), a pipe, a socket or a file that grows stops one byte past the limit. It applies to `from_file`, `stream`, `AudioSegment(data=file)`, `mediainfo` and `mediainfo_json`. Without it a server that loads uploads from a file object held the whole upload in memory, whatever `max_bytes` said. It is off by default.
- `pip install "pydub2[ffmpeg]"` pulls an ffmpeg binary from the `imageio-ffmpeg` package. pydub2 uses it for m4a, aac, wma and video files when `AudioSegment.converter` is not set and no ffmpeg is on `PATH`. The `FileNotFoundError` for a missing ffmpeg now says what was looked for, and suggests this extra only when `AudioSegment.converter` is not set.

### Changed

Speed and memory numbers in this section are from Linux x86-64 (Intel Xeon, 4 cores, Python 3.13) with 10 minutes of 44.1 kHz 16 bit stereo, pydub2 0.0.1 from PyPI against this release. Times are the median of 5 runs. Memory is the peak above the size of the Python process before the audio was loaded, so the audio itself is part of it. Apple silicon and Windows will give other times.

- Loading flac, ogg vorbis and opus needs less memory: the peak is now close to the size of the decoded audio. The peak for flac went from 176 to 101 MiB, for ogg from 272 to 202 MiB and for opus from 290 to 222 MiB. This also holds with `pydub2.max_duration` set.
- Loading through ffmpeg needs a third less memory: the peak for a 202 MiB aiff file went from 610 to 412 MiB.
- `detect_speech` needs a fixed amount of extra memory, where it needed about 3 times the audio. On 40 minutes of stereo it went from 1.2 GB to 17 MB. The ranges are the same.
- Writing ogg, opus and mp3 needs a third of the memory it did. The peak was 3.1, 3.1 and 3.4 times the size of the audio and is now 1.2 times. The files are the same.
- Writing flac to a file needs 1.7 times the size of the audio where it needed 2.5. The encoded file is kept in blocks that go back to the system as they are written out. A path or a temporary file gets the blocks one at a time, a file object you pass still gets one write.
- `set_frame_rate` without `resample_quality` needs a third less memory (3.3 to 2.2 times the size of the audio) and is 4 times faster, 0.83 s down to 0.21 s.
- `overlay` is 3 times faster (0.19 s to 0.06 s). With `loop=True` it is 5 times faster (1.2 s to 0.23 s) and needs less than half the memory, 5.1 down to 2.1 times the size of the audio with `gain_during_overlay`. Overlaying at a `position` no longer copies the audio before and after it.
- `fade`, `fade_in` and `fade_out` do not copy the parts of the audio they leave alone. A fade of 2 seconds peaked at 3.0 times the size of the audio and now peaks at 2.0, and is twice as fast.
- `fade` with a duration over 100 ms makes its gain steps in one pass in C++, where python made a slice, a gain and a bytes object for each millisecond. A fade over 10 minutes went from 2.2 s to 0.2 s, 11 times faster, and from 3.9 to 2.1 times the size of the audio in peak memory. The bytes are the same. pydub needs 1.8 s.
- `append` with a crossfade is 2.7 times faster (0.33 s to 0.12 s). The parts are joined in memory, where they were written to a temporary file and read back. The audio is the same.
- `split_to_mono` and `from_mono_audiosegments` copy each sample once. `split_to_mono` is 4 times faster and went from 3.6 to 2.1 times the size of the audio in peak memory. `from_mono_audiosegments` needed 2.5 times the size of the result on top of its input and now needs 1.
- `set_sample_width(3)` is 10 times faster and building a segment from 24 bit data is 6 times faster, and both need less memory. `set_sample_width(3)` peaked at 6.6 times the size of the audio and now peaks at 3.1.
- `speedup` needs half the memory: its peak went from 3.6 to 1.9 times the size of the audio. The chunks it keeps are views of the audio and are copied once, into the result.
- `remove_dc_offset` of stereo audio is 18 times faster and needs half the memory (4.1 to 2.1 times the size of the audio).
- `apply_gain`, `invert_phase`, `normalize`, `reverse` and `max` are faster, and now take less time than pydub. `apply_gain` went from 0.40 s to 0.07 s, `normalize` from 0.51 s to 0.09 s, `reverse` from 0.24 s to 0.06 s and `max` from 0.077 s to 0.028 s. `compress_dynamic_range` is a quarter faster (1.9 s to 1.5 s). The sample loops were built for size, now they are built for speed.
- `rms` and `dBFS` are 6 times faster, `rms` went from 0.14 s to 0.021 s. The squares are added as integers while the sum is exact in a double, so the result is the same, and past that the sum is walked in order like audioop.
- `set_channels`, `set_sample_width`, `pan` and `apply_gain_stereo` are 3 to 4 times faster and now take less time than pydub. `set_channels(1)` went from 0.29 s to 0.10 s, `set_sample_width(4)` from 0.32 s to 0.11 s and `pan` from 2.3 s to 0.58 s.
- Writing opus from 44.1 kHz audio on arm64 is about 40 percent faster. 10 minutes now take about 6.2 s on an Apple M2 Pro. On x86-64 Linux the time is the same as before.
- A `pydub2.max_duration` that is negative, `nan` or not a number raises `ValueError` when a load starts, where it raised `CouldntDecodeError` or loaded raw input without a limit. One too large to matter, like `1e14`, is no limit.
- `normalize(seg, target_lufs=...)` stops the gain at full scale minus `headroom`, so a quiet file with high peaks ends below the target and does not clip.

### Fixed

- An ogg export reads back at the length pydub gives. The ogg pages are cut where ffmpeg cuts them, and libvorbis gets the audio 64 frames at a time as from ffmpeg. Both decide whether the end padding is kept. 1 second of 8 kHz mono came back as 8192 frames, where pydub gives 8000.
- `rms`, `dBFS`, `detect_silence`, `compress_dynamic_range` and `normalize` of 32 bit audio now match the audioop you have. The first call asks that audioop, so macOS arm64 and the Pythons that fuse the sum give pydub's numbers where they were off by 1 (`rms`) or 1e-8 dB (`dBFS`).
- A segment with a partial last frame, which `_spawn` can make, works like in pydub. `low_pass_filter`, `high_pass_filter`, `compress_dynamic_range` and `export` to wav and flac, also with a `codec` or `parameters`, leave the partial frame out where they raised `ValueError`. `detect_silence`, `detect_nonsilent` and `split_on_silence` give pydub's ranges and raise only when a window reaches that frame.
- 24 bit data with stray bytes after the last whole sample loads like pydub does.
- A wav export of 8 or 24 bit audio with an odd number of bytes is what the `wave` module of the running Python writes, so there is no pad byte before Python 3.15.
- A damaged flac file whose header says the maximum block size is 0 no longer makes the seek compute an infinite byte offset, which is undefined in C.
- A damaged ogg vorbis file whose last granule position is close to the 64 bit limits no longer overflows a signed integer when its length is worked out, which is undefined in C++.
- Starting an mp3 export no longer rewrites the quantizer tables that the mp3 exports running in other threads are reading. pydub2-mp3 patches LAME 3.100 at build time to fill them once.
- The Windows wheels no longer need the Visual C++ redistributable. `import pydub2` failed with "DLL load failed" on a machine without it.
- On Windows, Ctrl-C reaches ffmpeg again when you run from a console, and ffmpeg is stopped when a call is interrupted or times out.
- `export` no longer fails on Windows when a virus scanner still holds the temporary file ffmpeg wrote.
- `import pydub` with pydub2-compat no longer fails under `pythonw` when pydub2 cannot be imported.
- A converter path in another case, like `C:\ffmpeg\bin\FFMPEG.EXE`, is found on Windows.
- `AudioSegment.stream` lets go of the file when the generator ends or is closed, also before the first chunk, so it can be deleted on Windows. A close before the first chunk left the file open on Python 3.12.
- A tag too long for the Windows command line raises `CouldntEncodeError` and not a raw `OSError`.
- On x86 the mp3 encoder of pydub2-mp3 is built with the options of LAME's own configure, so 24 and 32 bit audio and some VBR settings give the same audio bytes as ffmpeg's libmp3lame. They were different whenever the low 16 bits of the samples were not zero.
- A segment built with `+`, `sum()` or `append(crossfade=0)` drops its list of parts once `raw_data` or `_data` is read, so it no longer holds its audio twice, and several threads or a signal handler can read it while it is joined. `_data` appears in `vars(seg)` after the first read, as in pydub. Subclasses that define `_data`, `__init__`, `_spawn`, `__new__`, `__setattr__` or `__getattr__` get the joined data as in pydub.
- An ogg vorbis file cut before its first audio page ends raises `CouldntDecodeError` like pydub, where a few frames were returned.
- A failed `export` leaves an existing file as it was. The file is opened once the audio is encoded, where pydub empties it first.
- `utils.mediainfo` and `mediainfo_json` without ffmpeg return `{}` for a damaged wav, flac, mp3, ogg or opus file like pydub, where they said ffmpeg was not found.
- `AudioSegment.stream` raises `FileNotFoundError` for a missing file and says "cant read this file" for a damaged one, where it said it only reads wav, flac, mp3, ogg and opus.
- A `pydub2.ffmpeg_timeout` that is not a number above 0 raises `ValueError` naming it, where it raised `TypeError` or said ffmpeg ran too long. A load that ffprobe gives broken JSON for says so, where it said "cant decode this file" with Python's text.
- On Linux and macOS `ffmpeg_timeout` and Ctrl-C stop the whole process group, so an ffmpeg behind a wrapper script (snap, conda, docker) is no longer left running.
- `CouldntEncodeError` and `CouldntDecodeError` from ffmpeg have pydub's text again, with the return code, the command for an export and all of ffmpeg's output. A decode error ends with the line `ffmpeg said: ...` as before.
- Writing ogg or opus with 3 to 8 channels puts the channels in the order vorbis and opus use, as ffmpeg does. Before, the center and right channels of 5.1 came back swapped and the low frequency channel in the wrong place.
- Bitrates that ffmpeg cannot read, like `""`, `"k"`, `"-64k"`, `"1e30k"` or `"1e400k"`, raise `CouldntEncodeError` on export of ogg, opus, mp3 and flac, as with pydub. Before, ogg raised `ValueError`, `IndexError` or `TypeError`, and the others took them. An opus bitrate under 500 or over 256000 per channel raises it too. A `bitrate` with spaces around it, like `" 64k "`, works for ogg and opus. `"64m"` is 0.064 bits, as in ffmpeg, and `0x40k`, `64Ki` and `64kB` are read like ffmpeg reads them. A `bitrate` with a null byte raises `ValueError` on export of flac, as with pydub.
- `export` with a `bitrate`, `codec` or `parameters` that is not text raises `TypeError` like pydub, where a `bitrate` like `[1]` raised `CouldntEncodeError`. `utils.mediainfo` of something that is not a path or file raises `TypeError`, where it raised `AttributeError`.
- `AudioSegment.stream` takes a `start_second` or `duration` under 0.0001 (written `1e-05` by Python) and cuts it like a longer one, where it raised `ValueError`. One of 1e12 or more raises `ValueError` like a negative or `nan` one, where a huge `start_second` gave no audio and a huge `duration` gave the whole file.
- A `start_second` or `duration` that is a `Fraction`, a `bool` or under 0.0001 works when `from_file` hands the file to ffmpeg (a-law wav, a `codec`, a negative start), and a `Fraction` or `bool` works in `AudioSegment.stream` of flac, mp3, ogg and opus. ffmpeg was given text like `1/2` or `1e-05` and refused it.
- `AudioSegment.from_file` with a `start_second` or `duration` that is negative, `nan`, infinite or too large for ffmpeg does what pydub does: a short wav with `start_second=-1` is loaded through ffmpeg, and the others raise `CouldntDecodeError`, where they raised `TooManyMissingFrames`, `ValueError`, `OverflowError` or `TypeError` or gave a wrong cut. A `start_second` that is a string like `"1.5"`, or a `Decimal` for a wav, loads like pydub does, where it raised `TypeError`.
- Part of a wav loaded with `start_second` and `duration` ends at `(start_second + duration) * 1000` ms as in pydub, where it ended at `start_second * 1000 + duration * 1000` and could be a frame off.
- `export` to flac of a rate over 65535 that is not a multiple of 10, like 67685 Hz, writes the file as ffmpeg does, where it raised `CouldntEncodeError`.
- `export` to mp3 of a segment with a sample width other than 1 to 4 raises `ValueError` with pydub2-mp3 too, as pydub raises, where it wrote a file.
- `detect_silence`, `detect_nonsilent` and `split_on_silence` with a `seek_step` of 2**31 or more give the ranges pydub gives, where they raised `TypeError`.
- A float wav (or another float source) read as 16 bit rounds a sample that falls exactly between two values to the even one, as ffmpeg does, where it rounded up and was 1 off in about 1 sample in 256.
- `AudioSegment.from_file_using_temporary_files` gives 16 bit audio for ogg, opus, flac of more than 16 bit, float and a-law wav and the other formats that go through ffmpeg, as pydub does, where it gave 32 bit and a float64 wav raised `ValueError`. Float is rounded as ffmpeg does, and a value exactly halfway between two steps can be one step off. A wav that pydub reads itself (named `.wav` or given `format="wav"`) and raw data keep their width. It hands a wav given as a `pathlib.Path` or a file object to ffmpeg and gives 16 bit, as in pydub, and knows a wav by its name for a `str` or `bytes` path only.
- An ffmpeg that exits with 0 and writes no file gives an empty export, as with pydub, and not `FileNotFoundError`.
- `export` of an empty segment to mp3 or opus, of mp3 from a rate as low as 10 Hz, and of opus below 100 Hz give what pydub gives, where they raised `RuntimeError` or `CouldntEncodeError`. Opus below 100 Hz goes through ffmpeg.
- `AudioSegment.from_file` and `AudioSegment.stream` read a named pipe (fifo) once and decode the bytes, where they opened it a second time and waited for ever for a second writer or said they could not read the file.
- `export` with a `cover` that is not an mp3 picture raises the `AttributeError` of pydub with its text. A `cover` that is not a string raises what pydub raises on every format, like `AttributeError` for an int and `TypeError` for bytes. Before, flac, ogg and the other formats gave the text about mp3 for both.
- A missing attribute on an `AudioSegment` says `'AudioSegment' object has no attribute 'x'` like pydub, where it said only `x`. An `AttributeError` inside a property of a subclass keeps its own message.
- `overlay` with a `times` that is not a number works when the overlaid audio is as long as the rest, and raises `TypeError` when it has to loop, as pydub does. It raised `TypeError` every time.
- `detect_silence` with a `silence_thresh` that is not a number raises `ValueError` for a string like `"x"`, as pydub does, and not `TypeError`.
- `detect_silence`, `detect_nonsilent`, `split_on_silence`, `strip_silence`, `speedup`, `compress_dynamic_range`, `low_pass_filter` and `high_pass_filter` call what a subclass of `AudioSegment` overrides (`rms`, `max_possible_amplitude`, `_spawn`, `get_array_of_samples`, `append` and the others pydub calls there), where they went round it.
- `set_frame_rate` and `set_sample_width` with an int that does not fit in a C int raise `OverflowError` like pydub, where they raised `TypeError` and `ValueError`.
- `scipy_effects.eq` with a `gain_dB` of `nan` raises `ValueError`. It returned `None`, or raised `AttributeError` in the mid-side modes.
- `AudioSegment.from_file(..., format="wav")` reads an integer pcm wav that the decoder refuses, like one with an odd sized chunk without its pad byte, where it raised `CouldntDecodeError`.
- A file that ffmpeg turns into a wav with a rate the decoder refuses, like 1.3 MHz, loads like in pydub, where `from_file` raised `RuntimeError`.
- mp3 from `lame -p` (with crc) and mp3 with a VBRI frame decode to the same length as with ffmpeg. The Info or VBRI frame was decoded as a frame of silence and the encoder delay was not skipped.
- mp3 with frames missing or cut short in the middle, or a frame too short for its own data, decodes the frames after it like ffmpeg. They were off by up to the full scale for a frame.
- mp3 with more frames than its Info tag counts, from a doubled frame or a damaged header, has the length ffmpeg gives. ffmpeg drops the encoder padding where the tag says the audio ends, also in the middle of the file.
- mp3 frames with a mode extension outside joint stereo (mono, plain stereo) decode like ffmpeg, which applies intensity stereo only in joint stereo and lowers the gain for the mid side bit in any mode.
- Garbage in damaged mp3 frames decodes like ffmpeg: a granule whose bit reservoir is missing is silent, values that overflow ffmpeg's fixed point unscale are 0, the last values of a granule are not read past 572, and on x86 a sample far past full scale comes out as -32768. Such frames were off by up to the full scale.
- Loading raw audio from a file object whose `read` gives `None`, with `max_duration`, `max_bytes` or `max_file_size` set, keeps the `None` like pydub does, where it gave an empty segment.
- `duration=float("inf")` fails for files loaded through ffmpeg when `max_duration` is set, like it does without a limit and in pydub. It loaded the file before.
- A `frame_rate` of 2**31 or more in `loudness`, `normalize(target_lufs=...)`, `detect_speech` and `split_on_speech` raises `RuntimeError` with the rate in the message, where it raised a `TypeError` about the arguments of the core.
- A wav export at a `frame_rate` of 2**31 or more writes the same file as pydub when the header can hold it (8 bit mono up to 2**32 - 1), and raises `CouldntEncodeError` when it cannot, where pydub gives `struct.error` and pydub2 a `TypeError`. A flac, ogg or opus export at such a rate, or a wav with a `codec` or `parameters`, raises `CouldntEncodeError` too.
- An mp3 export at a `frame_rate` more than 256 times 48000 goes through ffmpeg and gives pydub's file, where it raised `RuntimeError: rates too far apart`.
- `AudioSegment.converter = None`, which `which("ffmpeg")` gives when no ffmpeg is on `PATH`, means the usual lookup, so the ffmpeg extra is used and a missing ffmpeg raises `FileNotFoundError` with the usual text. Every ffmpeg call raised `TypeError` before.
- `export` with `codec="libvorbis"` for ogg, `"libopus"` for opus, `"libmp3lame"` for mp3 or `"flac"` for flac uses the built in encoder and works without ffmpeg, as it does without `codec`. Without ffmpeg, another codec or a `cover` gives a `FileNotFoundError` that names the codec or the cover.
- Without ffmpeg, a wav, ogg or mp3 file that only ffmpeg reads (a-law, mu-law and ADPCM wav, chained ogg, ogg flac, an m4a named .mp3) says that it needs ffmpeg and how to get it, as `python -m pydub2 doctor` does, and is also a `FileNotFoundError` like in pydub. A path, a file object and `BytesIO` now give the same error, and a damaged file object of these formats no longer says that ffmpeg is missing.
- `set_frame_rate` with a float rate like `22050.0` raises `TypeError: 'float' object cannot be interpreted as an integer` like pydub, where the message showed the arguments of the core. An mp3 export with pydub2-mp3 takes a float rate and numpy ints for the rate, channels and sample width, where it raised `ctypes.ArgumentError`.
- An ogg export with a bitrate libvorbis has no setting for, like `64` or `"192"` (bits per second, as ffmpeg reads them), says so and suggests `64k` when that works, where it said only `cant encode this`. When no bitrate works for the rate and channels, like at 192 kHz, it still says `cant encode this`. pydub fails there too.

### Security

- On Windows, pydub2 no longer starts an `ffmpeg`, `ffprobe` or `ffplay` from the current folder when none is found on `PATH`. It says ffmpeg is not found.
- A float wav whose block align is smaller than its channels and sample size say no longer makes the decoder count a frame per block align and set aside up to 1024 times the decoded size, 1 GB for a 1 MB file with 256 channels.
- Writing opus from audio at a rate over 768 kHz raises `CouldntEncodeError`. The rate comes from the file header, an ogg vorbis file can say 1 GHz, and libopusenc took minutes per channel to resample from that.
- `pydub2.max_bytes` limits the size of the decoded audio, off by default. `max_duration` alone does not bound memory, a 301 kB flac that says 8 channels at 192 kHz asked for 22 GB under a limit of an hour. `max_duration` now also holds for raw and pcm input. Both limits are checked from the header before anything is read where it gives the length, hold in strict mode and in `stream`, take `inf`, and are read once at the start of a load. A file object is still read into memory whole before it is decoded, except for raw and pcm input.

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
