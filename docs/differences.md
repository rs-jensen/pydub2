# Differences from pydub 0.25.1

This page lists where pydub2 does something other than pydub 0.25.1. The short version is in the README.

With `pydub2.strict = True` or `PYDUB2_STRICT=1` the sections "Reading files", "Damaged files", "Stream info and tags without ffprobe", "Writing files" and the `parameters` and ffmpeg-missing lines below do not apply: those calls use ffmpeg and ffprobe like pydub. What stays is listed in [migrating.md](migrating.md#strict-mode).

## Reading files

- wav, flac, ogg and opus are read without ffmpeg, and so is mp3. With a missing converter `from_mp3` works instead of raising `OSError`.
- A file path is decoded straight from disk, so loading takes about the size of the decoded audio in memory.
- Wav files over 4 GB load (RF64, or a data size of 0xFFFFFFFF from a pipe). pydub refuses them.
- Loading part of a wav, flac, mp3 or opus file with `start_second` or `duration` seeks in the file instead of running ffmpeg. You get the same frames as pydub (ffmpeg's `-ss` and `-t` rounding, then pydub's slicing), with the same small sample differences as a full load of that format.
- Part of an ogg vorbis file goes through ffmpeg when it is there. ffmpeg cuts by packet times that drift from the sample count near the start of some files. Without ffmpeg the file is cut at the exact sample, which can be a few hundred samples from pydub. `stream` always cuts at the exact sample.
- `AudioSegment` accepts `bytearray` and `memoryview` as data.
- Sample widths other than 8, 16, 24 and 32 bit that ffprobe reports for a file are read as 16 bit.

### How the samples differ

- mp3 decodes to the same length as ffmpeg. A few samples in 1000 are off by 1 LSB (up to 1.5 in 100 against an x86 ffmpeg), because minimp3 does its float synthesis a little differently from ffmpeg's mp3float.
- ogg vorbis has the same length as ffmpeg. Samples differ by under 1024 at 32 bit scale, from float rounding in a different decoder.
- opus goes through libopus, where ffmpeg uses its own opus decoder. The length is the same, but samples can differ a lot, up to about a quarter of full scale at low bitrates.
- Chained opus (several opus streams one after the other) decodes without ffmpeg. libopus starts each link fresh where ffmpeg's decoder carries its state across, so a few samples after each join can differ.
- A chain whose links have another channel count needs ffmpeg.
- With ffmpeg installed, `from_file` still sends a chained file to ffmpeg. `stream` does not.
- `rms` of 32 bit audio is summed without fused multiply-add, like audioop on x86. audioop on arm64 (macOS, Linux aarch64) fuses the squares. There a few `rms` values are 1 higher or lower, `dBFS` changes by about 1e-8 dB, and `compress_dynamic_range` can differ by 1 in one 32 bit sample.

### Damaged files

- Damaged files can have another length than with ffmpeg, a few ms of difference where the damage is.
- mp3 keeps and drops frames around junk like ffmpeg does. It mixes a stream that switches between mono and stereo like ffmpeg, and it decodes a last frame cut short up to the cut.
- mp3 with junk in front of the first frame or the id3 tag (zeros, a stray byte, a tag with a wrong size, a second tag) is decoded without ffmpeg. It finds its first frame like ffmpeg does: two headers in a row within 64k. A file that starts with junk also needs four frames in a row that start within the first 1 MiB.
- mp3 inside mpeg ts, mpeg ps, mp4, matroska, avi, flv, zip, tar and the like goes to ffmpeg. So does a junk start whose frames are cut up more than twice.
- Past 64k of junk, or with a long run of repeated sync words in it, the result can differ by a frame or a few samples.
- Still decoded differently: mp3PRO, a last frame cut inside its first 36 bytes, frames whose granules claim more bits than the frame has, and a short block followed by a normal one (after a dropped frame). A seek into such a damaged stream can differ from the full read for a frame or two after the seek point.
- A flac frame with a bad crc is kept as silence of the same length. ffmpeg keeps its damaged samples, which can be loud noise. The length is the same and that one frame differs.
- A flac frame with a damaged or missing header is kept as silence too. ffmpeg drops it, so the file is one frame longer than with ffmpeg.
- A flac frame that dr_flac cannot decode is dropped like ffmpeg does, and decoding goes on after it. The first frame, the last frame and more than 8 damaged frames in a row are dropped, where ffmpeg may keep them. A seek into a silent frame lands on the next good one.
- Damaged ogg vorbis keeps the audio of a page with a bad checksum, where ffmpeg drops the page (about a second).
- opus with a bad page makes the built in decoder give up, so `from_file` uses ffmpeg.
- A file that wav, flac, mp3, ogg or opus reading cannot decode (damaged or chained vorbis) goes to ffmpeg. It raises `CouldntDecodeError` when ffmpeg is missing.
- mp3 in free format, and an mp3 whose sample rate changes inside the file, go to ffmpeg. ffmpeg cannot read free format either, so such a file raises `CouldntDecodeError` with or without ffmpeg, like pydub. An mp3 with a single audio frame raises like pydub too.

### Stream info and tags without ffprobe

- `utils.mediainfo` and `mediainfo_json` work without ffprobe for wav, flac, ogg, opus and mp3. They return a subset of what ffprobe gives (15 of about 37 keys).
- The stream keys are `index`, `codec_name`, `codec_type`, `codec_tag_string`, `codec_tag`, `sample_fmt`, `sample_rate`, `channels`, `bits_per_sample`, `bits_per_raw_sample` and `duration`. The format keys are `nb_streams`, `format_name` and `size`, and `TAG` when there are tags.
- Not there: `bit_rate`, `channel_layout`, `start_time`, `duration_ts`, `time_base`, `nb_frames`, `initial_padding`, `probe_score`, `codec_long_name`, `profile`, `disposition` and the rest.
- All values are strings in `mediainfo`. `duration` is the decoded length. ffprobe's can be a bit longer for mp3 and opus.
- Other files (m4a, wma, aiff and so on) use `ffmpeg -i`, the one from `AudioSegment.converter`. They only get `index`, `codec_type`, `codec_name`, `bits_per_sample` and `bits_per_raw_sample`. There are no tags, and no `channels`, `sample_rate`, `duration` or `size`.
- For those files the two sample size keys come from the sample format ffmpeg prints. A compressed file gets 32 where ffprobe says 0 and N/A.
- With ffprobe installed you get all of its keys.
- The tag reader leaves out `METADATA_BLOCK_PICTURE`, because ffprobe shows it as a video stream. It keeps up to 16 MiB of tag text and 65536 tags, and skips a single value that does not fit.
- Without ffprobe, `mediainfo` returns the tags of wav (LIST INFO), flac, ogg, opus and mp3 files, like ffprobe does. flac, wav and mp3 tags are at format level, ogg and opus tags at stream level. Doubled keys are joined with `;`.
- For mp3 that means ID3v2.2, 2.3 and 2.4 at the start (stacked tags, compressed and unsynchronised frames; encrypted frames are skipped). ID3v1 is read only when the v2 tags gave nothing. The encoder name from the Xing or Info frame is a stream tag.
- A TXXX key that only differs in case from another key is dropped, like ffmpeg 7 does. ffmpeg 8 keeps both.
- A UTF-16 comment with an empty description and no byte order mark is kept, like ffmpeg 8.1 does. ffmpeg 7 and 8.0 drop it.
- ID3 chapters, cover pictures and GEOB frames are left out. A file the built in decoder cannot open gets no tags.
- Invalid utf-8 is replaced per byte in a slightly different way. `CHAPTER001` style keys and cover pictures are shown as tags, where ffprobe makes chapters and streams of them.

## Writing files

- wav, flac, ogg and opus are written without ffmpeg.
- `export` takes the format from the extension of the path when `format` is left out (mp3 when the extension is unknown or the target is a file object).
- `format="pcm"` is the same as `"raw"`, and `"m4a"` and `"aac"` work. `bitrate` can be an int and `cover` can be a `pathlib.Path`.
- `export` of wav and flac is done without ffmpeg when `codec` is pcm_s16le, pcm_s24le or pcm_s32le (flac: none or flac), and `parameters` is only `-sample_fmt s16` or `s32`, `-ar` and `-ac` with the current rate and channels. The samples are the same as ffmpeg's.
- Anything else, such as a new rate or a new channel count, still goes to ffmpeg.
- 24 bit wav gets a plain PCM header, where ffmpeg writes WAVE_FORMAT_EXTENSIBLE and a LIST chunk.
- flac export writes 8 bit audio as 16 bit and 32 bit audio as 24 bit (the low byte is dropped), like ffmpeg. A file read back has `sample_width` 2 and 4. Flac with `-sample_fmt s32` is 24 bit too.
- Exported ogg, opus and flac files never match ffmpeg byte for byte, but they use its settings. opus is unconstrained vbr at 64k per stream plus 32k per coupled pair (96k stereo). vorbis is q3, or ABR without slow rate management when a bitrate is given. flac is level 5 with ffmpeg's block size.
- Sizes are within a few percent of ffmpeg's. opus at 16 kHz is within 7%. flac is about 8 kB smaller because we write no padding block.
- opus: the OpusHead input rate is the real rate, where ffmpeg writes the rate it resampled to (48000 for 44100). The tags name libopusenc where ffmpeg's say Lavf and Lavc.
- vorbis tags name libVorbis. A vorbis file has more, smaller ogg pages than ffmpeg's. The last granule is the input length. ffmpeg's own decoder can read vorbis a few ms too long.
- mp3 written by `pydub2_mp3` always starts with an Info frame carrying the encoder delay and padding, so the file reads back at the length it was written with. That includes low constant bitrates, where lame has no room for its own and ffmpeg uses a bigger frame. The crc fields of that frame are left 0 when lame did not write it.
- With `pydub2_mp3` and `tags`, the id3 tag is version 2.3 when `id3v2_version="3"` is asked for or ffmpeg is missing. Otherwise ffmpeg writes the default 2.4.
- `export` of flac, ogg and opus with `tags` writes the Vorbis comments without ffmpeg, with the keys, case, order and values ffmpeg gives them. `album_artist`, `track`, `disc` and `comment` are written as ALBUMARTIST, TRACKNUMBER, DISCNUMBER and DESCRIPTION. Keys match without case and the last one wins. An empty value or key drops the entry, and `a=b` as a key splits at the first `=`.
- The `encoder` tag says `pydub2` where ffmpeg's says Lavf or Lavc. A flac comment block over 16 MiB raises `CouldntEncodeError`, where ffmpeg fails earlier on the command line length. `cover` and mp3 are unchanged.
- `export` of wav with `tags` and a `codec` or `parameters` goes through ffmpeg, so the tags are written, like pydub.
- Exporting more than 4 GiB as wav raises `CouldntEncodeError`. pydub raises `struct.error`.
- A wav export with a float `frame_rate` rounds it like the `wave` module. A float `frame_rate` (`silent(frame_rate=3.5)`, `44100.0`) works everywhere. `detect_silence` and what is built on it do the millisecond math with the float, like pydub.

## Errors

- Where pydub raises `audioop.error` or `wave.Error`, pydub2 raises `ValueError`. That covers `set_sample_width(5)`, `set_frame_rate(0)`, a rate of 0 or below in an export and `get_dc_offset` on 3 channels.
- `remove_dc_offset` with an offset that does not fit the sample width raises `ValueError` where pydub raises `OverflowError`.
- A negative width, channel count or rate goes the same way. `set_sample_width(-1)`, `set_frame_rate(-5)` and a wav export with width -1 are `ValueError`. `set_channels(0)` is `ValueError` in both.
- On a new `AudioSegment` a width, channel count or rate of 0 or below raises the same class as pydub (`ZeroDivisionError`, or `ValueError` from `len`).
- `mul` (`apply_gain`, fades) with a nan factor, or an infinite factor on a zero sample, raises `ValueError`. The result is undefined in audioop.
- When ffmpeg is missing, `FileNotFoundError` is only raised for formats pydub2 cannot read itself. For wav, flac, mp3, ogg and opus a file it cannot decode raises `CouldntDecodeError`.
- `pydub2.max_duration` and `pydub2.ffmpeg_timeout` are new, both off by default. A file longer than `max_duration` raises `CouldntDecodeError`.

## ffmpeg

- `parameters` are placed before the output file, so they take effect.
- Files that fall back to ffmpeg get the same sample width as in pydub (from ffprobe, or from the `ffmpeg -i` output when ffprobe is missing).
- Data on a pipe goes through ffmpeg's `cache:` protocol like in pydub, so mp4 with the moov atom at the end works.
- No warning at import when ffmpeg is missing.
- ffmpeg is started without a console window on Windows. Output of ffprobe and ffmpeg that is not utf-8 is decoded with errors ignored.
- ffmpeg, ffprobe and ffplay are looked up on `PATH`, never in the current directory. An ffmpeg next to avconv wins over avconv.
- ffprobe and ffplay are also looked for in the folder of `AudioSegment.converter` when it has one and `PATH` has none.
- `from_file` and `export` use the `converter` of the class they are called on, so a subclass of `AudioSegment` can have its own. `converter` can be a `pathlib.Path`.
- A private ffmpeg set as `converter` (absolute, relative or with spaces in the path) is enough for reading, `mediainfo` and export. In pydub the ffprobe call fails with `FileNotFoundError`.
- A monkeypatched `get_prober_name` is used for `mediainfo` and `mediainfo_json`. `get_encoder_name`, `get_prober_name` and `get_player_name` return the full path they found, and `get_prober_name` gives no warning when it finds nothing.
- Setting `AudioSegment.ffmpeg` or `.ffprobe` does nothing, as in pydub. Set `converter`.
- Files go to ffmpeg with a `file:` prefix and `-protocol_whitelist`, so a name like `concat:a.wav` is just a file name.
- A URL passed as the file name (`from_file("http://...")`) is not fetched. pydub hands it to ffmpeg, pydub2 looks for a file with that name.
- A file that starts like an ffconcat script, an HLS playlist or a DASH manifest raises `CouldntDecodeError`.
- ffmpeg and ffprobe are also given the list of demuxers they may use (`-format_whitelist`). It is taken from `ffmpeg -demuxers` without hls, dash, concat, imf, sdp, rtsp, rtp and lavfi. A playlist hidden behind a comment or blank lines then cannot make them open other files. Asking for one of those with `format=` fails too.
- An ffmpeg that cannot list its demuxers raises `CouldntDecodeError` instead of running without the list.
- The oldest ffmpeg that works is 3.3 (April 2017). `-protocol_whitelist` and `-format_whitelist` came in 3.0 and `-demuxers` in 3.3.

## Things pydub gets wrong that pydub2 does not copy

These files are read properly:

- WAVE_FORMAT_EXTENSIBLE wav (ffmpeg writes it for more than 2 channels) with a-law, mu-law or float samples: pydub reads the bytes as integer pcm and gives noise.
- flac at 24 bit and 192 kHz with 6 or 8 channels: pydub gives 0 frames.
- wav with 12 bit samples in 16 bit: pydub gives a sample width of 1 and twice the frames.
- WAVE_FORMAT_EXTENSIBLE wav with 24 valid bits in 32 bit samples, from a file object: ffmpeg takes it for 24 bit float and pydub gives noise.

These calls hang or crash in pydub and work in pydub2:

- `overlay(empty, loop=True)`, and `append` with a crossfade under 1 ms.
- `low_pass_filter` and `high_pass_filter` on empty audio.
- `mediainfo` of a file object, files whose name starts with `-`, and `format="wma"`.
- `eq` and the other `scipy_effects` channel modes.
- `stereo_to_ms` and `ms_to_stereo` in `pydub2.utils` (they raise `NameError` in pydub).
- `strip_silence` and `append` with a crossfade that covers no frame (a few Hz, or under 1 ms) return the audio. pydub raises `ZeroDivisionError` in `fade`. A fade over zero frames does nothing here.

## pydub quirks kept on purpose

These give a result in pydub without an error, so pydub2 gives the same result.

- `AudioSegment.silent()` has the frame count pydub computes, `seg[-1]` is the last millisecond, and `speedup(playback_speed=1.0)` shortens the audio.
- `set_frame_rate` resamples without a low pass filter, so high frequencies alias.
- Downmixing more than 2 channels to mono can overflow like in pydub.
- `compress_dynamic_range` releases as slowly as pydub does. A quiet part after a loud one stays attenuated for a while.
- Exporting wav without `codec` or `parameters` ignores `tags`, and tags of a loaded file are not kept when it is exported again. Read them with `mediainfo(path)["TAG"]`.
- 24 bit audio is widened to 32 bit on load, so `sample_width` is 4 afterwards. `export(format="wav", codec="pcm_s24le")` writes it as 24 bit.
- `detect_silence` and what is built on it return `[]` for audio shorter than `min_silence_len` before looking at `seek_step`. A `seek_step` of 0 raises `ValueError` and a float raises `TypeError`, as the `range` call in pydub does. Negative steps and a negative `min_silence_len` follow pydub's own loop.
- `speedup` with a `chunk_size` that leaves no room to crossfade (`chunk_size=1`) raises `ZeroDivisionError` at once, like pydub does when it fades the empty first chunk.

## New functions and speed

- `AudioSegment.stream(path, chunk_ms=10000, start_second=None, duration=None)` yields the file in pieces with constant memory. It only works for wav, flac, mp3, ogg and opus. The pieces joined are the same as `from_file` with the same arguments. The exception is `start_second` or `duration` on an ogg vorbis file when ffmpeg is installed: `from_file` then uses ffmpeg and `stream` cuts at the exact sample.
- `detect_speech` and `split_on_speech` in `pydub2.silence`.
- `from_mono_audiosegments(..., fill_up=True)` pads shorter segments with silence.
- `split_on_silence` and `split_on_speech` take `return_ranges=True` and return `(chunk, (start, end))` pairs.
- `AudioSegment.from_numpy(array, frame_rate)` and `to_numpy()`.
- `pydub2.logging_utils`, `silence.db_to_float` and the other module level names of pydub exist. `pyaudioop` and the `audioop` name in the modules are not there, because the C++ core does that work.
- `speedup` is much faster on long audio, with the same output.
- `+=` and `sum()` of many segments are linear. `compress_dynamic_range`, `low_pass_filter` and `high_pass_filter` are several times faster. All of them give the same bytes.
- A 24 bit `AudioSegment` is widened without a Python loop.
- `fade` with a duration far past the end of the audio and `overlay(loop=True)` give the same bytes as pydub, only faster.
- opusfile 0.12 is patched at build time for CVE-2022-47021 (a null pointer when an allocation fails). The fix is the upstream commit 0a4cd796, and no release has it yet. The build also has the fix from opusfile master for a leak on a failed open.

The copy of pydub's own test suite (`tests/pydub_suite`) passes 113 of 113 tests with pydub2-mp3 installed, none are skipped.
