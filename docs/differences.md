# Differences from pydub 0.25.1

- ffmpeg `parameters` are placed before the output file, so they take effect.
- Files that fall back to ffmpeg come out 16 bit, because there is no ffprobe step. A 64 bit wav is 2 bytes per sample here and 8 in pydub.
- Reading and exporting wav, flac, ogg and opus, and reading mp3, need no ffmpeg. With a missing converter `from_mp3` works instead of raising `OSError`.
- Lossy formats decoded by the bundled decoders differ from ffmpeg by a few LSB.
- Loading part of a lossy file with `start_second` or `duration` goes through ffmpeg, so it can differ by a few LSB from slicing the fully loaded segment.
- The length of an ogg vorbis file can differ from ffmpeg's by under 1024 frames.
- `detect_silence` raises `ValueError` when `seek_step` is below 1.
- No warning at import when ffmpeg is missing.
- `AudioSegment` accepts `bytearray` and `memoryview` as data.
- New: `detect_speech` and `split_on_speech` in `pydub2.silence`.

In the copy of pydub's own test suite (`tests/pydub_suite`) these tests are skipped for the reasons above:
`test_192khz_audio`, `test_opening_mp3_file_fails` and the three `test_partial_load_*_mp3_audio_segment` tests.
