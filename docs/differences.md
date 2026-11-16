# Differences from pydub 0.25.1

- ffmpeg `parameters` are placed before the output file, so they take effect.
- Files that fall back to ffmpeg get the same sample width as in pydub (ffprobe, or the `ffmpeg -i` output when ffprobe is missing). Data on a pipe goes through ffmpeg's `cache:` protocol like pydub does, so mp4 with the moov atom at the end works.
- Reading and exporting wav, flac, ogg and opus, and reading mp3, need no ffmpeg. With a missing converter `from_mp3` works instead of raising `OSError`.
- mp3 decodes to the same length as ffmpeg, but about 1 sample in 1000 is off by 1 LSB, because minimp3 does its float synthesis a little differently from ffmpeg's mp3float.
- ogg vorbis has the same length as ffmpeg, samples differ by under 1024 at 32 bit scale (float rounding in a different decoder).
- opus goes through libopus, ffmpeg uses its own opus decoder. The length is the same but samples can differ a lot, up to about a quarter of full scale at low bitrates.
- Loading part of a wav, flac, mp3 or opus file with `start_second` or `duration` seeks in the file instead of running ffmpeg. You get the same frames as pydub (ffmpeg's `-ss`/`-t` rounding, then pydub's slicing), with the same small sample differences as a full load of that format. Part of an ogg vorbis file goes through ffmpeg when it is there, because ffmpeg cuts by packet times that drift from the sample count near the start of some files. Without ffmpeg it is cut at the exact sample, which can be a few hundred samples from pydub. `stream` always cuts at the exact sample.
- A file path is decoded straight from disk, so loading takes about the size of the decoded audio in memory, and wav files over 4 GB (RF64, or a data size of 0xFFFFFFFF from a pipe) work. pydub refuses wav over 4 GB.
- New: `AudioSegment.stream(path, chunk_ms=10000, start_second=None, duration=None)` yields the file in pieces with constant memory. Joined they are exactly `from_file` with the same arguments. Only for wav, flac, mp3, ogg and opus.
- `export` of wav and flac with `codec` pcm_s16le, pcm_s24le or pcm_s32le and `parameters` made of `-sample_fmt s16`/`s32`, `-ar` and `-ac` with the current rate and channels is written without ffmpeg, with the same samples. Anything else (a new rate, a new channel count, flac at 24 bit) still goes to ffmpeg. 24 bit wav gets a plain PCM header where ffmpeg writes WAVE_FORMAT_EXTENSIBLE and a LIST chunk.
- Exported ogg, opus and flac files never match ffmpeg byte for byte, but use its settings: opus is unconstrained vbr at 64k per stream plus 32k per coupled pair (96k stereo), vorbis is q3 or, with a bitrate, ABR without slow rate management, flac is level 5 with ffmpeg's block size. Sizes are within a few percent (opus at 16 kHz within 7%, flac is about 8 kB smaller because we write no padding block).
- flac export writes 8 bit audio as 16 bit and 32 bit audio as 24 bit (the low byte is dropped), like ffmpeg, so a file read back has `sample_width` 2 and 4.
- opus: the OpusHead input rate is the real rate where ffmpeg writes the rate it resampled to (48000 for 44100), and the tags name libopusenc where ffmpeg's say Lavf and Lavc. vorbis tags name libVorbis. A vorbis file has more, smaller ogg pages than ffmpeg's. The last granule is the input length, ffmpeg's own decoder can read vorbis a few ms too long.
- `utils.mediainfo` and `mediainfo_json` work without ffprobe for wav, flac, ogg, opus and mp3 (the keys ffprobe gives for the stream and format, as strings). `duration` is the decoded length, ffprobe's can be a bit longer for mp3 and opus. Other files use `ffmpeg -i` and only get `index`, `codec_type`, `codec_name` and the sample size keys.
- `pydub2.logging_utils`, `silence.db_to_float` and the other module level names of pydub exist. `pyaudioop` and the `audioop` name in the modules are not there, the C++ core does that work.
- `detect_silence` and what is built on it return `[]` for audio shorter than `min_silence_len` before looking at `seek_step`, like pydub. A `seek_step` of 0 raises `ValueError` and a float raises `TypeError`, as the `range` call in pydub does. Negative steps and a negative `min_silence_len` follow pydub's own loop.
- No warning at import when ffmpeg is missing.
- `AudioSegment` accepts `bytearray` and `memoryview` as data.
- New: `detect_speech` and `split_on_speech` in `pydub2.silence`.
- `export` takes the format from the extension of the path when `format` is left out (mp3 when the extension is unknown or the target is a file object). `format="pcm"` is the same as `"raw"`, `"m4a"` and `"aac"` work, `bitrate` can be an int and `cover` a `pathlib.Path`.
- Exporting more than 4 GiB as wav raises `CouldntEncodeError` (pydub raises `struct.error`).
- `stereo_to_ms` and `ms_to_stereo` in `pydub2.utils` work (they raise `NameError` in pydub).
- `speedup` is much faster on long audio, with the same output.
- New: `from_mono_audiosegments(..., fill_up=True)` pads shorter segments with silence, `split_on_silence` and `split_on_speech` take `return_ranges=True` and return `(chunk, (start, end))` pairs, `AudioSegment.from_numpy(array, frame_rate)` and `to_numpy()`.
- Things that hang or crash in pydub work: `overlay(empty, loop=True)`, `append` with a crossfade under 1 ms, `low_pass_filter` and `high_pass_filter` on empty audio, `mediainfo` of a file object, files whose name starts with `-`, `format="wma"`, `eq` and the other `scipy_effects` channel modes.
- `export` of wav with `tags` and a `codec` or `parameters` goes through ffmpeg so the tags are written, like pydub.
- `+=` and `sum()` of many segments are linear, `compress_dynamic_range`, `low_pass_filter` and `high_pass_filter` are several times faster, all with the same bytes. A 24 bit `AudioSegment` is widened without a Python loop.
- ffmpeg is started without a console window on Windows. Output of ffprobe and ffmpeg that is not utf-8 is decoded with errors ignored.
- Where pydub raises `audioop.error` or `wave.Error` (`set_sample_width(5)`, `set_frame_rate(0)`, a rate of 0 or below in an export, `get_dc_offset` on 3 channels) pydub2 raises `ValueError`. `remove_dc_offset` with an offset that does not fit the sample width raises `ValueError` where pydub raises `OverflowError`.
- A file that wav, flac, mp3, ogg or opus reading cannot decode (damaged, chained vorbis or chained opus) goes to ffmpeg, and raises `CouldntDecodeError` when ffmpeg is missing. `FileNotFoundError` is only for formats pydub2 cannot read itself.
- `export` of a 32 bit segment to flac writes 24 bit flac, like ffmpeg. A wav export with a float `frame_rate` rounds it like the `wave` module.
- Damaged files can have another length than with ffmpeg, a few ms of difference where the damage is. mp3 frames that minimp3 decodes differently from ffmpeg: junk in the middle of the file, mp3PRO, a stream that switches modes, and the last frame of a file cut in the middle of a frame (the length is right, the samples of that frame can differ).
- With `pydub2_mp3` and `tags`, the id3 tag is version 2.3 when `id3v2_version="3"` is asked for or ffmpeg is missing, otherwise ffmpeg writes the default 2.4.
- ffmpeg, ffprobe and ffplay are looked up on `PATH` only, never in the current directory, and an ffmpeg next to avconv wins over avconv. The functions `get_encoder_name`, `get_prober_name` and `get_player_name` return the full path they found. `AudioSegment.converter` can still be a full path.
- Files go to ffmpeg with a `file:` prefix and `-protocol_whitelist`, so a name like `concat:a.wav` is a file name and not a protocol. A file that starts like an ffconcat script, an HLS playlist or a DASH manifest raises `CouldntDecodeError`.
- `pydub2.max_duration` and `pydub2.ffmpeg_timeout` are new, both off by default.
- `mul` (`apply_gain`, fades) with a nan factor, or an infinite factor on a zero sample, raises `ValueError`. The result is undefined in audioop. `fade` with a duration far past the end of the audio and `overlay(loop=True)` give the same bytes as pydub, only faster. Sample widths other than 8, 16, 24 and 32 bit that ffprobe reports for a file are read as 16 bit.
- opusfile 0.12 is patched at build time for CVE-2022-47021 (a null pointer when an allocation fails), the fix is the upstream commit 0a4cd796 and no release has it yet.

## Files pydub reads wrongly

pydub2 decodes these properly and does not copy pydub.

- WAVE_FORMAT_EXTENSIBLE wav (ffmpeg writes it for more than 2 channels) with a-law, mu-law or float samples: pydub reads the bytes as integer pcm and gives noise.
- flac at 24 bit and 192 kHz with 6 or 8 channels: pydub gives 0 frames.
- wav with 12 bit samples in 16 bit: pydub gives a sample width of 1 and twice the frames.
- WAVE_FORMAT_EXTENSIBLE wav with 24 valid bits in 32 bit samples, from a file object: ffmpeg takes it for 24 bit float and pydub gives noise.

## pydub quirks kept on purpose

These give a result in pydub without an error, so pydub2 gives the same result.

- `AudioSegment.silent()` has the frame count pydub computes, `seg[-1]` is the last millisecond, `speedup(playback_speed=1.0)` shortens the audio.
- `set_frame_rate` resamples without a low pass filter, so high frequencies alias.
- Downmixing more than 2 channels to mono can overflow like in pydub.
- `compress_dynamic_range` releases as slowly as pydub does, a quiet part after a loud one stays attenuated for a while.
- Exporting wav without `codec` or `parameters` ignores `tags`, and tags of a loaded file are not kept when it is exported again. Read them with `mediainfo(path)["TAG"]` (needs ffprobe).
- 24 bit audio is widened to 32 bit on load, `sample_width` is 4 afterwards. `export(format="wav", codec="pcm_s24le")` writes it as 24 bit.

In the copy of pydub's own test suite (`tests/pydub_suite`) these tests are skipped for the reasons above:
`test_192khz_audio`, `test_opening_mp3_file_fails` and the three `test_partial_load_*_mp3_audio_segment` tests.
