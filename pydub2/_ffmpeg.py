import os
import shutil
import struct
import subprocess
import sys
import tempfile
from io import BytesIO

from .exceptions import CouldntDecodeError, CouldntEncodeError
from .logging_utils import log_conversion
from .utils import mediainfo_json, NOWINDOW


def find(converter):
    for name in (converter, "avconv"):
        path = shutil.which(name)
        if path:
            return path


def run(converter, args, data, what):
    exe = find(converter)
    if exe is None:
        raise FileNotFoundError("ffmpeg not found, pydub2 needs it for " + what)
    cmd = [exe, "-y", "-nostdin"] + args
    log_conversion(cmd)
    return subprocess.run(cmd, input=data, capture_output=True, creationflags=NOWINDOW)


def to_wav(converter, path=None, data=None, format=None, codec=None, parameters=None,
           start_second=None, duration=None):
    format = {"m4a": "mp4", "wave": "wav", "wma": "asf"}.get(format, format)
    opts = (["-f", format] if format else []) + (["-acodec", codec] if codec else [])
    if path is not None:
        inp = ["-i", path]
    else:
        inp = ["-read_ahead_limit", "-1", "-i", "cache:pipe:0"]
    out = ["-vn", "-f", "wav"]
    if not codec:
        info = mediainfo_json(path if path is not None else BytesIO(data))
        streams = [x for x in info.get("streams", []) if x["codec_type"] == "audio"]
        if streams:
            stream = streams[0]
            bits = stream["bits_per_sample"]
            # ffprobe says fltp for these even when they are not
            if stream.get("sample_fmt") == "fltp" and stream.get("codec_name") in ["mp3", "mp4", "aac", "webm", "ogg"]:
                bits = 16
            out = ["-acodec", "pcm_u8" if bits == 8 else "pcm_s%dle" % bits] + out
    if start_second is not None:
        out += ["-ss", str(start_second)]
    if duration is not None:
        out += ["-t", str(duration)]
    out += (parameters or []) + ["-"]
    p = run(converter, opts + inp + out, None if path is not None else data, format or "this file")
    if p.returncode != 0 or not p.stdout:
        raise CouldntDecodeError("cant decode this file: " + p.stderr.decode(errors="ignore")[-500:])
    wav = bytearray(p.stdout)
    i = 12
    while wav[i:i + 4] != b"data":
        size = struct.unpack_from("<I", wav, i + 4)[0]
        i += 8 + size + size % 2
    # ffmpeg cant seek back on a pipe so the sizes in the header are 0xffffffff
    wav[4:8] = struct.pack("<I", len(wav) - 8)
    wav[i + 4:i + 8] = struct.pack("<I", len(wav) - i - 8)
    return bytes(wav)


def from_wav(converter, wav, format, codec=None, bitrate=None, parameters=None, tags=None,
             id3v2_version="4", cover=None):
    codec = codec or {"ogg": "libvorbis"}.get(format)
    muxer = {"m4a": "ipod", "aac": "adts", "wma": "asf"}.get(format, format)
    args = ["-f", "wav", "-i", "pipe:0"]
    if cover is not None:
        cover = os.fspath(cover)
        if format != "mp3" or not cover.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
            raise AttributeError("cover images only work with mp3 and png, jpg, bmp or tif files")
        args += ["-i", cover, "-map", "0", "-map", "1", "-c:v", "mjpeg"]
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
    with tempfile.TemporaryDirectory() as d:
        name = os.path.join(d, "out")
        p = run(converter, args + ["-f", muxer, name], wav, format)
        if p.returncode != 0:
            raise CouldntEncodeError("cant encode this file: " + p.stderr.decode(errors="ignore")[-500:])
        with open(name, "rb") as f:
            return f.read()
