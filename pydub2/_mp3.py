import ctypes
import functools
import struct
import threading

import _pydub2

from . import _ffmpeg
from .exceptions import CouldntEncodeError

RATES = [44100, 48000, 32000, 22050, 24000, 16000, 11025, 12000, 8000]

# ffmpeg names for id3 frames, everything else goes to TXXX like ffmpeg does
FRAMES = {"album": "TALB", "composer": "TCOM", "genre": "TCON", "copyright": "TCOP",
          "encoded_by": "TENC", "title": "TIT2", "language": "TLAN", "artist": "TPE1",
          "album_artist": "TPE2", "performer": "TPE3", "disc": "TPOS", "publisher": "TPUB",
          "track": "TRCK", "encoder": "TSSE", "date": "TYER"}

# version bits, rates by header index, kbps by header index
MPEG = [(3, [44100, 48000, 32000], [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]),
        (2, [22050, 24000, 16000], [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]),
        (0, [11025, 12000, 8000], [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])]


# lame fills static tables the first time it is set up
setup = threading.Lock()


@functools.lru_cache(None)
def lib():
    try:
        import pydub2_mp3
    except ImportError:
        return None
    lame = ctypes.CDLL(pydub2_mp3.path())
    lame.lame_init.restype = ctypes.c_void_p
    return lame


def available():
    return lib() is not None


def rate_of(text):
    text = str(text).lower()
    unit = {"k": 1000, "m": 1000000}.get(text[-1])
    return int(float(text[:-1]) * unit) if unit else int(float(text))


def info_frame(rate, channels, frames, music, delay, padding):
    # lame skips its tag when the cbr frame is too small for it, so make one in a frame that fits
    ver, rates, kbps = next(m for m in MPEG if rate in m[1])
    side = (21 if channels == 1 else 36) if ver == 3 else (13 if channels == 1 else 21)
    unit = 144000 if ver == 3 else 72000
    i = next(i for i in range(1, 15) if unit * kbps[i] // rate >= side + 156)
    size = unit * kbps[i] // rate
    head = bytes([0xFF, 0xE3 | ver << 3, i << 4 | rates.index(rate) << 2, 0xC0 if channels == 1 else 0x40])
    toc = bytes(255 * k // 100 for k in range(100))
    gapless = bytes([delay >> 4, delay << 4 & 255 | padding >> 8, padding & 255])
    # crcs stay 0, no reader checks them
    tag = struct.pack(">4sIII100sI9sBB8xBB3sBBHIHH", b"Info", 15, frames, music + size, toc, 0, b"LAME3.100",
                      1, 0, 0, 0, gapless, 0, 0, 0, music + size, 0, 0)
    return (head + bytes(side - 4) + tag).ljust(size, b"\0")


def encode(data, rate, channels, width, bitrate=None, tags=None, id3v2_version="4", parameters=None,
           converter="ffmpeg"):
    out_rate, out_channels, quality = rate, channels, None
    params = list(parameters or [])
    if len(params) % 2 or channels > 2:
        return None
    try:
        for k, v in zip(params[::2], params[1::2]):
            if k in ("-q:a", "-qscale:a"):
                quality = float(v)
            elif k == "-b:a":
                bitrate = v
            elif k == "-ar":
                out_rate = rate_of(v)
            elif k == "-ac" and int(v) in (1, 2):
                out_channels = int(v)
            else:
                return None
        if bitrate:
            rate_of(bitrate)
    except (ValueError, IndexError):
        return None
    if quality is not None and not 0 <= quality <= 9.999:
        return None
    # lame writes id3v2.3, ffmpeg does the default 2.4
    if tags and id3v2_version == "4" and _ffmpeg.find(converter):
        return None
    # ffmpeg picks the nearest rate lame can do
    out_rate = min(RATES, key=lambda r: abs(r - out_rate))

    # ffmpeg mixes and resamples before lame too, so do the same here
    if out_channels == 1 and channels == 2:
        data = _pydub2.tomono(data, width, 0.5, 0.5)
    elif out_channels == 2 and channels == 1:
        data = _pydub2.tostereo(data, width, 0.7071067811865476, 0.7071067811865476)
    channels = out_channels
    if out_rate != rate:
        data = _pydub2.resample(data, rate, channels, width, out_rate)
        rate = out_rate

    lame = lib()
    with setup:
        gfp = ctypes.c_void_p(lame.lame_init())
    if not gfp:
        raise MemoryError("cant start lame")
    try:
        lame.lame_set_num_channels(gfp, channels)
        lame.lame_set_in_samplerate(gfp, rate)
        lame.lame_set_out_samplerate(gfp, rate)
        lame.lame_set_mode(gfp, 1 if channels == 2 else 3)
        if quality is not None:
            lame.lame_set_VBR(gfp, 4)
            lame.lame_set_VBR_quality(gfp, ctypes.c_float(quality))
        elif bitrate:
            lame.lame_set_brate(gfp, rate_of(bitrate) // 1000)
        lame.lame_set_write_id3tag_automatic(gfp, 0)
        if tags:
            lame.id3tag_init(gfp)
            lame.id3tag_add_v2(gfp)
            lame.id3tag_v2_only(gfp)
            for k, v in tags.items():
                frame = FRAMES.get(k.lower(), "TXXX")
                text = str(v) if frame != "TXXX" else "%s=%s" % (k, v)
                if text.isascii():
                    lame.id3tag_set_textinfo_latin1(gfp, frame.encode(), text.encode())
                else:
                    lame.id3tag_set_textinfo_utf16(gfp, frame.encode(), ("\ufeff" + text + "\0").encode("utf-16-le"))
        with setup:
            failed = lame.lame_init_params(gfp) < 0
        if failed:
            raise CouldntEncodeError("lame cant encode with these settings")

        n = lame.lame_get_id3v2_tag(gfp, None, ctypes.c_size_t(0))
        id3 = ctypes.create_string_buffer(n)
        lame.lame_get_id3v2_tag(gfp, id3, ctypes.c_size_t(n))

        pcm = data if width == 4 else _pydub2.lin2lin(data, width, 4)
        frames = len(pcm) // (4 * channels)
        base = ctypes.cast(ctypes.c_char_p(pcm), ctypes.c_void_p).value
        step = 1152 * 64
        buf = ctypes.create_string_buffer(step * 5 // 4 + 7200)
        out = []
        for i in range(0, frames, step):
            k = min(step, frames - i)
            p = ctypes.c_void_p(base + i * 4 * channels)
            if channels == 2:
                got = lame.lame_encode_buffer_interleaved_int(gfp, p, k, buf, len(buf))
            else:
                got = lame.lame_encode_buffer_int(gfp, p, None, k, buf, len(buf))
            if got < 0:
                raise CouldntEncodeError("lame failed with code %d" % got)
            out.append(buf.raw[:got])
        got = lame.lame_encode_flush(gfp, buf, len(buf))
        if got < 0:
            raise CouldntEncodeError("lame failed with code %d" % got)
        out.append(buf.raw[:got])
        mp3 = b"".join(out)

        # the first frame is a placeholder for the lame tag, it only knows the gapless info at the end
        n = lame.lame_get_lametag_frame(gfp, buf, ctypes.c_size_t(len(buf)))
        if n == 0:
            first = info_frame(rate, channels, lame.lame_get_frameNum(gfp), len(mp3),
                               lame.lame_get_encoder_delay(gfp), lame.lame_get_encoder_padding(gfp))
            return id3.raw + first + mp3
        return id3.raw + buf.raw[:n] + mp3[n:]
    finally:
        lame.lame_close(gfp)
