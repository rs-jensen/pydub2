import json
import numbers
import os
import re
import shutil
import struct
import sys
import tempfile
from contextvars import ContextVar
from decimal import Decimal
from io import BytesIO

import pydub2
from .exceptions import CouldntDecodeError, CouldntEncodeError, tagged
from .logging_utils import log_conversion
from .utils import _using, check_duration, extra_ffmpeg, limits, mediainfo_json, source, timeout, which
from .utils import run as run_command


# a list here gets every ffmpeg command of this thread, doctor asks this way whether a load used ffmpeg
_seen = ContextVar("seen", default=None)


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
        raise tagged(FileNotFoundError(("ffmpeg not found" if name == "ffmpeg" else "no ffmpeg at " + name) +
                                       ", pydub2 needs it for " + what + (". strict mode needs ffmpeg" if pydub2.strict else "") +
                                       ('. install ffmpeg or pip install "pydub2[ffmpeg]"' if name == "ffmpeg" else "")),
                     None if what == "this file" else what, "needs_ffmpeg",
                     'pip install "pydub2[ffmpeg]"' if name == "ffmpeg" else None)
    cmd = [exe, "-y", "-nostdin"] + args
    log_conversion(cmd)
    seen = _seen.get()
    if seen is not None:
        seen.append(cmd)
    return run_command(cmd, data)


def seconds(v):
    # ffmpeg reads 1e+30 but not 1e-05, 1/2 or True
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, numbers.Real) and not isinstance(v, numbers.Integral):
        try:
            text = str(v if isinstance(v, float) else float(v))
        except OverflowError:
            return str(v)
        return format(Decimal(text), "f") if "e-" in text else text
    return str(v)


def to_wav(converter, path=None, data=None, format=None, codec=None, parameters=None,
           start_second=None, duration=None, lim=None, probe=True):
    lim = lim or limits()
    timeout()
    try:
        return _to_wav(converter, path, data, format, codec, parameters, start_second, duration, lim, probe)
    except json.JSONDecodeError:
        raise tagged(CouldntDecodeError("cant decode this file: ffprobe gave no usable answer"), format, "cant_decode")
    except (KeyError, TypeError, ValueError, TimeoutError, struct.error) as e:
        raise tagged(CouldntDecodeError("cant decode this file: " + str(e)), format,
                     "timeout" if isinstance(e, TimeoutError) else "cant_decode")


def _to_wav(converter, path, data, format, codec, parameters, start_second, duration, lim, probe):
    format = {"m4a": "mp4", "wave": "wav", "wma": "asf"}.get(format, format)
    opts = (["-f", format] if format else []) + (["-acodec", codec] if codec else [])
    inp = source(path if path is not None else BytesIO(data), exe=find(converter))[0]
    out = ["-vn", "-f", "wav"]
    if not codec and probe:
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
        out += ["-ss", seconds(start_second)]
    limit, max_bytes = lim[:2]
    if limit is not None:
        # one second over, so a file past the limit is seen and not just cut
        duration = limit + 1 if duration is None else min(duration, limit + 1)
    if duration is not None:
        out += ["-t", seconds(duration)]
    # room for the wav header, output that reaches it was cut and is over max_bytes
    fs = None if max_bytes is None else int(max_bytes) + 65536
    if fs is not None:
        out += ["-fs", str(fs)]
    # pydub puts them after the output, where ffmpeg ignores them
    out += ["-"] + (parameters or []) if pydub2.strict else (parameters or []) + ["-"]
    p = run(converter, opts + inp + out, None if path is not None else data, format or "this file")
    if p.returncode != 0 or not p.stdout:
        err = p.stderr.decode(errors="ignore")
        lines = [x.strip() for x in err.splitlines() if x.strip()]
        # drop the [mp3 @ 0x...] prefix, the address changes every run
        last = re.sub(r"^\[[^\]]*\] ", "", lines[-1]) if lines else "no output"
        # pydub's text, then the last line alone, which doctor shows
        raise tagged(CouldntDecodeError("Decoding failed. ffmpeg returned error code: %d\n\nOutput from ffmpeg/avlib:\n\n%s"
                                        "\nffmpeg said: %s" % (p.returncode, err, last)), format, "cant_decode")
    wav = bytearray(p.stdout)
    # the output is copied again on return, so the first copy goes now
    del p
    i = 12
    while wav[i:i + 4] != b"data":
        size = struct.unpack_from("<I", wav, i + 4)[0]
        i += 8 + size + size % 2
    # ffmpeg cant seek back on a pipe so the sizes in the header are 0xffffffff
    wav[4:8] = struct.pack("<I", len(wav) - 8)
    wav[i + 4:i + 8] = struct.pack("<I", len(wav) - i - 8)
    if fs is not None and len(wav) >= fs:
        raise tagged(CouldntDecodeError("this file is larger than max_bytes"), reason="too_big")
    if limit is not None or fs is not None:
        channels, rate, _, align, bits = struct.unpack_from("<HIIHH", wav, 22)
        check_duration((len(wav) - i - 8) // align, rate, channels * (4 if bits == 24 else bits // 8), lim)
    return bytes(wav)


def from_wav(converter, wav, format, codec=None, bitrate=None, parameters=None, tags=None,
             id3v2_version="4", cover=None):
    codec = codec or {"ogg": "libvorbis"}.get(format)
    muxer = format if pydub2.strict else {"m4a": "ipod", "aac": "adts", "wma": "asf"}.get(format, format)
    args = ["-f", "wav", "-i", "pipe:0"]
    if cover is not None:
        if isinstance(cover, os.PathLike):
            cover = os.fspath(cover)
        if not cover.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")) or format != "mp3":
            raise AttributeError("Currently cover images are only supported by MP3 files. "
                                 "The allowed image formats are: .tif, .jpg, .bmp, .jpeg and .png.")
        args += ["-protocol_whitelist", "file", "-i", "file:" + cover, "-map", "0", "-map", "1", "-c:v", "mjpeg"]
    if codec:
        args += ["-acodec", codec]
    if bitrate is not None:
        args += ["-b:a", os.fsdecode(bitrate) if isinstance(bitrate, bytes) else str(bitrate)]
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
            raise tagged(CouldntEncodeError("cant encode this file: " + str(e)), format, "timeout")
        except OSError as e:
            if find(converter) is None:
                raise
            raise tagged(CouldntEncodeError("cant start ffmpeg (%s), tags that are too long for the command line can cause this" % e),
                         format, "cant_start")
        if p.returncode != 0:
            raise tagged(CouldntEncodeError(
                "Encoding failed. ffmpeg/avlib returned error code: {0}\n\nCommand:{1}\n\nOutput from ffmpeg/avlib:\n\n{2}"
                .format(p.returncode, p.args, p.stderr.decode(errors="ignore"))), format, "cant_encode")
        # pydub makes the file before ffmpeg runs, so no output at all is an empty export there
        if not os.path.exists(name):
            return b""
        with open(name, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
