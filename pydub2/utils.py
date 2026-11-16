from __future__ import division

import json
import os
import re
import struct
import sys
import subprocess
from subprocess import Popen, PIPE
from math import log, ceil
from io import BytesIO
from tempfile import TemporaryFile
from warnings import warn
from functools import wraps

import _pydub2
import pydub2
from .exceptions import CouldntDecodeError

if sys.version_info >= (3, 0):
    basestring = str

NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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
    # Add .exe program extension for windows support
    if os.name == "nt" and not program.endswith(".exe"):
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


def check_duration(frames, rate):
    limit = pydub2.max_duration
    if limit is not None and frames > limit * rate:
        raise CouldntDecodeError("this file is longer than max_duration")


def run(command, data=None, **kwargs):
    try:
        return subprocess.run(command, input=data, capture_output=True, creationflags=NOWINDOW,
                              timeout=pydub2.ffmpeg_timeout, **kwargs)
    except subprocess.TimeoutExpired:
        raise TimeoutError("ffmpeg took longer than ffmpeg_timeout")


def source(filepath, read_ahead_limit=-1):
    """the input arguments for ffmpeg or ffprobe and the bytes for its stdin, if any"""
    try:
        path = fsdecode(filepath)
    except TypeError:
        filepath.seek(0)
        return ["-protocol_whitelist", "cache,pipe", "-read_ahead_limit", str(read_ahead_limit),
                "-i", "cache:pipe:0"], filepath.read()
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        head = b""
    # these name other files and a protocol whitelist cant stop that
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"ffconcat", b"#EXTM3U", b"<MPD", b"<?xml")):
        raise CouldntDecodeError("cant decode this file")
    return ["-protocol_whitelist", "file", "-i", "file:" + path], None


def _first(*names):
    for name in names:
        path = which(name)
        if path:
            return path


def get_encoder_name():
    """
    Return enconder default application for system, either avconv or ffmpeg
    """
    path = _first("ffmpeg", "avconv")
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
    path = _first("ffplay", "avplay")
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
    path = _first("ffprobe", "avprobe")
    if path:
        return path
    else:
        # should raise exception
        warn("Couldn't find ffprobe or avprobe - defaulting to ffprobe, but may not work", RuntimeWarning)
        return "ffprobe"


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
    prober = get_prober_name()
    command_args = ["-v", "info", "-show_format", "-show_streams"]
    args, data = source(filepath, read_ahead_limit)
    if data is not None and not os.path.basename(prober).startswith("ffprobe"):
        args = ["-"]
    command = [prober, '-of', 'json'] + command_args + args
    res = run(command, data)
    output = res.stdout.decode("utf-8", 'ignore')
    stderr = res.stderr.decode("utf-8", 'ignore')

    return json.loads(output), stderr

def _native_info(data):
    try:
        d = _pydub2.Decoder(data)
        frames = d.frames or len(d.read()) // (d.channels * d.width)
    except RuntimeError:
        return None
    rate, channels = d.rate, d.channels
    raw = "N/A"
    bits = "0"
    tag = 0
    if data[:4] in (b"RIFF", b"RF64"):
        i = data.find(b"fmt ")
        tag, _, _, _, _, bits = struct.unpack_from("<HHIIHH", data, i + 8)
        if tag == 0xFFFE:
            tag = struct.unpack_from("<H", data, i + 32)[0]
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
    elif data[:4] == b"fLaC":
        raw = str((((data[20] & 1) << 4) | (data[21] >> 4)) + 1)
        name, fmt, format_name = "flac", "s16" if int(raw) <= 16 else "s32", "flac"
    elif data[:4] == b"OggS":
        name = "opus" if b"OpusHead" in data[:200] else "vorbis"
        fmt, format_name = "fltp", "ogg"
    else:
        name, fmt, format_name = "mp3", "fltp", "mp3"
    stream = {
        "index": "0", "codec_name": name, "codec_type": "audio",
        "codec_tag_string": "[%d][0][0][0]" % tag, "codec_tag": "0x%04x" % tag,
        "sample_fmt": fmt, "sample_rate": str(rate), "channels": str(channels),
        "bits_per_sample": bits, "bits_per_raw_sample": raw,
        "duration": "%.6f" % (frames / rate),
    }
    return stream, {"nb_streams": "1", "format_name": format_name, "size": str(len(data))}


def _read_all(filepath):
    try:
        with open(fsdecode(filepath), "rb") as f:
            return f.read()
    except TypeError:
        filepath.seek(0)
        return filepath.read()
    except OSError:
        return None


def _has_prober():
    return which("ffprobe") or which("avprobe")


def mediainfo_json(filepath, read_ahead_limit=-1):
    """Return json dictionary with media info(codec, duration, size, bitrate...) from filepath
    """
    if _has_prober():
        info, stderr = _run_prober(filepath, read_ahead_limit)
        if not info:
            return info
        extra_info = get_extra_info(stderr)
    else:
        data = _read_all(filepath)
        if data is None:
            return {}
        native = _native_info(data)
        if native:
            stream, fmt = native
            for k in ("index", "channels", "bits_per_sample"):
                stream[k] = int(stream[k])
            raw = stream["bits_per_raw_sample"]
            info = {"streams": [{k: v for k, v in stream.items() if v != "N/A"}], "format": dict(fmt, nb_streams=1)}
            extra_info = {0: [stream["sample_fmt"] + (" (%s bit)" % raw if raw != "N/A" else "")]}
        else:
            args = source(BytesIO(data))[0]
            extra_info = get_extra_info(run([get_encoder_name()] + args, data).stderr.decode("utf-8", "ignore"))
            info = {"streams": [
                {"index": i, "codec_type": "audio", "codec_name": t[t.index("Audio") + 1].split()[0],
                 "bits_per_sample": 0} for i, t in extra_info.items() if "Audio" in t[:2]]}

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
    if not _has_prober():
        data = _read_all(filepath)
        native = data and _native_info(data)
        if native:
            return {**native[0], **native[1]}

    prober = get_prober_name()
    source_args, data = source(filepath)
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


@cache_codecs
def get_supported_codecs():
    encoder = get_encoder_name()
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

