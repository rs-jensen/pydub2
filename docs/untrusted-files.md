# Untrusted files

If your service decodes uploads, set a limit on size, on length and on how long ffmpeg may run. All are off by default.

```python
import pydub2
pydub2.max_bytes = 200 << 20  # decoded size, larger files raise CouldntDecodeError
pydub2.max_duration = 3600    # seconds, longer files raise CouldntDecodeError
pydub2.max_file_size = 50 << 20  # the file itself, larger files raise CouldntDecodeError
pydub2.ffmpeg_timeout = 60    # seconds for every ffmpeg and ffprobe call
seg = pydub2.AudioSegment.from_file(upload)
```

`max_bytes` is what bounds memory. `max_duration` alone does not: the file picks the rate, the channels
and the sample width, so a 301 kB flac can ask for 22 GB within an hour. Both are checked from the header
before anything is read where it gives the length, and again while the file is decoded, so a header that
lies does not get around them. They hold for raw and pcm input, in strict mode and in `stream`, and
are read once at the start of each load. A limit that is not `None` or a number of 0 or more raises
`ValueError`, and one too large to matter, like `inf`, is no limit.

A file object, such as a `BytesIO` or an uploaded file, and a named pipe are read into memory whole
before they are decoded. `max_bytes` and `max_duration` bound what the file decodes to, not that read.
`max_file_size` bounds the read, and it is the only limit that bounds a named pipe or a socket. A path is refused from its size before anything is read. A file object
that can seek is measured from where it stands to its end, because that is what is read. A wav is the
exception, it is read from the start as in pydub, and so are `mediainfo` and `mediainfo_json`, so those
measure the whole file. A pipe or a socket is read in pieces and stops one byte past the limit, and so
does a file that grows after it was measured. This covers `from_file`, `stream`, `AudioSegment(data=file)`,
`mediainfo` and `mediainfo_json`. It cannot be worked out from `max_bytes`, because tags, cover art
and padding can make a file far larger than its audio, so set it to the largest upload you accept.

Never build `format`, `codec`, `parameters`, `cover` or `AudioSegment.converter` from user input, they
go to ffmpeg as they are. Do not show exception text to end users, ffmpeg errors can hold paths on your
server. Numbers such as `silent(duration)`, `seg * n` and `set_frame_rate(n)` use memory in proportion to
the number, so keep them in bounds. For the rest, run decoding in a worker with a memory and time limit.

To report a security problem, see [SECURITY.md](../SECURITY.md).
