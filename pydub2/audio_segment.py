from __future__ import division

import array
import os
from tempfile import TemporaryFile, NamedTemporaryFile
import subprocess
import sys
import struct
import wave
from .utils import dict_set, fsdecode, get_encoder_name, mediainfo_json
from .logging_utils import log_conversion, log_subprocess_output
import base64
from collections import namedtuple

try:
    from StringIO import StringIO
except:
    from io import StringIO

from io import BytesIO

try:
    from itertools import izip
except:
    izip = zip

from .utils import (
    _fd_or_path_or_tempfile,
    db_to_float,
    ratio_to_db,
    get_array_type,
    check_duration,
    read_capped,
)
from .exceptions import (
    TooManyMissingFrames,
    InvalidDuration,
    InvalidID3TagVersion,
    InvalidTag,
    CouldntDecodeError,
    CouldntEncodeError,
    MissingAudioParameter,
)
from . import _ffmpeg
from . import _mp3
import _pydub2
import pydub2

if sys.version_info >= (3, 0):
    basestring = str
    xrange = range
    StringIO = BytesIO


def _encode(data, rate, *args):
    try:
        # the wave module in pydub rounds the rate
        return _pydub2.encode(data, round(rate), *args)
    except RuntimeError as e:
        raise CouldntEncodeError(str(e))


def _vorbis_tags(tags, format):
    # what ffmpeg makes of -metadata, the muxer renames four keys
    if not isinstance(tags, dict):
        raise InvalidTag("Tags must be a dictionary.")
    out, idx = [], {}
    for k, v in tags.items():
        arg = ("%s=%s" % (k, v)).encode("utf-8", "surrogateescape")
        if b"\0" in arg:
            raise ValueError("embedded null byte")
        k, _, v = arg.partition(b"=")
        if k:
            dict_set(out, idx, k, v)
    names = {b"album_artist": b"ALBUMARTIST", b"track": b"TRACKNUMBER", b"disc": b"DISCNUMBER",
             b"comment": b"DESCRIPTION"}
    renamed, out, idx = out, [], {}
    for k, v in renamed:
        dict_set(out, idx, names.get(k.lower(), k), v)
    dict_set(out, idx, b"encoder", b"")
    # flac has it last, ogg and opus first
    return out + [(b"encoder", b"pydub2")] if format == "flac" else [(b"encoder", b"pydub2")] + out


def _export_pcm(seg, format, codec, parameters):
    # only what ffmpeg gives back bit for bit, resampling and channel mixing stay with ffmpeg
    p = parameters or []
    if len(p) % 2:
        return None
    fmt = None
    for k, v in zip(p[::2], p[1::2]):
        if k == "-ar" and v == str(seg.frame_rate) or k == "-ac" and v == str(seg.channels):
            pass
        elif k == "-sample_fmt" and v in ("s16", "s32"):
            fmt = v
        else:
            return None
    if format == "flac":
        # flac keeps 16 bit or goes to 24, a pcm codec cant be put in it
        if codec not in (None, "flac") or seg.sample_width not in (2, 4):
            return None
        width = 2 if (fmt or {2: "s16", 4: "s32"}[seg.sample_width]) == "s16" else 3
    else:
        width = {None: 2, "pcm_s16le": 2, "pcm_s24le": 3, "pcm_s32le": 4}.get(codec, 0)
        if not width or fmt and fmt != {2: "s16", 3: "s32", 4: "s32"}[width]:
            return None
    seg = seg.set_sample_width(4 if width == 3 else width)
    if format == "flac" and width == 3:
        return _encode(_pydub2.lin2lin(seg._data, 4, 3), seg.frame_rate, seg.channels, 3, format, 0)
    if width != 3:
        return _encode(seg._data, seg.frame_rate, seg.channels, width, format, 0)
    n = len(seg._data) // 4 * 3
    pcm = bytearray(n)
    for i in range(3):
        pcm[i::3] = seg._data[i + 1::4]
    rate, channels = round(seg.frame_rate), seg.channels
    try:
        head = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + n + n % 2, b"WAVE", b"fmt ", 16, 1, channels, rate,
                           rate * channels * 3, channels * 3, 24, b"data", n)
    except struct.error:
        raise CouldntEncodeError("cant encode this")
    return head + bytes(pcm) + b"\0" * (n % 2)


class ClassPropertyDescriptor(object):

    def __init__(self, fget, fset=None):
        self.fget = fget
        self.fset = fset

    def __get__(self, obj, klass=None):
        if klass is None:
            klass = type(obj)
        return self.fget.__get__(obj, klass)()

    def __set__(self, obj, value):
        if not self.fset:
            raise AttributeError("can't set attribute")
        type_ = type(obj)
        return self.fset.__get__(obj, type_)(value)

    def setter(self, func):
        if not isinstance(func, (classmethod, staticmethod)):
            func = classmethod(func)
        self.fset = func
        return self


def classproperty(func):
    if not isinstance(func, (classmethod, staticmethod)):
        func = classmethod(func)

    return ClassPropertyDescriptor(func)


def wave_pads_odd_data():
    buf = BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(1)
    w.setframerate(8000)
    w.writeframes(b"\0")
    w.close()
    return len(buf.getvalue()) % 2 == 0


# the wave module pydub writes with counts the pad byte of odd data from python 3.15 on, the core always does
WAVE_PADS = wave_pads_odd_data()


def riff_without_pad(wav):
    return wav[:4] + (int.from_bytes(wav[4:8], "little") - 1).to_bytes(4, "little") + wav[8:]


AUDIO_FILE_EXT_ALIASES = {
    "m4a": "mp4",
    "wave": "wav",
}

WavSubChunk = namedtuple('WavSubChunk', ['id', 'position', 'size'])
WavData = namedtuple('WavData', ['audio_format', 'channels', 'sample_rate',
                                 'bits_per_sample', 'raw_data'])


def extract_wav_headers(data):
    # def search_subchunk(data, subchunk_id):
    pos = 12  # The size of the RIFF chunk descriptor
    subchunks = []
    while pos + 8 <= len(data) and len(subchunks) < 10:
        subchunk_id = data[pos:pos + 4]
        subchunk_size = struct.unpack_from('<I', data[pos + 4:pos + 8])[0]
        subchunks.append(WavSubChunk(subchunk_id, pos, subchunk_size))
        if subchunk_id == b'data':
            # 'data' is the last subchunk
            break
        pos += subchunk_size + 8

    return subchunks


def read_wav_audio(data, headers=None):
    if not headers:
        headers = extract_wav_headers(data)

    fmt = [x for x in headers if x.id == b'fmt ']
    if not fmt or fmt[0].size < 16:
        raise CouldntDecodeError("Couldn't find fmt header in wav data")
    fmt = fmt[0]
    pos = fmt.position + 8
    audio_format = struct.unpack_from('<H', data[pos:pos + 2])[0]
    if audio_format != 1 and audio_format != 0xFFFE:
        raise CouldntDecodeError("Unknown audio format 0x%X in wav data" %
                                 audio_format)

    channels = struct.unpack_from('<H', data[pos + 2:pos + 4])[0]
    sample_rate = struct.unpack_from('<I', data[pos + 4:pos + 8])[0]
    bits_per_sample = struct.unpack_from('<H', data[pos + 14:pos + 16])[0]

    data_hdr = headers[-1]
    if data_hdr.id != b'data':
        raise CouldntDecodeError("Couldn't find data header in wav data")

    pos = data_hdr.position + 8
    return WavData(audio_format, channels, sample_rate, bits_per_sample,
                   data[pos:pos + data_hdr.size])


def fix_wav_headers(data):
    headers = extract_wav_headers(data)
    if not headers or headers[-1].id != b'data':
        return

    # TODO: Handle huge files in some other way
    if len(data) > 2**32:
        raise CouldntDecodeError("Unable to process >4GB files")

    # Set the file size in the RIFF chunk descriptor
    data[4:8] = struct.pack('<I', len(data) - 8)

    # Set the data size in the data subchunk
    pos = headers[-1].position
    data[pos + 4:pos + 8] = struct.pack('<I', len(data) - pos - 8)


def _pydub_wav(head):
    try:
        return bool(read_wav_audio(head))
    except (CouldntDecodeError, struct.error):
        return False


def _own_wav(head, flen, read_at):
    # pydub cuts at the stated data size and keeps a cut last frame, the decoder reads like ffmpeg
    h = extract_wav_headers(head)
    fmt = [x for x in h if x.id == b"fmt "][0]
    align, bits = struct.unpack_from("<HH", head, fmt.position + 20)
    pos, size = h[-1].position + 8, h[-1].size
    if size == 0xffffffff or head[:4] == b"RF64":
        return None, b""
    n = max(0, min(size, flen - pos))
    cut = pos if size == 0 else None
    at = pos + size + size % 2
    # a thousand chunks at most, past that it is junk
    for _ in range(1000):
        if cut is not None or at + 8 > flen:
            break
        c = read_at(at, 8)
        if c[:4] == b"data":
            cut = pos + n
        at += 8 + struct.unpack("<I", c[4:])[0] + c[4] % 2
    stray = read_at(pos + n - n % align, n % align) if align and n % align and bits in (8, 16, 24, 32) else b""
    if bits == 8:
        stray = bytes(b ^ 128 for b in stray)
    if bits == 24:
        stray = b"".join((b"\xff" if stray[i + 2] > 127 else b"\0") + stray[i:i + 3] for i in range(0, len(stray) - 2, 3))
    return cut, stray


def _ffmpeg_frames(x, rate):
    # ffmpeg reads str(x) to the microsecond and rounds to the nearest frame
    i, _, f = str(x).partition(".")
    if not i.isdigit() or not (f.isdigit() or f == ""):
        return None
    us = int(i) * 1000000 + int((f + "000000")[:6])
    return (us * rate + 500000) // 1000000


def _slice_frames(n, rate, start, end):
    # the frames AudioSegment[start:end] takes out of n frames
    ms = round(1000 * (n / rate))
    return int(min(start, ms) * (rate / 1000.0)), int(min(end, ms) * (rate / 1000.0))


def _span(d, wav, start_second, duration):
    # (first frame, frame count or -1, final length from the count read) of what pydub returns, None if odd
    if start_second is None and duration is None:
        return 0, -1, lambda m: m
    if (start_second is not None and start_second < 0) or (duration is not None and duration < 0):
        return None
    if wav:
        # pydub slices the whole wav
        ss = (start_second or 0) * 1000
        a, b = _slice_frames(d.frames, d.rate, ss, ss + duration * 1000 if duration is not None else float("inf"))
        return a, max(0, min(b, d.frames) - a), lambda m: max(0, b - a)
    # ffmpeg cuts with -ss and -t after -i, then pydub slices that
    a = _ffmpeg_frames(start_second or 0, d.rate)
    n = -1 if duration is None else _ffmpeg_frames(duration, d.rate)
    if a is None or n is None:
        return None
    end = float("inf") if duration is None else duration * 1000
    return a, n, lambda m: _slice_frames(m, d.rate, 0, end)[1]


def _fit(data, m, want, fw, rate):
    # m frames to want frames like pydub slicing: cut, or fill up to 2 ms with silence
    if want < m:
        return data[:len(data) - (m - want) * fw]
    if want > m and m:
        if want - m > 2 * (rate / 1000.0):
            raise TooManyMissingFrames("You should never be filling in "
                                       "   more than 2 ms with silence here, "
                                       "missing frames: %s" % (want - m))
        data += b"\0" * ((want - m) * fw)
    return data


class AudioSegment(object):
    """
    AudioSegments are *immutable* objects representing segments of audio
    that can be manipulated using python code.

    AudioSegments are slicable using milliseconds.
    for example:
        a = AudioSegment.from_mp3(mp3file)
        first_second = a[:1000] # get the first second of an mp3
        slice = a[5000:10000] # get a slice from 5 to 10 seconds of an mp3
    """
    converter = "ffmpeg"  # either ffmpeg or avconv

    # TODO: remove in 1.0 release
    # maintain backwards compatibility for ffmpeg attr (now called converter)
    @classproperty
    def ffmpeg(cls):
        return cls.converter

    @ffmpeg.setter
    def ffmpeg(cls, val):
        cls.converter = val

    DEFAULT_CODECS = {
        "ogg": "libvorbis"
    }

    def __init__(self, data=None, *args, **kwargs):
        self.sample_width = kwargs.pop("sample_width", None)
        self.frame_rate = kwargs.pop("frame_rate", None)
        self.channels = kwargs.pop("channels", None)

        audio_params = (self.sample_width, self.frame_rate, self.channels)

        if isinstance(data, array.array):
            try:
                data = data.tobytes()
            except:
                data = data.tostring()

        if isinstance(data, (bytearray, memoryview)):
            data = bytes(data)

        # prevent partial specification of arguments
        if any(audio_params) and None in audio_params:
            raise MissingAudioParameter("Either all audio parameters or no parameter must be specified")

        # all arguments are given
        elif self.sample_width is not None:
            if len(data) % (self.sample_width * self.channels) != 0:
                raise ValueError("data length must be a multiple of '(sample_width * channels)'")

            self.frame_width = self.channels * self.sample_width
            self._data = data

        # keep support for 'metadata' until audio params are used everywhere
        elif kwargs.get('metadata', False):
            # internal use only
            self._data = data
            for attr, val in kwargs.pop('metadata').items():
                setattr(self, attr, val)
        else:
            # normal construction
            try:
                data = data if isinstance(data, (basestring, bytes)) else data.read()
            except(OSError):
                d = b''
                reader = data.read(2 ** 31 - 1)
                while reader:
                    d += reader
                    reader = data.read(2 ** 31 - 1)
                data = d

            wav_data = read_wav_audio(data)
            if not wav_data:
                raise CouldntDecodeError("Couldn't read wav audio from data")

            self.channels = wav_data.channels
            self.sample_width = wav_data.bits_per_sample // 8
            self.frame_rate = wav_data.sample_rate
            self.frame_width = self.channels * self.sample_width
            self._data = wav_data.raw_data
            if self.sample_width == 1:
                # convert from unsigned integers in wav
                self._data = _pydub2.bias(self._data, 1, -128)

        # Convert 24-bit audio to 32-bit audio.
        # (stdlib audioop and array modules do not support 24-bit data)
        if self.sample_width == 3:
            # the low byte is padding, 0xff for negative samples like pydub does
            out = bytearray(len(self._data) // 3 * 4)
            out[0::4] = self._data[2::3].translate(bytes(128) + b"\xff" * 128)
            out[1::4] = self._data[0::3]
            out[2::4] = self._data[1::3]
            out[3::4] = self._data[2::3]
            self._data = bytes(out)
            self.sample_width = 4
            self.frame_width = self.channels * self.sample_width

        super(AudioSegment, self).__init__(*args, **kwargs)

    def __getattr__(self, name):
        if name != "_data" or "_parts" not in self.__dict__:
            raise AttributeError(name)
        self._data = b"".join(self._parts)
        return self._data

    @property
    def raw_data(self):
        """
        public access to the raw audio data as a bytestring
        """
        return self._data

    def get_array_of_samples(self, array_type_override=None):
        """
        returns the raw_data as an array of samples
        """
        if array_type_override is None:
            array_type_override = self.array_type
        return array.array(array_type_override, self._data)

    @property
    def array_type(self):
        return get_array_type(self.sample_width * 8)

    def __len__(self):
        """
        returns the length of this audio segment in milliseconds
        """
        return round(1000 * (self.frame_count() / self.frame_rate))

    def __eq__(self, other):
        try:
            return self._data == other._data
        except:
            return False

    def __hash__(self):
        return hash(AudioSegment) ^ hash((self.channels, self.frame_rate, self.sample_width, self._data))

    def __ne__(self, other):
        return not (self == other)

    def __iter__(self):
        return (self[i] for i in xrange(len(self)))

    def __getitem__(self, millisecond):
        if isinstance(millisecond, slice):
            if millisecond.step:
                return (
                    self[i:i + millisecond.step]
                    for i in xrange(*millisecond.indices(len(self)))
                )

            start = millisecond.start if millisecond.start is not None else 0
            end = millisecond.stop if millisecond.stop is not None \
                else len(self)

            start = min(start, len(self))
            end = min(end, len(self))
        else:
            start = millisecond
            end = millisecond + 1

        start = self._parse_position(start) * self.frame_width
        end = self._parse_position(end) * self.frame_width
        data = self._data[start:end]

        # ensure the output is as long as the requester is expecting
        expected_length = end - start
        missing_frames = (expected_length - len(data)) // self.frame_width
        if missing_frames:
            if missing_frames > self.frame_count(ms=2):
                raise TooManyMissingFrames(
                    "You should never be filling in "
                    "   more than 2 ms with silence here, "
                    "missing frames: %s" % missing_frames)
            silence = _pydub2.mul(data[:self.frame_width],
                                  self.sample_width, 0)
            data += (silence * missing_frames)

        return self._spawn(data)

    def get_sample_slice(self, start_sample=None, end_sample=None):
        """
        Get a section of the audio segment by sample index.

        NOTE: Negative indices do *not* address samples backword
        from the end of the audio segment like a python list.
        This is intentional.
        """
        max_val = int(self.frame_count())

        def bounded(val, default):
            if val is None:
                return default
            if val < 0:
                return 0
            if val > max_val:
                return max_val
            return val

        start_i = bounded(start_sample, 0) * self.frame_width
        end_i = bounded(end_sample, max_val) * self.frame_width

        data = self._data[start_i:end_i]
        return self._spawn(data)

    def __add__(self, arg):
        if isinstance(arg, AudioSegment):
            return self.append(arg, crossfade=0)
        else:
            return self.apply_gain(arg)

    def __radd__(self, rarg):
        """
        Permit use of sum() builtin with an iterable of AudioSegments
        """
        if rarg == 0:
            return self
        raise TypeError("Gains must be the second addend after the "
                        "AudioSegment")

    def __sub__(self, arg):
        if isinstance(arg, AudioSegment):
            raise TypeError("AudioSegment objects can't be subtracted from "
                            "each other")
        else:
            return self.apply_gain(-arg)

    def __mul__(self, arg):
        """
        If the argument is an AudioSegment, overlay the multiplied audio
        segment.

        If it's a number, just use the string multiply operation to repeat the
        audio.

        The following would return an AudioSegment that contains the
        audio of audio_seg eight times

        `audio_seg * 8`
        """
        if isinstance(arg, AudioSegment):
            return self.overlay(arg, position=0, loop=True)
        else:
            return self._spawn(data=self._data * arg)

    def _spawn(self, data, overrides={}):
        """
        Creates a new audio segment using the metadata from the current one
        and the data passed in. Should be used whenever an AudioSegment is
        being returned by an operation that would alters the current one,
        since AudioSegment objects are immutable.
        """
        # accept lists of data chunks
        if isinstance(data, list):
            data = b''.join(data)

        if isinstance(data, array.array):
            try:
                data = data.tobytes()
            except:
                data = data.tostring()

        # accept file-like objects
        if hasattr(data, 'read'):
            if hasattr(data, 'seek'):
                data.seek(0)
            data = data.read()

        metadata = {
            'sample_width': self.sample_width,
            'frame_rate': self.frame_rate,
            'frame_width': self.frame_width,
            'channels': self.channels
        }
        metadata.update(overrides)
        return self.__class__(data=data, metadata=metadata)

    @classmethod
    def _sync(cls, *segs):
        channels = max(seg.channels for seg in segs)
        frame_rate = max(seg.frame_rate for seg in segs)
        sample_width = max(seg.sample_width for seg in segs)

        return tuple(
            seg.set_channels(channels).set_frame_rate(frame_rate).set_sample_width(sample_width)
            for seg in segs
        )

    def _parse_position(self, val):
        if val < 0:
            val = len(self) - abs(val)
        val = self.frame_count(ms=len(self)) if val == float("inf") else \
            self.frame_count(ms=val)
        return int(val)

    @classmethod
    def empty(cls):
        return cls(b'', metadata={
            "channels": 1,
            "sample_width": 1,
            "frame_rate": 1,
            "frame_width": 1
        })

    @classmethod
    def silent(cls, duration=1000, frame_rate=11025):
        """
        Generate a silent audio segment.
        duration specified in milliseconds (default duration: 1000ms, default frame_rate: 11025).
        """
        frames = int(frame_rate * (duration / 1000.0))
        data = b"\0\0" * frames
        return cls(data, metadata={"channels": 1,
                                   "sample_width": 2,
                                   "frame_rate": frame_rate,
                                   "frame_width": 2})

    @classmethod
    def from_mono_audiosegments(cls, *mono_segments, fill_up=False):
        if not len(mono_segments):
            raise ValueError("At least one AudioSegment instance is required")

        segs = cls._sync(*mono_segments)

        if segs[0].channels != 1:
            raise ValueError(
                "AudioSegment.from_mono_audiosegments requires all arguments are mono AudioSegment instances")

        channels = len(segs)
        sample_width = segs[0].sample_width
        frame_rate = segs[0].frame_rate

        frame_count = max(int(seg.frame_count()) for seg in segs)
        data = array.array(
            segs[0].array_type,
            b'\0' * (frame_count * sample_width * channels)
        )

        for i, seg in enumerate(segs):
            samples = seg.get_array_of_samples()
            data[i:len(samples) * channels if fill_up else None:channels] = samples

        return cls(
            data,
            channels=channels,
            sample_width=sample_width,
            frame_rate=frame_rate,
        )

    @classmethod
    def from_file_using_temporary_files(cls, file, format=None, codec=None, parameters=None, start_second=None, duration=None, **kwargs):
        return cls.from_file(file, format, codec, parameters, start_second, duration, **kwargs)

    @classmethod
    def from_file(cls, file, format=None, codec=None, parameters=None, start_second=None, duration=None, **kwargs):
        try:
            filename = fsdecode(file)
        except TypeError:
            filename = None
        if isinstance(file, bytes):
            file = filename
        file, close_file = _fd_or_path_or_tempfile(file, 'rb', tempfile=False)

        if format:
            format = format.lower()
            format = AUDIO_FILE_EXT_ALIASES.get(format, format)

        def is_format(f):
            f = f.lower()
            if format == f:
                return True

            if filename:
                return filename.lower().endswith(".{0}".format(f))

            return False

        def decoded(data):
            if pydub2.strict:
                return cls(data=data)
            data, frame_rate, channels, sample_width = _pydub2.decode(data)
            return cls(data=data, metadata={
                'sample_width': sample_width,
                'frame_rate': frame_rate,
                'channels': channels,
                'frame_width': channels * sample_width
            })

        wav = is_format("wav")
        if not wav and (is_format("raw") or is_format("pcm")):
            sample_width = kwargs['sample_width']
            frame_rate = kwargs['frame_rate']
            channels = kwargs['channels']
            metadata = {
                'sample_width': sample_width,
                'frame_rate': frame_rate,
                'channels': channels,
                'frame_width': channels * sample_width
            }
            if start_second is None and duration is None:
                return cls(data=file.read(), metadata=metadata)
            elif start_second is not None and duration is None:
                return cls(data=file.read(), metadata=metadata)[start_second*1000:]
            elif start_second is None and duration is not None:
                return cls(data=file.read(), metadata=metadata)[:duration*1000]
            else:
                return cls(data=file.read(), metadata=metadata)[start_second*1000:(start_second+duration)*1000]

        if pydub2.strict and wav:
            # what pydub does, it reads any wav it can parse itself
            try:
                obj = cls._from_safe_wav(file)
                if start_second is not None or duration is not None:
                    obj = obj[None if start_second is None else start_second * 1000:
                              None if duration is None else ((start_second or 0) + duration) * 1000]
                if close_file:
                    file.close()
                return obj
            except Exception:
                pass
        if wav:
            # pydub rewinds a wav
            file.seek(0)
        # a name goes to the decoder as it is, so the file never sits in memory
        data = None if close_file else bytes(file.read())
        # pydub reads pcm wav itself, without codec or parameters, and cuts it in python
        head = file.read(65536) if data is None else data[:65536]
        own = wav and _pydub_wav(head)
        if close_file:
            file.close()
        # ffmpeg cuts vorbis by packet times that drift from the sample count, only ffmpeg lands on the same sample
        cut = (start_second is not None or duration is not None) and head[:4] == b"OggS" and len(head) > 27 and \
            head[27 + head[26]:][:7] == b"\x01vorbis" and _ffmpeg.find(cls.converter)

        tried = False
        if not pydub2.strict and (own or (codec is None and parameters is None)) and not cut:
            tried = True
            try:
                d = _pydub2.Decoder(filename if data is None else data)
                if d.chained and _ffmpeg.find(cls.converter):
                    raise RuntimeError("ffmpeg joins the links")
                stray = b""
                if own:
                    fh = open(filename, "rb") if data is None else None

                    def read_at(at, n):
                        if fh is None:
                            return data[at:at + n]
                        fh.seek(at)
                        return fh.read(n)
                    try:
                        cut_at, stray = _own_wav(head, len(data) if data is not None else os.path.getsize(filename), read_at)
                        if cut_at is not None:
                            d = _pydub2.Decoder(read_at(0, cut_at))
                    finally:
                        if fh is not None:
                            fh.close()
                span = None if stray else _span(d, own, start_second, duration)
                if span:
                    start, n, final = span
                    if start:
                        d.seek(start)
                    pcm = read_capped(d, n)
                    fw = d.channels * d.width
                    return cls._segment(d, _fit(pcm, len(pcm) // fw, final(len(pcm) // fw), fw, d.rate))
                obj = cls._segment(d, read_capped(d, -1) + stray)
                if start_second is None and duration is None:
                    return obj
                elif start_second is not None and duration is None:
                    return obj[start_second*1000:]
                elif start_second is None and duration is not None:
                    return obj[:duration*1000]
                else:
                    return obj[start_second*1000:(start_second+duration)*1000]
            except RuntimeError:
                pass

        if tried and _ffmpeg.find(cls.converter) is None and any(is_format(f) for f in ("wav", "flac", "mp3", "ogg", "opus")):
            raise CouldntDecodeError("cant read this file")
        obj = decoded(_ffmpeg.to_wav(cls.converter, filename, data, format, codec, parameters,
                                     start_second, duration))

        if start_second is None and duration is None:
            return obj
        elif start_second is not None and duration is None:
            return obj[0:]
        elif start_second is None and duration is not None:
            return obj[:duration * 1000]
        else:
            return obj[0:duration * 1000]

    @classmethod
    def _segment(cls, d, data):
        return cls(data=data, metadata={
            'sample_width': d.width,
            'frame_rate': d.rate,
            'channels': d.channels,
            'frame_width': d.channels * d.width
        })

    @classmethod
    def stream(cls, file, chunk_ms=10000, start_second=None, duration=None):
        """Yields the file in chunk_ms pieces without loading all of it. wav, flac, mp3, ogg and opus only."""
        filename = None if hasattr(file, "read") else fsdecode(file)
        try:
            d = _pydub2.Decoder(bytes(file.read()) if filename is None else filename)
        except RuntimeError:
            raise CouldntDecodeError("stream only reads wav, flac, mp3, ogg and opus")

        def run(f, *args):
            try:
                return f(*args)
            except RuntimeError:
                raise CouldntDecodeError("cant read this file")
        own = False
        if filename is not None and filename.lower().endswith(".wav"):
            with open(filename, "rb") as f:
                own = _pydub_wav(f.read(65536))
        span = _span(d, own, start_second, duration)
        if not span:
            raise ValueError("start_second and duration have to be plain numbers, 0 or more")
        start, n, final = span
        fw = d.channels * d.width
        step = int(chunk_ms * (d.rate / 1000.0))
        if step < 1:
            raise ValueError("chunk_ms is too small")
        if start:
            run(d.seek, start)

        def chunks():
            prev = run(d.read, step if n < 0 else min(step, n))
            got = len(prev) // fw
            check_duration(got, d.rate)
            while True:
                k = step if n < 0 else min(step, n - got)
                cur = run(d.read, k) if k > 0 else b""
                got += len(cur) // fw
                if pydub2.max_duration is not None and got > pydub2.max_duration * d.rate:
                    yield cls._segment(d, prev)
                    check_duration(got, d.rate)
                if k <= 0 or len(cur) < k * fw:
                    break
                yield cls._segment(d, prev)
                prev = cur
            # only the end can change, by under a ms, so it stays inside the last two chunks
            tail = _fit(prev + cur, got, final(got), fw, d.rate)
            for i in range(0, len(tail), step * fw):
                yield cls._segment(d, tail[i:i + step * fw])

        return chunks()

    @classmethod
    def from_mp3(cls, file, parameters=None):
        return cls.from_file(file, 'mp3', parameters=parameters)

    @classmethod
    def from_flv(cls, file, parameters=None):
        return cls.from_file(file, 'flv', parameters=parameters)

    @classmethod
    def from_ogg(cls, file, parameters=None):
        return cls.from_file(file, 'ogg', parameters=parameters)

    @classmethod
    def from_wav(cls, file, parameters=None):
        return cls.from_file(file, 'wav', parameters=parameters)

    @classmethod
    def from_raw(cls, file, **kwargs):
        return cls.from_file(file, 'raw', sample_width=kwargs['sample_width'], frame_rate=kwargs['frame_rate'],
                             channels=kwargs['channels'])

    @classmethod
    def _from_safe_wav(cls, file):
        file, close_file = _fd_or_path_or_tempfile(file, 'rb', tempfile=False)
        file.seek(0)
        obj = cls(data=file)
        if close_file:
            file.close()
        return obj

    def export(self, out_f=None, format=None, codec=None, bitrate=None, parameters=None, tags=None, id3v2_version='4',
               cover=None):
        """
        Export an AudioSegment to a file with given options

        out_f (string):
            Path to destination audio file. Also accepts os.PathLike objects on
            python >= 3.6

        format (string)
            Format for destination audio file, taken from the extension of out_f when left out (mp3 if unknown).
            ('mp3', 'wav', 'raw', 'ogg' or other ffmpeg/avconv supported files)

        codec (string)
            Codec used to encode the destination file.

        bitrate (string)
            Bitrate used when encoding destination file. (64, 92, 128, 256, 312k...)
            Each codec accepts different bitrate arguments so take a look at the
            ffmpeg documentation for details (bitrate usually shown as -b, -ba or
            -a:b).

        parameters (list of strings)
            Aditional ffmpeg/avconv parameters

        tags (dict)
            Set metadata information to destination files
            usually used as tags. ({title='Song Title', artist='Song Artist'})

        id3v2_version (string)
            Set ID3v2 version for tags. (default: '4')

        cover (file)
            Set cover for audio file from image file. (png or jpg)
        """
        id3v2_allowed_versions = ['3', '4']

        if format is None:
            ext = ""
            if isinstance(out_f, (basestring, os.PathLike)):
                ext = os.path.splitext(fsdecode(out_f))[1][1:].lower()
            format = ext if ext in ("wav", "flac", "ogg", "opus", "mp3", "m4a", "aac") and not pydub2.strict else "mp3"

        if format in ("raw", "pcm") and (codec is not None or parameters is not None):
            raise AttributeError(
                    'Can not invoke ffmpeg when export format is "raw"; '
                    'specify an ffmpeg raw format like format="s16le" instead '
                    'or call export(format="raw") with no codec or parameters')

        out_f, _ = _fd_or_path_or_tempfile(out_f, 'wb+')
        out_f.seek(0)

        if format in ("raw", "pcm"):
            out_f.write(self._data)
            out_f.seek(0)
            return out_f

        # wav ignores tags and bitrate in pydub too
        if codec is None and parameters is None and (
                format == "wav" or (format in ("flac", "ogg", "opus") and cover is None and not pydub2.strict)):
            rate = 0
            if bitrate and format in ("ogg", "opus"):
                bitrate = str(bitrate).lower()
                unit = {"k": 1000, "m": 1000000}.get(bitrate[-1])
                rate = int(float(bitrate[:-1]) * unit) if unit else int(bitrate)
            data, width = self._data, self.sample_width
            if format == "flac" and width == 4:
                # ffmpeg writes 32 bit audio as 24 bit flac
                data, width = _pydub2.lin2lin(data, 4, 3), 3
            if format == "wav" and width in (2, 4):
                # 8 bit is unsigned in a wav, 16 and 32 bit go out as they are
                try:
                    out_f.write(_pydub2.wav_head(data, round(self.frame_rate), self.channels, width))
                except RuntimeError as e:
                    raise CouldntEncodeError(str(e))
                out_f.write(data)
            else:
                out = _encode(data, self.frame_rate, self.channels, width, format, rate,
                              [] if tags is None or format == "wav" else _vorbis_tags(tags, format))
                if format == "wav" and out[40] & 1 and not WAVE_PADS:
                    out = riff_without_pad(out[:-1])
                out_f.write(out)
            out_f.seek(0)
            return out_f

        if tags is not None:
            if not isinstance(tags, dict):
                raise InvalidTag("Tags must be a dictionary.")
            if format == 'mp3' and id3v2_version not in id3v2_allowed_versions:
                raise InvalidID3TagVersion(
                    "id3v2_version not allowed, allowed versions: %s" % id3v2_allowed_versions)

        if format in ("wav", "flac") and (codec or parameters) and cover is None and tags is None and not pydub2.strict:
            out = _export_pcm(self, format, codec, parameters)
            if out is not None:
                out_f.write(out)
                out_f.seek(0)
                return out_f

        if format == "mp3" and codec is None and cover is None and not pydub2.strict:
            if _mp3.available():
                mp3 = _mp3.encode(self._data, self.frame_rate, self.channels, self.sample_width,
                                  bitrate, tags, id3v2_version, parameters, self.converter)
                if mp3 is not None:
                    out_f.write(mp3)
                    out_f.seek(0)
                    return out_f
            elif _ffmpeg.find(self.converter) is None:
                raise FileNotFoundError("ffmpeg not found, pip install pydub2-mp3 to write mp3 without it")

        wav = _encode(self._data, self.frame_rate, self.channels, self.sample_width, "wav")
        out_f.write(_ffmpeg.from_wav(self.converter, wav, format, codec, bitrate, parameters, tags,
                                     id3v2_version, cover))
        out_f.seek(0)
        return out_f

    @classmethod
    def from_numpy(cls, array, frame_rate):
        """array is (n,) or (n, channels), int8/16/32 as is, float32/64 in -1..1 becomes 16 bit"""
        import numpy as np
        array = np.asarray(array)
        if array.dtype.kind == "f":
            array = np.rint(np.clip(array, -1, 1) * 32767).astype(np.int16)
        if array.dtype not in (np.int8, np.int16, np.int32) or array.ndim not in (1, 2):
            raise ValueError("need a 1d or 2d array of int8, int16, int32, float32 or float64")
        return cls(np.ascontiguousarray(array).tobytes(), sample_width=array.dtype.itemsize,
                   frame_rate=frame_rate, channels=array.shape[1] if array.ndim == 2 else 1)

    def to_numpy(self):
        import numpy as np
        return np.frombuffer(self._data, dtype="int%d" % (self.sample_width * 8)).reshape(-1, self.channels).copy()

    def get_frame(self, index):
        frame_start = index * self.frame_width
        frame_end = frame_start + self.frame_width
        return self._data[frame_start:frame_end]

    def frame_count(self, ms=None):
        """
        returns the number of frames for the given number of milliseconds, or
            if not specified, the number of frames in the whole AudioSegment
        """
        if ms is not None:
            return ms * (self.frame_rate / 1000.0)
        else:
            return float(len(self._data) // self.frame_width)

    def set_sample_width(self, sample_width):
        if sample_width == self.sample_width:
            return self

        frame_width = self.channels * sample_width

        return self._spawn(
            _pydub2.lin2lin(self._data, self.sample_width, sample_width),
            overrides={'sample_width': sample_width, 'frame_width': frame_width}
        )

    def set_frame_rate(self, frame_rate, resample_quality=None):
        """resample_quality="high" uses a proper resampler instead of pydub's ratecv"""
        if resample_quality not in (None, "high"):
            raise ValueError("resample_quality must be None or 'high'")

        if frame_rate == self.frame_rate:
            return self

        if self._data and resample_quality == "high":
            converted = _pydub2.resample(self._data, self.frame_rate, self.channels,
                                         self.sample_width, frame_rate)
        elif self._data:
            converted = _pydub2.ratecv(self._data, self.sample_width,
                                       self.channels, self.frame_rate,
                                       frame_rate)
        else:
            converted = self._data

        return self._spawn(data=converted,
                           overrides={'frame_rate': frame_rate})

    def set_channels(self, channels):
        if channels == self.channels:
            return self

        if channels == 2 and self.channels == 1:
            fn = _pydub2.tostereo
            frame_width = self.frame_width * 2
            fac = 1
            converted = fn(self._data, self.sample_width, fac, fac)
        elif channels == 1 and self.channels == 2:
            fn = _pydub2.tomono
            frame_width = self.frame_width // 2
            fac = 0.5
            converted = fn(self._data, self.sample_width, fac, fac)
        elif channels == 1:
            converted = _pydub2.downmix(self._data, self.sample_width, self.channels)
            frame_width = self.frame_width // self.channels
        elif self.channels == 1:
            dup_channels = [self for iChannel in range(channels)]
            return AudioSegment.from_mono_audiosegments(*dup_channels)
        else:
            raise ValueError(
                "AudioSegment.set_channels only supports mono-to-multi channel and multi-to-mono channel conversion")

        return self._spawn(data=converted,
                           overrides={
                               'channels': channels,
                               'frame_width': frame_width})

    def split_to_mono(self):
        if self.channels == 1:
            return [self]

        samples = self.get_array_of_samples()

        mono_channels = []
        for i in range(self.channels):
            samples_for_current_channel = samples[i::self.channels]

            try:
                mono_data = samples_for_current_channel.tobytes()
            except AttributeError:
                mono_data = samples_for_current_channel.tostring()

            mono_channels.append(
                self._spawn(mono_data, overrides={"channels": 1, "frame_width": self.sample_width})
            )

        return mono_channels

    @property
    def rms(self):
        return _pydub2.rms(self._data, self.sample_width)

    @property
    def dBFS(self):
        rms = self.rms
        if not rms:
            return -float("infinity")
        return ratio_to_db(rms / self.max_possible_amplitude)

    @property
    def max(self):
        return _pydub2.max(self._data, self.sample_width)

    @property
    def max_possible_amplitude(self):
        bits = self.sample_width * 8
        max_possible_val = (2 ** bits)

        # since half is above 0 and half is below the max amplitude is divided
        return max_possible_val / 2

    @property
    def max_dBFS(self):
        return ratio_to_db(self.max, self.max_possible_amplitude)

    def loudness(self):
        """integrated loudness in LUFS (EBU R128)"""
        return _pydub2.loudness(self._data, self.frame_rate, self.channels, self.sample_width)

    @property
    def duration_seconds(self):
        return self.frame_rate and self.frame_count() / self.frame_rate or 0.0

    def get_dc_offset(self, channel=1):
        """
        Returns a value between -1.0 and 1.0 representing the DC offset of a
        channel (1 for left, 2 for right).
        """
        if not 1 <= channel <= 2:
            raise ValueError("channel value must be 1 (left) or 2 (right)")

        if self.channels == 1:
            data = self._data
        elif channel == 1:
            data = _pydub2.tomono(self._data, self.sample_width, 1, 0)
        else:
            data = _pydub2.tomono(self._data, self.sample_width, 0, 1)

        return float(_pydub2.avg(data, self.sample_width)) / self.max_possible_amplitude

    def remove_dc_offset(self, channel=None, offset=None):
        """
        Removes DC offset of given channel. Calculates offset if it's not given.
        Offset values must be in range -1.0 to 1.0. If channel is None, removes
        DC offset from all available channels.
        """
        if channel and not 1 <= channel <= 2:
            raise ValueError("channel value must be None, 1 (left) or 2 (right)")

        if offset and not -1.0 <= offset <= 1.0:
            raise ValueError("offset value must be in range -1.0 to 1.0")

        if offset:
            offset = int(round(offset * self.max_possible_amplitude))

        def remove_data_dc(data, off):
            if not off:
                off = _pydub2.avg(data, self.sample_width)
            try:
                return _pydub2.bias(data, self.sample_width, -off)
            except TypeError:
                raise ValueError("offset does not fit in the sample width")

        if self.channels == 1:
            return self._spawn(data=remove_data_dc(self._data, offset))

        left_channel = _pydub2.tomono(self._data, self.sample_width, 1, 0)
        right_channel = _pydub2.tomono(self._data, self.sample_width, 0, 1)

        if not channel or channel == 1:
            left_channel = remove_data_dc(left_channel, offset)

        if not channel or channel == 2:
            right_channel = remove_data_dc(right_channel, offset)

        left_channel = _pydub2.tostereo(left_channel, self.sample_width, 1, 0)
        right_channel = _pydub2.tostereo(right_channel, self.sample_width, 0, 1)

        return self._spawn(data=_pydub2.add(left_channel, right_channel,
                                            self.sample_width))

    def apply_gain(self, volume_change):
        return self._spawn(data=_pydub2.mul(self._data, self.sample_width,
                                            db_to_float(float(volume_change))))

    def overlay(self, seg, position=0, loop=False, times=None, gain_during_overlay=None):
        """
        Overlay the provided segment on to this segment starting at the
        specificed position and using the specfied looping beahvior.

        seg (AudioSegment):
            The audio segment to overlay on to this one.

        position (optional int):
            The position to start overlaying the provided segment in to this
            one.

        loop (optional bool):
            Loop seg as many times as necessary to match this segment's length.
            Overrides loops param.

        times (optional int):
            Loop seg the specified number of times or until it matches this
            segment's length. 1 means once, 2 means twice, ... 0 would make the
            call a no-op
        gain_during_overlay (optional int):
            Changes this segment's volume by the specified amount during the
            duration of time that seg is overlaid on top of it. When negative,
            this has the effect of 'ducking' the audio under the overlay.
        """

        if loop:
            # match loop=True's behavior with new times (count) mechinism.
            times = -1
        elif times is None:
            # no times specified, just once through
            times = 1
        elif times == 0:
            # it's a no-op, make a copy since we never mutate
            return self._spawn(self._data)

        seg1, seg2 = AudioSegment._sync(self, seg)
        sample_width = seg1.sample_width
        spawn = seg1._spawn

        head = seg1[:position]._data

        # drop down to the raw data
        seg1 = seg1[position:]._data
        seg2 = seg2._data
        remaining = len(seg1)
        n = -(-remaining // len(seg2)) if seg2 else 0
        # a times that never counts down to 0 loops to the end, like negative
        if times > 0 and times % 1 == 0:
            n = min(n, int(times))
        seg2 = (seg2 * n)[:remaining]
        pos = len(seg2)
        if gain_during_overlay:
            seg1_overlaid = _pydub2.mul(seg1[:pos], self.sample_width, db_to_float(float(gain_during_overlay)))
        else:
            seg1_overlaid = seg1[:pos]
        output = [head, _pydub2.add(seg1_overlaid, seg2, sample_width), memoryview(seg1)[pos:]]

        return spawn(data=output)

    def append(self, seg, crossfade=100):
        seg1, seg2 = AudioSegment._sync(self, seg)

        if not crossfade:
            # joined on first read of _data, so seg += chunk in a loop stays linear
            parts = seg1.__dict__.get("_parts") or [seg1._data]
            out = seg1._spawn(b"")
            del out._data
            out._parts = parts + [seg2._data]
            return out
        elif crossfade > len(self):
            raise ValueError("Crossfade is longer than the original AudioSegment ({}ms > {}ms)".format(
                crossfade, len(self)
            ))
        elif crossfade > len(seg):
            raise ValueError("Crossfade is longer than the appended AudioSegment ({}ms > {}ms)".format(
                crossfade, len(seg)
            ))

        xf = seg1[-crossfade:].fade(to_gain=-120, start=0, end=float('inf'))
        xf *= seg2[:crossfade].fade(from_gain=-120, start=0, end=float('inf'))

        output = TemporaryFile()

        output.write(seg1[:-crossfade]._data)
        output.write(xf._data)
        output.write(seg2[crossfade:]._data)

        output.seek(0)
        obj = seg1._spawn(data=output)
        output.close()
        return obj

    def fade(self, to_gain=0, from_gain=0, start=None, end=None,
             duration=None):
        """
        Fade the volume of this audio segment.

        to_gain (float):
            resulting volume_change in db

        start (int):
            default = beginning of the segment
            when in this segment to start fading in milliseconds

        end (int):
            default = end of the segment
            when in this segment to start fading in milliseconds

        duration (int):
            default = until the end of the audio segment
            the duration of the fade
        """
        if None not in [duration, end, start]:
            raise TypeError('Only two of the three arguments, "start", '
                            '"end", and "duration" may be specified')

        # no fade == the same audio
        if to_gain == 0 and from_gain == 0:
            return self

        start = min(len(self), start) if start is not None else None
        end = min(len(self), end) if end is not None else None

        if start is not None and start < 0:
            start += len(self)
        if end is not None and end < 0:
            end += len(self)

        if duration is not None and duration < 0:
            raise InvalidDuration("duration must be a positive integer")

        if duration:
            if start is not None:
                end = start + duration
            elif end is not None:
                start = end - duration
        else:
            duration = end - start

        from_power = db_to_float(from_gain)

        output = []

        # original data - up until the crossfade portion, as is
        before_fade = self[:start]._data
        if from_gain != 0:
            before_fade = _pydub2.mul(before_fade,
                                      self.sample_width,
                                      from_power)
        output.append(before_fade)

        gain_delta = db_to_float(to_gain) - from_power

        # fades longer than 100ms can use coarse fading (one gain step per ms),
        # shorter fades will have audible clicks so they use precise fading
        # (one gain step per sample)
        if duration > 100:
            scale_step = gain_delta / duration

            # chunks past the end are empty, below 500 hz they raise at the first one
            last = duration if self.frame_rate < 500 else max(0, int(len(self) - start) + 2)
            for i in range(duration)[:last]:
                volume_change = from_power + (scale_step * i)
                chunk = self[start + i]
                chunk = _pydub2.mul(chunk._data,
                                    self.sample_width,
                                    volume_change)

                output.append(chunk)
        else:
            start_frame = self.frame_count(ms=start)
            end_frame = self.frame_count(ms=end)
            fade_frames = end_frame - start_frame
            scale_step = gain_delta / (fade_frames or 1)

            for i in range(int(fade_frames)):
                volume_change = from_power + (scale_step * i)
                sample = self.get_frame(int(start_frame + i))
                sample = _pydub2.mul(sample, self.sample_width, volume_change)

                output.append(sample)

        # original data after the crossfade portion, at the new volume
        after_fade = self[end:]._data
        if to_gain != 0:
            after_fade = _pydub2.mul(after_fade,
                                     self.sample_width,
                                     db_to_float(to_gain))
        output.append(after_fade)

        return self._spawn(data=output)

    def fade_out(self, duration):
        return self.fade(to_gain=-120, duration=duration, end=float('inf'))

    def fade_in(self, duration):
        return self.fade(from_gain=-120, duration=duration, start=0)

    def reverse(self):
        return self._spawn(
            data=_pydub2.reverse(self._data, self.sample_width)
        )

    def _repr_html_(self):
        src = """
                    <audio controls>
                        <source src="data:audio/mpeg;base64,{base64}" type="audio/mpeg"/>
                        Your browser does not support the audio element.
                    </audio>
                  """
        fh = self.export()
        data = base64.b64encode(fh.read()).decode('ascii')
        return src.format(base64=data)


from . import effects
