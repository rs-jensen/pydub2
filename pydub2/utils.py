from __future__ import division

import glob
import json
import numbers
import os
import re
import signal
import struct
import sys
import subprocess
import zlib
from subprocess import Popen, PIPE
from math import log, ceil, inf
from io import BytesIO
from tempfile import TemporaryFile
from warnings import warn
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from itertools import islice
from bisect import bisect_right

import _pydub2
import pydub2
from .exceptions import CouldntDecodeError, tagged

if sys.version_info >= (3, 0):
    basestring = str

# with a console the child shares it and gets Ctrl-C, without one it would open its own window
NOWINDOW = 0
if os.name == "nt":
    import ctypes
    if not ctypes.windll.kernel32.GetConsoleWindow():
        NOWINDOW = subprocess.CREATE_NO_WINDOW

FRAME_WIDTHS = {
    8: 1,
    16: 2,
    32: 4,
}
ARRAY_TYPES = {
    8: "b",
    16: "h",
    32: "i",
}
ARRAY_RANGES = {
    8: (-0x80, 0x7f),
    16: (-0x8000, 0x7fff),
    32: (-0x80000000, 0x7fffffff),
}


def get_frame_width(bit_depth):
    return FRAME_WIDTHS[bit_depth]


def get_array_type(bit_depth, signed=True):
    t = ARRAY_TYPES[bit_depth]
    if not signed:
        t = t.upper()
    return t


def get_min_max_value(bit_depth):
    return ARRAY_RANGES[bit_depth]


def _fd_or_path_or_tempfile(fd, mode='w+b', tempfile=True):
    close_fd = False
    if fd is None and tempfile:
        fd = TemporaryFile(mode=mode)
        close_fd = True

    if isinstance(fd, basestring):
        fd = open(fd, mode=mode)
        close_fd = True

    try:
        if isinstance(fd, os.PathLike):
            fd = open(fd, mode=mode)
            close_fd = True
    except AttributeError:
        # module os has no attribute PathLike, so we're on python < 3.6.
        # The protocol we're trying to support doesn't exist, so just pass.
        pass

    return fd, close_fd


def db_to_float(db, using_amplitude=True):
    """
    Converts the input db to a float, which represents the equivalent
    ratio in power.
    """
    db = float(db)
    if using_amplitude:
        return 10 ** (db / 20)
    else:  # using power
        return 10 ** (db / 10)


def ratio_to_db(ratio, val2=None, using_amplitude=True):
    """
    Converts the input float to db, which represents the equivalent
    to the ratio in power represented by the multiplier passed in.
    """
    ratio = float(ratio)

    # accept 2 values and use the ratio of val1 to val2
    if val2 is not None:
        ratio = ratio / val2

    # special case for multiply-by-zero (convert to silence)
    if ratio == 0:
        return -float('inf')

    if using_amplitude:
        return 20 * log(ratio, 10)
    else:  # using power
        return 10 * log(ratio, 10)


def register_pydub_effect(fn, name=None):
    """
    decorator for adding pydub effects to the AudioSegment objects.
    example use:
        @register_pydub_effect
        def normalize(audio_segment):
            ...
    or you can specify a name:
        @register_pydub_effect("normalize")
        def normalize_audio_segment(audio_segment):
            ...
    """
    if isinstance(fn, basestring):
        name = fn
        return lambda fn: register_pydub_effect(fn, name)

    if name is None:
        name = fn.__name__

    from .audio_segment import AudioSegment
    setattr(AudioSegment, name, fn)
    return fn


def overrides(seg, *names):
    """True when seg is not a plain pydub2 AudioSegment as far as these names go, so pydub's own calls have to be made"""
    from .audio_segment import AudioSegment
    cls = type(seg)
    return not isinstance(seg, AudioSegment) or any(getattr(cls, n) is not getattr(AudioSegment, n) for n in names)


def make_chunks(audio_segment, chunk_length):
    """
    Breaks an AudioSegment into chunks that are <chunk_length> milliseconds
    long.
    if chunk_length is 50 then you'll get a list of 50 millisecond long audio
    segments back (except the last one, which can be shorter)
    """
    number_of_chunks = ceil(len(audio_segment) / float(chunk_length))
    return [audio_segment[i * chunk_length:(i + 1) * chunk_length]
            for i in range(int(number_of_chunks))]


def which(program):
    """
    Mimics behavior of UNIX which command. The current directory is not searched.
    """
    program = os.fspath(program)
    # Add .exe program extension for windows support
    if os.name == "nt" and not program.lower().endswith(".exe"):
        program += ".exe"

    # a name with a directory in it is an explicit choice
    if os.path.dirname(program):
        envdir_list = [""]
    else:
        envdir_list = [d for d in os.environ.get("PATH", os.defpath).split(os.pathsep) if d]

    for envdir in envdir_list:
        program_path = os.path.join(envdir, program)
        if os.path.isfile(program_path) and os.access(program_path, os.X_OK):
            return program_path


def extra_ffmpeg():
    # the binary of pip install "pydub2[ffmpeg]", looked at only when nothing is on PATH
    # imageio_ffmpeg.get_ffmpeg_exe would run a bare ffmpeg from the current folder on windows
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    exe = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if not exe:
        found = glob.glob(os.path.join(os.path.dirname(imageio_ffmpeg.__file__), "binaries", "ffmpeg-*"))
        exe = found[0] if found else None
    return which(exe) if exe and os.path.isabs(exe) else None


def check_rate(rate, what):
    # the core takes a 32 bit signed frame rate
    if isinstance(rate, numbers.Real):
        if rate >= 2 ** 31:
            raise RuntimeError("cant %s: the frame rate %s is too high" % (what, rate))
        # a rate like 44100.0 is fine for the core
        if float(rate).is_integer():
            return int(rate)
    return rate


def limits():
    # read once per load, so another thread that changes them cant cut a load short
    out = []
    for name, top in (("max_duration", 2 ** 31), ("max_bytes", 2 ** 62), ("max_file_size", 2 ** 62)):
        v = getattr(pydub2, name)
        if v is not None and not (isinstance(v, numbers.Real) and v >= 0):
            raise ValueError("pydub2.%s has to be None or a number, 0 or more, not %r" % (name, v))
        # more than ffmpeg or an index can take, like inf, is no limit
        out.append(None if v is None or v >= top else v)
    return tuple(out)


def check_duration(frames, rate, fw=None, lim=None):
    max_duration, max_bytes = (lim or limits())[:2]
    if max_duration is not None and frames > max_duration * rate:
        raise tagged(CouldntDecodeError("this file is longer than max_duration"), reason="too_long")
    if max_bytes is not None and fw is not None and frames * fw > max_bytes:
        raise tagged(CouldntDecodeError("this file is larger than max_bytes"), reason="too_big")


def frame_cap(rate, fw, lim):
    # one frame past the limits, so a file over them is seen and not just cut, -1 for no limit
    max_duration, max_bytes = lim[:2]
    n = inf
    if max_duration is not None:
        n = max_duration * rate
    if max_bytes is not None:
        n = min(n, max_bytes // fw)
    return -1 if n >= 2 ** 62 else int(n) + 1


def check_size(size, lim):
    if lim[2] is not None and size > lim[2]:
        raise tagged(CouldntDecodeError("this file is larger than max_file_size"), reason="too_big")


def file_size(f, start=None):
    # what is left to read from start, from the current position when it is None, and None for a pipe or a socket
    try:
        at = f.tell()
        try:
            return f.seek(0, 2) - (at if start is None else start)
        finally:
            f.seek(at)
    except (AttributeError, OSError, ValueError):
        return None


def read_file(f, lim, none_error=False):
    # a file can grow after its size was read, so with a limit the read stops one byte past it either way
    if lim[2] is None:
        # a non-blocking file gives None for no data, pydub then hands ffmpeg nothing
        return bytes(f.read() or b"")
    check_size(file_size(f) or 0, lim)
    return read_upto(f, lim, none_error=none_error)


def read_upto(f, lim, n=-1, none_error=False, keep_none=False):
    # read(n) sets aside n bytes first, so a cap far past the file is read in pieces
    if lim[2] is not None:
        top = int(lim[2]) + 1
        n = top if n < 0 else min(n, top)
    parts = []
    while n > 0:
        part = f.read(min(n, 1 << 24))
        if part is None and keep_none and not parts:
            # pydub keeps a None from its single read, and fails only when it is used
            return None
        if part is None and none_error and not parts:
            # what len() says of it in pydub
            raise TypeError("object of type 'NoneType' has no len()")
        if not part:
            break
        parts.append(part)
        n -= len(part)
    data = b"".join(parts)
    check_size(len(data), lim)
    return data


def read_capped(d, n, lim=None):
    # a header can lie about the length, so read one frame past the limits and look
    lim = lim or limits()
    fw = d.channels * d.width
    if n < 0 and d.exact:
        check_duration(d.frames, d.rate, fw, lim)
    cap = frame_cap(d.rate, fw, lim)
    if cap >= 0:
        n = cap if n < 0 else min(n, cap)
    pcm = d.read(n)
    check_duration(len(pcm) // fw, d.rate, fw, lim)
    return pcm


def timeout():
    t = pydub2.ffmpeg_timeout
    if t is not None and not (isinstance(t, numbers.Real) and t > 0):
        raise ValueError("pydub2.ffmpeg_timeout has to be None or a number more than 0, not %r" % (t,))
    return t


def run(command, data=None, **kwargs):
    t = timeout()
    try:
        if os.name != "posix":
            return subprocess.run(command, input=data, capture_output=True, creationflags=NOWINDOW,
                                  timeout=t, **kwargs)
        # a group of its own, so a timeout or Ctrl-C also stops what a wrapper script around ffmpeg started
        with Popen(command, stdin=None if data is None else PIPE, stdout=PIPE, stderr=PIPE, start_new_session=True,
                   **kwargs) as p:
            try:
                out, err = p.communicate(data, timeout=t)
            except BaseException:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise
        return subprocess.CompletedProcess(command, p.returncode, out, err)
    except subprocess.TimeoutExpired:
        raise TimeoutError("ffmpeg took longer than ffmpeg_timeout")


# demuxers that open other files or hosts named inside the file
PLAYLISTS = {"hls", "applehttp", "dash", "concat", "imf", "sdp", "rtsp", "rtp", "lavfi", "avisynth", "vapoursynth"}


@lru_cache(None)
def allowed_demuxers(exe):
    # a timeout or an empty list raises and is not remembered
    out = run([exe, "-hide_banner", "-demuxers"]).stdout.decode("utf-8", "ignore")
    # the third flag column is d for a device, those are left out
    rows = re.findall(r"^ D[ E]([ d]) *(\S+)", out.partition("\n --")[2], re.M)
    names = ",".join(n for flag, n in rows if flag != "d" and PLAYLISTS.isdisjoint(n.split(",")))
    if not names:
        raise CouldntDecodeError("cant list the demuxers of this ffmpeg, pydub2 needs ffmpeg 3.3 or newer")
    return names


def sniff(head):
    # from the first bytes only, None when nothing is recognised
    h = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if h.startswith((b"ffconcat", b"#EXTM3U", b"<MPD", b"<?xml")):
        return "playlist"
    if head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "opus" if b"OpusHead" in head[:200] else "ogg"
    if head[4:8] == b"ftyp":
        return "m4a" if head[8:12] == b"M4A " else "mp4"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[:4] == b"\x30\x26\xb2\x75":
        return "wma"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if head[:3] == b"ID3":
        return "mp3"
    if head[:1] == b"\xff" and len(head) > 1:
        if head[1] & 0xe0 == 0xe0 and head[1] & 6:
            return "mp3"
        if head[1] & 0xf6 == 0xf0:
            return "aac"


# starts that sniff does not know, only ffmpeg reads these
MAGIC = [(b"\x0b\x77", "ac3"), (b".snd", "au"), (b"caff", "caf"), (b"riff\x2e\x91\xcf\x11", "w64"), (b"wvpk", "wv"),
         (b"#!AMR", "amr"), (b"MAC ", "ape"), (b"TTA1", "tta"), (b"MPCK", "mpc"), (b"\x7f\xfe\x80\x01", "dts"),
         (b"FLV", "flv"), (b"Creative Voice File", "voc"), (b"MThd", "midi")]


def behind_id3(f, head):
    # an id3 tag can sit in front of any format
    at = 0
    while head[:3] == b"ID3" and len(head) >= 10:
        at += 10 + (head[6] << 21 | head[7] << 14 | head[8] << 7 | head[9]) + (10 if head[5] & 0x10 else 0)
        f.seek(at)
        head = f.read(4096)
    return at, head


def what(f, head):
    # the format by its first bytes, None when not known
    h = behind_id3(f, head)[1]
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "avi"
    fmt = sniff(h) or next((name for magic, name in MAGIC if h.startswith(magic)), None)
    if fmt == "mp3" and h[:1] == b"\xff":
        return {2: "mp3", 4: "mp2", 6: "mp1"}[h[1] & 6]
    if fmt == "webm" and b"webm" not in h[:64]:
        return "matroska"
    return fmt


def native_format(head):
    # the choice detect() in src/decode.cpp makes, all else goes to the mp3 decoder
    if head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS" and len(head) > 27:
        packet = head[27 + head[26]:]
        if packet.startswith(b"OpusHead"):
            return "opus"
        if packet.startswith(b"\x01vorbis"):
            return "ogg"
    return "mp3"


def chunks(f):
    # the chunks of a riff file with the start of each, a thousand at most like the decoder
    at = 12
    for _ in range(1000):
        f.seek(at)
        h = f.read(34)
        if len(h) < 8:
            return
        n = struct.unpack("<I", h[4:8])[0]
        yield at, h[:4], n, h[8:]
        at += 8 + n + (n & 1)


def damaged(f, head, fmt):
    # the built in decoder takes this file by its start and still failed, ffmpeg is no fix for that
    if fmt is None or native_format(head) != fmt:
        return False
    if fmt in ("ogg", "opus"):
        return not ogg_chained(f)
    if fmt != "wav":
        return True
    for _, cid, n, data in chunks(f):
        if cid == b"fmt ":
            # an extensible wav keeps its real tag further in
            tag = data[24:26] if data[:2] == b"\xfe\xff" else data[:2]
            # pcm and float are what pydub2 reads itself
            return len(tag) < 2 or struct.unpack("<H", tag)[0] in (1, 3)
    return True


def ogg_chained(f):
    # a second start page means chained streams, which the built in decoder leaves to ffmpeg
    at, first = 0, True
    size = f.seek(0, 2)
    while at + 27 <= size:
        f.seek(at)
        h = f.read(27)
        if h[:4] != b"OggS":
            return False
        if h[5] & 2 and not first:
            return True
        first = False
        at += 27 + h[26] + sum(f.read(h[26]))
    return False


def needs_ffmpeg(f, head):
    # what doctor says of a file the built in decoders refused: ffmpeg reads it, and it is not just damaged
    fmt = what(f, head)
    return fmt is not None and not damaged(f, head, fmt)


def source(filepath, read_ahead_limit=-1, exe=None):
    # sniffing the start of a file is not enough, so ffmpeg is also told which demuxers it may use
    allow = ["-format_whitelist", allowed_demuxers(exe)] if exe else []
    try:
        path = fsdecode(filepath)
    except TypeError:
        filepath.seek(0)
        return ["-protocol_whitelist", "cache,pipe"] + allow + ["-read_ahead_limit", str(read_ahead_limit),
                "-i", "cache:pipe:0"], read_file(filepath, limits())
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        head = b""
    if sniff(head) == "playlist":
        raise tagged(CouldntDecodeError("cant decode this file"), "playlist", "playlist_refused")
    return ["-protocol_whitelist", "file"] + allow + ["-i", path if pydub2.strict else "file:" + path], None


# the converter of the class a call was made on, the public lookups take no arguments like pydub's
_converter = ContextVar("converter", default=None)


@contextmanager
def _using(converter):
    token = _converter.set(converter)
    try:
        yield
    finally:
        _converter.reset(token)


def _current():
    from .audio_segment import AudioSegment
    from ._ffmpeg import name_of
    return name_of(_converter.get() or AudioSegment.converter)


def _first(names):
    for name in names:
        path = which(name)
        if path:
            return path
    # a private ffmpeg usually has its ffprobe and ffplay beside it
    folder = os.path.dirname(_current())
    for name in names:
        path = folder and which(os.path.join(folder, name))
        if path:
            return path


def get_encoder_name():
    """
    Return enconder default application for system, either avconv or ffmpeg
    """
    path = _first(("ffmpeg", "avconv")) or extra_ffmpeg()
    if path:
        return path
    else:
        # should raise exception
        warn("Couldn't find ffmpeg or avconv - defaulting to ffmpeg, but may not work", RuntimeWarning)
        return "ffmpeg"


def get_player_name():
    """
    Return enconder default application for system, either avconv or ffmpeg
    """
    path = _first(("ffplay", "avplay"))
    if path:
        return path
    else:
        # should raise exception
        warn("Couldn't find ffplay or avplay - defaulting to ffplay, but may not work", RuntimeWarning)
        return "ffplay"


def get_prober_name():
    """
    Return probe application, either avconv or ffmpeg
    """
    return _first(("ffprobe", "avprobe")) or "ffprobe"


def fsdecode(filename):
    """Wrapper for os.fsdecode which was introduced in python 3.2 ."""

    if sys.version_info >= (3, 2):
        PathLikeTypes = (basestring, bytes)
        if sys.version_info >= (3, 6):
            PathLikeTypes += (os.PathLike,)
        if isinstance(filename, PathLikeTypes):
            return os.fsdecode(filename)
    else:
        if isinstance(filename, bytes):
            return filename.decode(sys.getfilesystemencoding())
        if isinstance(filename, basestring):
            return filename

    raise TypeError("type {0} not accepted by fsdecode".format(type(filename)))


def get_extra_info(stderr):
    """
    avprobe sometimes gives more information on stderr than
    on the json output. The information has to be extracted
    from stderr of the format of:
    '    Stream #0:0: Audio: flac, 88200 Hz, stereo, s32 (24 bit)'
    or (macOS version):
    '    Stream #0:0: Audio: vorbis'
    '      44100 Hz, stereo, fltp, 320 kb/s'

    :type stderr: str
    :rtype: list of dict
    """
    extra_info = {}

    re_stream = r'^(?P<space_start> +)Stream #0[:\.](?P<stream_id>([0-9]+))(?P<content_0>.+)\n?(?! *Stream)((?P<space_end> +)(?P<content_1>.+))?'
    for i in re.finditer(re_stream, stderr, re.M):
        if i.group('space_end') is not None and len(i.group('space_start')) <= len(
                i.group('space_end')):
            content_line = ','.join([i.group('content_0'), i.group('content_1')])
        else:
            content_line = i.group('content_0')
        tokens = [x.strip() for x in re.split('[:,]', content_line) if x]
        extra_info[int(i.group('stream_id'))] = tokens
    return extra_info


def _run_prober(filepath, read_ahead_limit):
    prober = _has_prober()
    command_args = ["-v", "info", "-show_format", "-show_streams"]
    args, data = source(filepath, read_ahead_limit, prober)
    if data is not None and not os.path.basename(prober).lower().startswith("ffprobe"):
        args = ["-"]
    command = [prober, '-of', 'json'] + command_args + args
    res = run(command, data)
    output = res.stdout.decode("utf-8", 'ignore')
    stderr = res.stderr.decode("utf-8", 'ignore')

    return json.loads(output), stderr


# real files stay far under these, only a cover picture is big and that is skipped
LIMIT = 16 << 20
MAX_TAGS = 1 << 16
MAX_STEPS = 1 << 14
MAX_FRAMES = 4096


def dict_set(items, idx, k, v, join=False, keep=False):
    # av_dict_set: keys match without case, the old entry is swapped out for the last one and the new goes
    # at the end, an empty value deletes. idx finds an entry by its lowercase key
    i = idx.pop(k.lower(), None)
    if i is not None:
        was = items[i]
        last = items.pop()
        if i < len(items):
            items[i] = last
            idx[last[0].lower()] = i
        if join:
            # a bytearray grows in place, v2 + b";" + v would copy it every time
            old = was[1] if isinstance(was[1], bytearray) else bytearray(was[1])
            old += b";"
            old += v
            v = old
    if v or keep:
        idx[k.lower()] = len(items)
        items.append((k, v))


def _vorbis_comments(at, end):
    # pictures are no tags for ffprobe, values that do not fit in LIMIT are skipped
    out, idx, room = [], {}, LIMIT
    try:
        i = 4 + struct.unpack("<I", at(0, 4))[0]
        n, = struct.unpack("<I", at(i, 4))
        i += 4
        for _ in range(min(n, MAX_TAGS)):
            size, = struct.unpack("<I", at(i, 4))
            if i + 4 + size > end:
                break
            if size <= room:
                k, eq, v = at(i + 4, size).partition(b"=")
                if eq and k and v and k.upper() != b"METADATA_BLOCK_PICTURE":
                    room -= size
                    dict_set(out, idx, k, v, True)
            i += 4 + size
    except struct.error:
        pass
    return out


def _convert(items, names):
    out, idx = [], {}
    for k, v in items:
        dict_set(out, idx, names.get(k.lower(), k), v)
    return {k.decode(errors="replace"): v.decode(errors="replace") for k, v in out}


_vorbis_names = {b"albumartist": b"album_artist", b"tracknumber": b"track", b"discnumber": b"disc",
                 b"description": b"comment"}


def _flac_tags(at):
    i = 4
    for _ in range(MAX_STEPS):
        h = at(i, 4)
        if len(h) < 4:
            break
        size = int.from_bytes(h[1:], "big")
        if h[0] & 127 == 4:
            return _vorbis_comments(lambda a, n: at(i + 4 + a, n), size)
        if h[0] & 128:
            break
        i += 4 + size
    return []


def _ogg_tags(at, skip):
    # the second packet is the comment header, read page by page so a big cover is never held
    packet, spans, i, size = 0, [], 0, 0
    for _ in range(MAX_STEPS):
        h = at(i, 27)
        if h[:4] != b"OggS" or len(h) < 27:
            return []
        lacing = at(i + 27, h[26])
        body = i + 27 + h[26]
        i = body + sum(lacing)
        j = take = 0
        for v in lacing:
            if packet == 0:
                j += v
            elif packet == 1:
                take += v
            packet += v < 255
            if packet == 2:
                break
        if take:
            spans.append((size, body + j, take))
            size += take
        if packet == 2:
            starts = [x[0] for x in spans]

            def read(a, n):
                out, k, n = [], bisect_right(starts, a) - 1, min(n, size - a)
                while n > 0 and k < len(spans):
                    off, pos, length = spans[k]
                    part = at(pos + a - off, min(n, off + length - a))
                    if len(part) < min(n, off + length - a):
                        out.append(part)
                        break
                    out.append(part)
                    a, n, k = a + len(part), n - len(part), k + 1
                return b"".join(out)

            return _vorbis_comments(lambda a, n: read(skip + a, n), size - skip)
    return []


def _wav_chunks(at):
    i = 12
    for _ in range(MAX_STEPS):
        h = at(i, 8)
        if len(h) < 8:
            return
        size, = struct.unpack_from("<I", h, 4)
        yield h[:4], i + 8, size
        i += 8 + size + size % 2


def _wav_tags(at, spans):
    out, idx, n, room = [], {}, 0, LIMIT
    for i, end in spans:
        body = at(i, min(end - i, room))
        room -= len(body)
        j = 0
        while j + 8 <= len(body) and n < MAX_TAGS:
            n += 1
            size, = struct.unpack_from("<I", body, j + 4)
            dict_set(out, idx, body[j:j + 4], body[j + 8:j + 8 + size].split(b"\0")[0])
            j += 8 + size + size % 2
    return out


GENRES = (
    "Blues,Classic Rock,Country,Dance,Disco,Funk,Grunge,Hip-Hop,Jazz,Metal,New Age,Oldies,Other,Pop,R&B,"
    "Rap,Reggae,Rock,Techno,Industrial,Alternative,Ska,Death Metal,Pranks,Soundtrack,Euro-Techno,Ambient,"
    "Trip-Hop,Vocal,Jazz+Funk,Fusion,Trance,Classical,Instrumental,Acid,House,Game,Sound Clip,Gospel,"
    "Noise,AlternRock,Bass,Soul,Punk,Space,Meditative,Instrumental Pop,Instrumental Rock,Ethnic,Gothic,"
    "Darkwave,Techno-Industrial,Electronic,Pop-Folk,Eurodance,Dream,Southern Rock,Comedy,Cult,Gangsta,"
    "Top 40,Christian Rap,Pop/Funk,Jungle,Native American,Cabaret,New Wave,Psychedelic,Rave,Showtunes,"
    "Trailer,Lo-Fi,Tribal,Acid Punk,Acid Jazz,Polka,Retro,Musical,Rock & Roll,Hard Rock,Folk,Folk-Rock,"
    "National Folk,Swing,Fast Fusion,Bebop,Latin,Revival,Celtic,Bluegrass,Avantgarde,Gothic Rock,"
    "Progressive Rock,Psychedelic Rock,Symphonic Rock,Slow Rock,Big Band,Chorus,Easy Listening,Acoustic,"
    "Humour,Speech,Chanson,Opera,Chamber Music,Sonata,Symphony,Booty Bass,Primus,Porn Groove,Satire,"
    "Slow Jam,Club,Tango,Samba,Folklore,Ballad,Power Ballad,Rhythmic Soul,Freestyle,Duet,Punk Rock,"
    "Drum Solo,A Cappella,Euro-House,Dance Hall,Goa,Drum & Bass,Club-House,Hardcore Techno,Terror,Indie,"
    "BritPop,Negerpunk,Polsk Punk,Beat,Christian Gangsta Rap,Heavy Metal,Black Metal,Crossover,"
    "Contemporary Christian,Christian Rock,Merengue,Salsa,Thrash Metal,Anime,Jpop,Synthpop,Abstract,"
    "Art Rock,Baroque,Bhangra,Big Beat,Breakbeat,Chillout,Downtempo,Dub,EBM,Eclectic,Electro,"
    "Electroclash,Emo,Experimental,Garage,Global,IDM,Illbient,Industro-Goth,Jam Band,Krautrock,Leftfield,"
    "Lounge,Math Rock,New Romantic,Nu-Breakz,Post-Punk,Post-Rock,Psytrance,Shoegaze,Space Rock,Trop Rock,"
    "World Music,Neoclassical,Audiobook,Audio Theatre,Neue Deutsche Welle,Podcast,Indie Rock,G-Funk,"
    "Dubstep,Garage Rock,Psybient"
).split(",")

_id3_names = [dict(x.split(b"=") for x in t.split()) for t in (
    b"talb=album tcom=composer tcon=genre tcop=copyright tenc=encoded_by tit2=title tlan=language tpe1=artist "
    b"tpe2=album_artist tpe3=performer tpos=disc tpub=publisher trck=track tsse=encoder uslt=lyrics",
    b"tal=album tco=genre tcp=compilation tt2=title ten=encoded_by tp1=artist tp2=album_artist tp3=performer "
    b"trk=track",
    b"tcmp=compilation tdrc=date tdrl=date tden=creation_time tsoa=album-sort tsop=artist-sort tsot=title-sort "
    b"tit1=grouping")]


def _syncsafe(b):
    return sum((x & 127) << 7 * (3 - i) for i, x in enumerate(b))


def _put(items, idx, k, v):
    # like av_dict_set with DONT_OVERWRITE, the first one stays
    if k.lower() not in idx:
        idx[k.lower()] = len(items)
        items.append((k, v))


def _id3_str(d, i, enc):
    # like decode_str in ffmpeg: utf-8 bytes and the index after the end, None when ffmpeg gives up
    if enc in (0, 3):
        j = d.find(b"\0", i)
        e = len(d) if j < 0 else j
        s = d[i:e]
        return (s.decode("latin-1").encode() if enc == 0 else s), min(e + 1, len(d))
    if enc not in (1, 2):
        return b"", i
    codec = "utf-16-be"
    if enc == 1:
        # ffmpeg 8.1 takes an empty string with no bom, 7 and 8.0 give up
        if d[i:i + 2] == b"\0\0":
            return b"", min(i + 2, len(d))
        if d[i:i + 2] not in (b"\xff\xfe", b"\xfe\xff"):
            return None, i + 2
        codec = "utf-16-le" if d[i] == 255 else codec
        i += 2
    j = d.find(b"\0\0", i)
    while j >= 0 and (j - i) % 2:
        j = d.find(b"\0\0", j + 1)
    e = len(d) if j < 0 else j
    s = d[i:e - (e - i) % 2]
    try:
        s.decode(codec)
    except UnicodeDecodeError as x:
        s = s[:x.start]
    return s.decode(codec).encode(), min(e + 2, len(d))


def _id3_frame(tag, d, items, idx, priv):
    if tag[:1] == b"T":
        s, i = _id3_str(d, 1, d[0])
        if s is None:
            return
        key = tag.split(b"\0")[0]
        g = re.match(rb"\(?\s*\+?0*(\d{1,6})", s)
        if key in (b"TCON", b"TCO") and g and int(g[1]) < 192:
            s = GENRES[int(g[1])].encode()
        elif key in (b"TXXX", b"TXX"):
            key = s
            s, i = _id3_str(d, i, d[0])
            if s is None:
                return
        elif not s:
            return
        _put(items, idx, key, s)
    elif tag in (b"COM", b"COMM", b"USLT") and len(d) > 3:
        k, i = _id3_str(d, 4, d[0])
        v, i = _id3_str(d, i, d[0]) if k is not None else (None, i)
        if v is None:
            return
        if tag == b"USLT":
            dict_set(items, idx, b"lyrics-" + (k + b"-" if k else b"") + d[1:4].split(b"\0")[0], v, keep=True)
        else:
            _put(items, idx, k or b"comment", v)
    elif tag == b"PRIV":
        k, i = _id3_str(d, 0, 0)
        # unicode_escape writes \n, \t and \\ where ffmpeg writes \x0a, \x09 and \x5c
        v = d[i:].decode("latin-1").encode("unicode_escape")
        for a, b in ((b"\\\\", b"\\x5c"), (b"\\n", b"\\x0a"), (b"\\r", b"\\x0d"), (b"\\t", b"\\x09")):
            v = v.replace(a, b)
        priv.append((b"id3v2_priv." + k, v))


def _is_tag(b):
    return re.fullmatch(rb"[A-Z0-9]{4}|\0{4}", b) is not None


def _inflate(d, dlen):
    # the data and what it cost, a stream that fails or stops short still costs what it inflated (all of dlen on an error)
    if not d or dlen <= 0 or dlen // 32768 > len(d):
        return b"", 0
    z = zlib.decompressobj()
    try:
        out = z.decompress(d, dlen)
    except zlib.error:
        return b"", dlen
    return (out if z.eof else b""), len(out)


def _id3v2_frames(f, end):
    # (frame id, data) of the frames of all id3v2 tags at the start, one tag after the other like ffmpeg does
    # a frame it cant use gets empty data, so it still counts for the cap
    pos, room = 0, LIMIT
    for _ in range(MAX_STEPS):
        end[0] = pos
        f.seek(pos)
        h = f.read(10)
        if len(h) < 10 or h[:3] != b"ID3" or 255 in h[3:5] or any(x & 128 for x in h[6:10]):
            return
        ver, flags = h[3], h[5]
        size = _syncsafe(h[6:10])
        pos += 10 + size
        b = f.read(min(size, room))
        room -= len(b)
        w, i = (6, 0) if ver == 2 else (10, 0)
        if ver not in (2, 3, 4) or ver == 2 and flags & 64:
            continue
        if ver > 2 and flags & 64:
            i = _syncsafe(b[:4]) + (4 if ver == 3 else 0)
            if i > len(b) or ver == 4 and i < 4:
                continue
        if ver == 4 and flags & 16:
            pos += 10
        while len(b) - i >= w:
            if ver == 2:
                tag, n, fl = b[i:i + 3], int.from_bytes(b[i + 3:i + 6], "big"), 0
            else:
                tag, n, fl = b[i:i + 4], int.from_bytes(b[i + 4:i + 8], "big"), int.from_bytes(b[i + 8:i + 10], "big")
                if ver == 4 and n > 127:
                    s = _syncsafe(b[i + 4:i + 8])
                    if n >= len(b) - i or _is_tag(b[i + 10 + s:i + 14 + s]):
                        n = s
                    elif not _is_tag(b[i + 10 + n:i + 14 + n]):
                        break
            if n > 1 << 28 or i + w + n > len(b):
                break
            d = b[i + w:i + w + n]
            i += w + n
            dlen = n
            if n and fl & 1:
                if n < 4:
                    break
                dlen, d = int.from_bytes(d[:4], "big"), d[4:]
            if fl & 4:
                d = b""
            elif n and not tag[0]:
                break
            else:
                if fl & 2 or flags & 128:
                    d = d.replace(b"\xff\x00", b"\xff")
                if fl & 8:
                    d, cost = _inflate(d, min(dlen, room))
                    room -= cost
            yield tag, d


def _id3v1(f, size):
    if size <= 128:
        return []
    f.seek(size - 128)
    b = f.read(128)
    if b[:3] != b"TAG":
        return []
    out = []
    for k, i, n in ((b"title", 3, 30), (b"artist", 33, 30), (b"album", 63, 30), (b"date", 93, 4), (b"comment", 97, 30)):
        s = b[i:i + n].split(b"\0")[0].rstrip(b" ")
        if s:
            out.append((k, s))
    if b[125] == 0 and b[126]:
        out.append((b"track", b"%d" % b[126]))
    if b[127] < 192:
        out.append((b"genre", GENRES[b[127]].encode()))
    return out


def _date_part(items, idx, *keys):
    for k in keys:
        i = idx.get(k.lower())
        if i is not None and items[i][0] == k and len(items[i][1]) == 4 and items[i][1].isdigit():
            v = items[i][1]
            for k in keys:
                dict_set(items, idx, k, b"")
            return v


def _xing_encoder(f, start):
    # ffmpeg only trusts the encoder name in the Xing or Info frame when the crc at its end is right
    f.seek(start)
    b = f.read(200)
    if len(b) < 4 or b[0] != 255 or b[1] & 0xe0 != 0xe0:
        return None
    version, layer, bitrate, rate = b[1] >> 3 & 3, b[1] >> 1 & 3, b[2] >> 4, b[2] >> 2 & 3
    if version == 1 or layer != 1 or bitrate == 15 or rate == 3:
        return None
    off = 4 + (32, 17, 17, 9)[2 * (version != 3) + (b[3] >> 6 == 3)]
    if b[off:off + 4] not in (b"Xing", b"Info"):
        return None
    flags = int.from_bytes(b[off + 4:off + 8], "big")
    p = off + 8 + 4 * (flags & 1) + 4 * (flags >> 1 & 1) + 100 * (flags >> 2 & 1) + 4 * (flags >> 3 & 1)
    crc = 0
    for x in b[:p + 34]:
        crc ^= x
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    if len(b) >= p + 36 and crc == int.from_bytes(b[p + 34:p + 36], "big"):
        return b[p:p + 9].split(b"\0")[0].decode(errors="replace")


def _mp3_tags(f, size):
    items, idx, priv, end = [], {}, [], [0]
    for tag, d in islice(_id3v2_frames(f, end), MAX_FRAMES):
        if d:
            _id3_frame(tag, d, items, idx, priv)
    for names in _id3_names:
        new, idx = [], {}
        for k, v in items:
            dict_set(new, idx, names.get(k.lower(), k), v, keep=True)
        items = new
    date = _date_part(items, idx, b"TYER", b"TYE")
    if date:
        day = _date_part(items, idx, b"TDAT", b"TDA")
        if day:
            date += b"-" + day[2:] + b"-" + day[:2]
            hm = _date_part(items, idx, b"TIME", b"TIM")
            if hm:
                date += b" " + hm[:2] + b":" + hm[2:]
        dict_set(items, idx, b"date", date)
    if not items:
        for k, v in _id3v1(f, size):
            _put(items, idx, k, v)
    for k, v in priv:
        _put(items, idx, k, v)
    return {k.decode(errors="replace"): v.decode(errors="replace") for k, v in items}, _xing_encoder(f, end[0])


def _native_info(f, source):
    try:
        d = _pydub2.Decoder(source)
        frames = d.frames or len(read_capped(d, -1)) // (d.channels * d.width)
    except RuntimeError:
        return None
    rate, channels = d.rate, d.channels
    f.seek(0, 2)
    size = f.tell()

    def at(i, n):
        f.seek(start + i)
        return f.read(n)

    start = 0
    if at(0, 3) == b"ID3":
        start = 10 + sum((x & 127) << 7 * (3 - i) for i, x in enumerate(at(6, 4)))
    raw = "N/A"
    bits = "0"
    tag = 0
    tags, in_stream, enc = {}, False, None
    head = at(0, 22)
    if head[:4] in (b"RIFF", b"RF64"):
        chunk, lists = None, []
        for cid, i, n in _wav_chunks(at):
            if cid == b"fmt " and chunk is None:
                chunk = at(i, 40).ljust(40, b"\0")
            elif cid == b"LIST" and at(i, 4) == b"INFO":
                lists.append((i + 4, i + n))
        if chunk is None:
            return None
        tag, _, _, _, _, bits = struct.unpack_from("<HHIIHH", chunk)
        if tag == 0xFFFE:
            tag = struct.unpack_from("<H", chunk, 24)[0]
        if tag == 3:
            name, fmt = "pcm_f%dle" % bits, "flt" if bits == 32 else "dbl"
        elif bits == 8:
            name, fmt = "pcm_u8", "u8"
        else:
            name, fmt = "pcm_s%dle" % bits, "s16" if bits == 16 else "s32"
        format_name = "wav"
        if tag == 1 and bits in (24, 32):
            raw = str(bits)
        bits = str(bits)
        tags = _convert(_wav_tags(at, lists), {
            b"iart": b"artist", b"icmt": b"comment", b"icop": b"copyright", b"icrd": b"date", b"ignr": b"genre",
            b"ilng": b"language", b"inam": b"title", b"iprd": b"album", b"iprt": b"track", b"isft": b"encoder",
            b"ismp": b"timecode", b"itch": b"encoded_by"})
    elif head[:4] == b"fLaC":
        raw = str((((head[20] & 1) << 4) | (head[21] >> 4)) + 1)
        name, fmt, format_name = "flac", "s16" if int(raw) <= 16 else "s32", "flac"
        tags = _convert(_flac_tags(at), _vorbis_names)
    elif head[:4] == b"OggS":
        name = "opus" if b"OpusHead" in at(0, 200) else "vorbis"
        fmt, format_name = "fltp", "ogg"
        tags = _convert(_ogg_tags(at, 8 if name == "opus" else 7), _vorbis_names)
        in_stream = True
    else:
        name, fmt, format_name = "mp3", "fltp", "mp3"
        tags, enc = _mp3_tags(f, size)
    stream = {
        "index": "0", "codec_name": name, "codec_type": "audio",
        "codec_tag_string": "[%d][0][0][0]" % tag, "codec_tag": "0x%04x" % tag,
        "sample_fmt": fmt, "sample_rate": str(rate), "channels": str(channels),
        "bits_per_sample": bits, "bits_per_raw_sample": raw,
        "duration": "%.6f" % (frames / rate),
    }
    if enc is not None:
        stream["tags"] = {"encoder": enc}
    fmt = {"nb_streams": "1", "format_name": format_name, "size": str(size)}
    if tags:
        (stream if in_stream else fmt)["tags"] = tags
    return stream, fmt


def _open(filepath):
    try:
        path = fsdecode(filepath)
    except TypeError:
        filepath.seek(0)
        data = read_file(filepath, limits())
        return BytesIO(data), data
    try:
        return open(path, "rb"), path
    except OSError:
        return None, None


def _has_prober():
    path = which(get_prober_name())
    if path is None and pydub2.strict:
        raise tagged(FileNotFoundError("%s not found, pydub2 needs it for this. strict mode needs ffmpeg" % get_prober_name()),
                     reason="needs_ffmpeg", hint="install ffmpeg and ffprobe")
    return path


def _pcm_bits(name):
    m = re.match(r"pcm_[suf](\d+)", name)
    return int(m[1]) if m else 8 if name in ("pcm_mulaw", "pcm_alaw") else 4 if name.startswith("adpcm_") else 0


def mediainfo_json(filepath, read_ahead_limit=-1):
    """Return json dictionary with media info(codec, duration, size, bitrate...) from filepath
    """
    if _has_prober():
        info, stderr = _run_prober(filepath, read_ahead_limit)
        if not info:
            return info
        extra_info = get_extra_info(stderr)
    else:
        f, src = _open(filepath)
        if f is None:
            return {}
        with f:
            head = f.read(4096)
            native = _native_info(f, src)
        from . import _ffmpeg
        exe = _current()
        if native:
            stream, fmt = native
            for k in ("index", "channels", "bits_per_sample"):
                stream[k] = int(stream[k])
            raw = stream["bits_per_raw_sample"]
            info = {"streams": [{k: v for k, v in stream.items() if v != "N/A"}], "format": dict(fmt, nb_streams=1)}
            extra_info = {0: [stream["sample_fmt"] + (" (%s bit)" % raw if raw != "N/A" else "")]}
        # refused by the reader means damaged, ffprobe gives {} too, but an ID3 tag can sit in front of anything
        elif _ffmpeg.find(exe) is None and (sniff(head) in ("wav", "flac", "mp3", "ogg", "opus") and head[:3] != b"ID3"
                                             or isinstance(src, str)
                                             and src.lower().endswith((".wav", ".flac", ".mp3", ".ogg", ".opus"))):
            return {}
        else:
            args, data = source(filepath, exe=_ffmpeg.find(exe))
            extra_info = get_extra_info(_ffmpeg.run(exe, args, data, "this file").stderr.decode("utf-8", "ignore"))
            info = {"streams": []}
            for i, t in extra_info.items():
                if "Audio" in t[:2]:
                    name = t[t.index("Audio") + 1].split()[0]
                    info["streams"].append({"index": i, "codec_type": "audio", "codec_name": name,
                                            "bits_per_sample": _pcm_bits(name)})

    audio_streams = [x for x in info.get('streams', []) if x.get('codec_type') == 'audio']
    if len(audio_streams) == 0:
        return info

    # We just operate on the first audio stream in case there are more
    stream = audio_streams[0]

    def set_property(stream, prop, value):
        if prop not in stream or stream[prop] == 0:
            stream[prop] = value

    for token in extra_info.get(stream['index'], []):
        m = re.match(r'([su]([0-9]{1,2})p?) \(([0-9]{1,2}) bit\)$', token)
        m2 = re.match(r'([su]([0-9]{1,2})p?)( \(default\))?$', token)
        if m:
            set_property(stream, 'sample_fmt', m.group(1))
            set_property(stream, 'bits_per_sample', int(m.group(2)))
            set_property(stream, 'bits_per_raw_sample', int(m.group(3)))
        elif m2:
            set_property(stream, 'sample_fmt', m2.group(1))
            set_property(stream, 'bits_per_sample', int(m2.group(2)))
            set_property(stream, 'bits_per_raw_sample', int(m2.group(2)))
        elif re.match(r'(flt)p?( \(default\))?$', token):
            set_property(stream, 'sample_fmt', token)
            set_property(stream, 'bits_per_sample', 32)
            set_property(stream, 'bits_per_raw_sample', 32)
        elif re.match(r'(dbl)p?( \(default\))?$', token):
            set_property(stream, 'sample_fmt', token)
            set_property(stream, 'bits_per_sample', 64)
            set_property(stream, 'bits_per_raw_sample', 64)
    return info


def mediainfo(filepath):
    """Return dictionary with media info(codec, duration, size, bitrate...) from filepath
    """
    if not hasattr(filepath, "read"):
        os.fsdecode(filepath)
    if not _has_prober():
        f, src = _open(filepath)
        native = None
        if f is not None:
            with f:
                native = _native_info(f, src)
        if native:
            stream, fmt = native
            tags = {**stream.pop("tags", {}), **fmt.pop("tags", {})}
            return {**stream, **fmt, **({"TAG": tags} if tags else {})}
        streams = mediainfo_json(filepath).get("streams", [])
        return {k: str(v) for k, v in streams[0].items()} if streams else {}

    prober = _has_prober()
    source_args, data = source(filepath, exe=prober)
    command_args = ["-v", "quiet", "-show_format", "-show_streams"] + source_args

    command = [prober, '-of', 'old'] + command_args
    res = run(command, data)
    output = res.stdout.decode("utf-8", "ignore")

    if res.returncode != 0:
        command = [prober] + command_args
        output = run(command, data).stdout.decode("utf-8", "ignore")

    rgx = re.compile(r"(?:(?P<inner_dict>.*?):)?(?P<key>.*?)\=(?P<value>.*?)$")
    info = {}

    if sys.platform == 'win32':
        output = output.replace("\r", "")

    for line in output.split("\n"):
        mobj = rgx.match(line)

        if mobj:
            inner_dict, key, value = mobj.groups()

            if inner_dict:
                try:
                    info[inner_dict]
                except KeyError:
                    info[inner_dict] = {}
                info[inner_dict][key] = value
            else:
                info[key] = value

    return info


def cache_codecs(function):
    cache = {}

    @wraps(function)
    def wrapper():
        try:
            return cache[0]
        except:
            cache[0] = function()
            return cache[0]

    return wrapper


def get_supported_codecs():
    return _codecs(_current())


@lru_cache(None)
def _codecs(converter):
    from . import _ffmpeg
    encoder = _ffmpeg.find(converter) or which(get_encoder_name())
    if not encoder:
        return set(), set()
    command = [encoder, "-codecs"]
    try:
        res = run(command)
    except OSError:
        return set(), set()
    output = res.stdout.decode("utf-8", "ignore")
    if res.returncode != 0:
        return set(), set()

    if sys.platform == 'win32':
        output = output.replace("\r", "")


    rgx = re.compile(r"^([D.][E.][AVS.][I.][L.][S.]) (\w*) +(.*)")
    decoders = set()
    encoders = set()
    for line in output.split('\n'):
        match = rgx.match(line.strip())
        if not match:
            continue
        flags, codec, name = match.groups()

        if flags[0] == 'D':
            decoders.add(codec)

        if flags[1] == 'E':
            encoders.add(codec)

    return (decoders, encoders)


def get_supported_decoders():
    return get_supported_codecs()[0]


def get_supported_encoders():
    return get_supported_codecs()[1]


def stereo_to_ms(audio_segment):
    '''
    Left-Right -> Mid-Side
    '''
    from .audio_segment import AudioSegment
    channel = audio_segment.split_to_mono()
    channel = [channel[0].overlay(channel[1]), channel[0].overlay(channel[1].invert_phase())]
    return AudioSegment.from_mono_audiosegments(channel[0], channel[1])


def ms_to_stereo(audio_segment):
    '''
    Mid-Side -> Left-Right
    '''
    from .audio_segment import AudioSegment
    channel = audio_segment.split_to_mono()
    channel = [channel[0].overlay(channel[1]) - 3, channel[0].overlay(channel[1].invert_phase()) - 3]
    return AudioSegment.from_mono_audiosegments(channel[0], channel[1])

