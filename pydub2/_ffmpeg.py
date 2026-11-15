import os
import shutil
import struct
import subprocess
import sys
import tempfile

from .exceptions import CouldntDecodeError, CouldntEncodeError


def find(converter):
    for name in (converter, "avconv"):
        path = shutil.which(name)
        if path:
            return path


def run(converter, args, data, what):
    exe = find(converter)
    if exe is None:
        raise FileNotFoundError("ffmpeg not found, pydub2 needs it for " + what)
    return subprocess.run([exe, "-y", "-nostdin"] + args,
                          input=data, capture_output=True)


def to_wav(converter, path=None, data=None, format=None, codec=None, parameters=None,
           start_second=None, duration=None):
    format = {"m4a": "mp4", "wave": "wav"}.get(format, format)
    opts = (["-f", format] if format else []) + (["-acodec", codec] if codec else [])
    out = ["-vn", "-f", "wav"]
    if start_second is not None:
        out += ["-ss", str(start_second)]
    if duration is not None:
        out += ["-t", str(duration)]
    out += (parameters or []) + ["-"]
    if path is not None:
        p = run(converter, opts + ["-i", path] + out, None, format or "this file")
    else:
        p = run(converter, opts + ["-i", "pipe:0"] + out, data, format or "this file")
        if p.returncode != 0 or not p.stdout:
            # mp4 with moov at the end cant be read from a pipe
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(data)
            try:
                p = run(converter, opts + ["-i", f.name] + out, None, format or "this file")
            finally:
                os.unlink(f.name)
    if p.returncode != 0 or not p.stdout:
        raise CouldntDecodeError("cant decode this file: " + p.stderr.decode(errors="ignore")[-500:])
    wav = bytearray(p.stdout)
    i = wav.find(b"data")
    # ffmpeg cant seek back on a pipe so the sizes in the header are 0xffffffff
    wav[4:8] = struct.pack("<I", len(wav) - 8)
    wav[i + 4:i + 8] = struct.pack("<I", len(wav) - i - 8)
    return bytes(wav)


def from_wav(converter, wav, format, codec=None, bitrate=None, parameters=None, tags=None,
             id3v2_version="4", cover=None):
    codec = codec or {"ogg": "libvorbis"}.get(format)
    args = ["-f", "wav", "-i", "pipe:0"]
    if cover is not None:
        if format != "mp3" or not cover.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
            raise AttributeError("cover images only work with mp3 and png, jpg, bmp or tif files")
        args += ["-i", cover, "-map", "0", "-map", "1", "-c:v", "mjpeg"]
    if codec:
        args += ["-acodec", codec]
    if bitrate:
        args += ["-b:a", bitrate]
    args += parameters or []
    for k, v in (tags or {}).items():
        args += ["-metadata", "%s=%s" % (k, v)]
    if tags and format == "mp3":
        args += ["-id3v2_version", id3v2_version]
    if codec == "mp3" and sys.platform == "darwin":
        args += ["-write_xing", "0"]
    with tempfile.TemporaryDirectory() as d:
        name = os.path.join(d, "out")
        p = run(converter, args + ["-f", format, name], wav, format)
        if p.returncode != 0:
            raise CouldntEncodeError("cant encode this file: " + p.stderr.decode(errors="ignore")[-500:])
        with open(name, "rb") as f:
            return f.read()
