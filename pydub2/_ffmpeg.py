import os
import re
import shutil
import struct
import sys
import tempfile
from io import BytesIO

import pydub2
from .exceptions import CouldntDecodeError, CouldntEncodeError
from .logging_utils import log_conversion
from .utils import _using, check_duration, extra_ffmpeg, mediainfo_json, source, which
from .utils import run as run_command


def find(converter):
    for name in (converter, "avconv"):
        path = which(name)
        if path:
            return path
    if os.fspath(converter) == "ffmpeg":
        return extra_ffmpeg()


def run(converter, args, data, what):
    exe = find(converter)
    if exe is None:
        name = os.fspath(converter)
        raise FileNotFoundError(("ffmpeg not found" if name == "ffmpeg" else "no ffmpeg at " + name) +
                                ", pydub2 needs it for " + what + (". strict mode needs ffmpeg" if pydub2.strict else "") +
                                ('. install ffmpeg or pip install "pydub2[ffmpeg]"' if name == "ffmpeg" else ""))
    cmd = [exe, "-y", "-nostdin"] + args
    log_conversion(cmd)
    return run_command(cmd, data)


def to_wav(converter, path=None, data=None, format=None, codec=None, parameters=None,
           start_second=None, duration=None):
    try:
        return _to_wav(converter, path, data, format, codec, parameters, start_second, duration)
    except (KeyError, TypeError, ValueError, TimeoutError, struct.error) as e:
        raise CouldntDecodeError("cant decode this file: " + str(e))


def _to_wav(converter, path, data, format, codec, parameters, start_second, duration):
    format = {"m4a": "mp4", "wave": "wav", "wma": "asf"}.get(format, format)
    opts = (["-f", format] if format else []) + (["-acodec", codec] if codec else [])
    inp = source(path if path is not None else BytesIO(data), exe=find(converter))[0]
    out = ["-vn", "-f", "wav"]
    if not codec:
        with _using(converter):
            info = mediainfo_json(path if path is not None else BytesIO(data))
        streams = [x for x in info.get("streams", []) if x.get("codec_type") == "audio"]
        if streams:
            stream = streams[0]
            bits = stream.get("bits_per_sample")
            # ffprobe says fltp for these even when they are not
            if stream.get("sample_fmt") == "fltp" and stream.get("codec_name") in ["mp3", "mp4", "aac", "webm", "ogg"]:
                bits = 16
            if bits not in (8, 16, 24, 32) and not pydub2.strict:
                bits = 16
            out = ["-acodec", "pcm_u8" if bits == 8 else "pcm_s%dle" % bits] + out
    if start_second is not None:
        out += ["-ss", str(start_second)]
    limit = pydub2.max_duration
    if limit is not None:
        # one second over, so a file past the limit is seen and not just cut
        duration = limit + 1 if duration is None else min(duration, limit + 1)
    if duration is not None:
        out += ["-t", str(duration)]
    # pydub puts them after the output, where ffmpeg ignores them
    out += ["-"] + (parameters or []) if pydub2.strict else (parameters or []) + ["-"]
    p = run(converter, opts + inp + out, None if path is not None else data, format or "this file")
    if p.returncode != 0 or not p.stdout:
        lines = [x.strip() for x in p.stderr.decode(errors="ignore").splitlines() if x.strip()]
        # drop the [mp3 @ 0x...] prefix, the address changes every run
        last = re.sub(r"^\[[^\]]*\] ", "", lines[-1]) if lines else "no output"
        raise CouldntDecodeError("Decoding failed. ffmpeg returned error code: %d\n\nformat: %s\nffmpeg said: %s"
                                 % (p.returncode, format or "unknown", last))
    wav = bytearray(p.stdout)
    i = 12
    while wav[i:i + 4] != b"data":
        size = struct.unpack_from("<I", wav, i + 4)[0]
        i += 8 + size + size % 2
    # ffmpeg cant seek back on a pipe so the sizes in the header are 0xffffffff
    wav[4:8] = struct.pack("<I", len(wav) - 8)
    wav[i + 4:i + 8] = struct.pack("<I", len(wav) - i - 8)
    if limit is not None:
        channels, rate, _, align = struct.unpack_from("<HIIH", wav, 22)
        check_duration((len(wav) - i - 8) // align, rate)
    return bytes(wav)


def from_wav(converter, wav, format, codec=None, bitrate=None, parameters=None, tags=None,
             id3v2_version="4", cover=None):
    codec = codec or {"ogg": "libvorbis"}.get(format)
    muxer = format if pydub2.strict else {"m4a": "ipod", "aac": "adts", "wma": "asf"}.get(format, format)
    args = ["-f", "wav", "-i", "pipe:0"]
    if cover is not None:
        cover = os.fspath(cover)
        if format != "mp3" or not cover.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
            raise AttributeError("cover images only work with mp3 and png, jpg, bmp or tif files")
        args += ["-protocol_whitelist", "file", "-i", "file:" + cover, "-map", "0", "-map", "1", "-c:v", "mjpeg"]
    if codec:
        args += ["-acodec", codec]
    if bitrate:
        args += ["-b:a", str(bitrate)]
    args += parameters or []
    for k, v in (tags or {}).items():
        args += ["-metadata", "%s=%s" % (k, v)]
    if tags and format == "mp3":
        args += ["-id3v2_version", id3v2_version]
    if codec == "mp3" and sys.platform == "darwin":
        args += ["-write_xing", "0"]
    # a virus scanner can still hold the file when it is removed, that must not fail the export
    d = tempfile.mkdtemp()
    try:
        name = os.path.join(d, "out")
        try:
            p = run(converter, args + ["-f", muxer, name], wav, format)
        except TimeoutError as e:
            raise CouldntEncodeError("cant encode this file: " + str(e))
        except OSError as e:
            if find(converter) is None:
                raise
            raise CouldntEncodeError("cant start ffmpeg (%s), tags that are too long for the command line can cause this" % e)
        if p.returncode != 0:
            raise CouldntEncodeError("cant encode this file: " + p.stderr.decode(errors="ignore")[-500:])
        with open(name, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
