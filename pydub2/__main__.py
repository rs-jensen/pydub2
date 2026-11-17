import argparse
import ast
import codecs
import contextlib
import importlib.metadata
import importlib.util
import io
import json
import multiprocessing
import os
import platform
import re
import signal
import stat
import struct
import sys

import _pydub2
import pydub2
from . import _ffmpeg, _mp3, utils
from .audio_segment import AudioSegment

MIN_FFMPEG = (3, 3)
FFMPEG_FIX = 'pip install "pydub2[ffmpeg]"'
SKIP_DIRS = {"venv", "node_modules", "build", "dist", "site-packages", "__pycache__"}
# formats pydub2 cant read or write by itself
NEEDS_FFMPEG = {"m4a", "mp4", "aac", "wma", "aiff", "aif", "flv", "3gp", "webm", "mkv", "mov", "avi", "amr", "ac3", "wmv"}
SAMPLES_DIFFER = {"mp3", "ogg", "opus"}
# starts that utils.sniff does not know, only ffmpeg reads these
MAGIC = [(b"\x0b\x77", "ac3"), (b".snd", "au"), (b"caff", "caf"), (b"riff\x2e\x91\xcf\x11", "w64"), (b"wvpk", "wv"),
         (b"#!AMR", "amr"), (b"MAC ", "ape"), (b"TTA1", "tta"), (b"MPCK", "mpc"), (b"\x7f\xfe\x80\x01", "dts"),
         (b"FLV", "flv"), (b"Creative Voice File", "voc"), (b"MThd", "midi")]
KINDS = [(stat.S_ISDIR, "a directory"), (stat.S_ISFIFO, "a fifo"), (stat.S_ISSOCK, "a socket"),
         (stat.S_ISCHR, "a device"), (stat.S_ISBLK, "a device"), (stat.S_ISLNK, "a symlink")]


def tool(exe):
    if exe is None:
        return None
    try:
        line = utils.run([exe, "-version"]).stdout.decode("utf-8", "ignore").splitlines()[0]
    except (OSError, TimeoutError, IndexError):
        return {"path": exe, "version": None, "ok": False}
    version = (re.search(r"version (\S+)", line) or [0, None])[1]
    num = re.match(r"n?(\d+)\.(\d+)", version or "")
    # a git build has no number, call it fine
    return {"path": exe, "version": version, "ok": not num or tuple(map(int, num.groups())) >= MIN_FFMPEG}


def selftest():
    try:
        seg = AudioSegment.silent(100)
        return _pydub2.Decoder(seg.export(io.BytesIO(), format="flac").read()).frames == seg.frame_count()
    except Exception:
        return False


def report():
    conv = os.fspath(AudioSegment.converter)
    exe = _ffmpeg.find(conv)
    probe = utils.which(utils.get_prober_name())
    if conv != "ffmpeg":
        where = "AudioSegment.converter"
    elif utils.which("ffmpeg") or utils.which("avconv"):
        where = "PATH"
    else:
        where = "the ffmpeg extra"
    ffmpeg = tool(exe)
    if ffmpeg:
        ffmpeg["from"] = where
    ffprobe = tool(probe)
    if ffprobe:
        ffprobe["from"] = "PATH" if utils.which("ffprobe") or utils.which("avprobe") else "next to ffmpeg"
    spec = importlib.util.find_spec("pydub")
    if spec is None:
        pydub = {"resolves_to": None, "version": None}
    elif type(spec.loader).__name__ == "Alias":
        pydub = {"resolves_to": "pydub2", "version": pydub2.__version__}
    else:
        try:
            version = importlib.metadata.version("pydub")
        except importlib.metadata.PackageNotFoundError:
            version = None
        pydub = {"resolves_to": "pydub", "version": version}
    try:
        mp3, broken = _mp3.available(), None
    except Exception as e:
        # pydub2_mp3 imports but its library does not load, mp3 export raises the same
        mp3, broken = False, str(e) or type(e).__name__
    fixes = []
    if ffmpeg is None:
        fixes.append({"for": "m4a, aac, wma and video files" + (", and strict mode" if pydub2.strict else ""),
                      "run": FFMPEG_FIX if conv == "ffmpeg" else "put ffmpeg at " + conv + " or fix AudioSegment.converter"})
    elif not ffmpeg["ok"]:
        fixes.append({"for": "ffmpeg is older than %d.%d" % MIN_FFMPEG, "run": "install a newer ffmpeg"})
    if pydub2.strict and ffprobe is None:
        fixes.append({"for": "strict mode needs ffprobe", "run": "install ffmpeg with ffprobe"})
    if broken:
        fixes.append({"for": "pydub2-mp3 is installed but does not load (%s)" % broken,
                      "run": "pip install --force-reinstall pydub2-mp3"})
    elif not mp3:
        fixes.append({"for": "mp3 export without ffmpeg", "run": 'pip install "pydub2[mp3]"'})
    return {
        "pydub2": pydub2.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        # strict mode reads and writes all but wav through ffmpeg
        "read_natively": ["wav"] if pydub2.strict else ["wav", "flac", "ogg", "opus", "mp3"],
        "write_natively": ["wav"] if pydub2.strict else ["wav", "flac", "ogg", "opus"] + (["mp3"] if mp3 else []),
        "pydub2_mp3": mp3,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "strict": bool(pydub2.strict),
        "pydub": pydub,
        "audioop": importlib.util.find_spec("audioop") is not None,
        "fixes": fixes,
        "ok": selftest() and not broken and not (ffmpeg and not ffmpeg["ok"]) and not (pydub2.strict and (ffmpeg is None or ffprobe is None)),
    }


def show_tool(name, t):
    if t is None:
        return name + ": none found"
    return "%s: %s, %s, from %s%s" % (name, t["path"], t["version"] or "version unknown", t["from"],
                                       "" if t["ok"] else ", too old, needs %d.%d" % MIN_FFMPEG)


def print_report(r):
    print("pydub2", r["pydub2"])
    print("python %s on %s" % (r["python"], r["platform"]))
    print("reads without ffmpeg:", " ".join(r["read_natively"]))
    print("writes without ffmpeg:", " ".join(r["write_natively"]), "(pydub2-mp3 %s)" %
          ("installed" if r["pydub2_mp3"] else "broken" if any(f["for"].startswith("pydub2-mp3") for f in r["fixes"])
           else "not installed"))
    print(show_tool("ffmpeg", r["ffmpeg"]))
    print(show_tool("ffprobe", r["ffprobe"]))
    print("strict mode:", "on" if r["strict"] else "off")
    p = r["pydub"]
    print("import pydub:", "not installed" if not p["resolves_to"] else "%s %s%s" % (
        "real pydub" if p["resolves_to"] == "pydub" else "pydub2", p["version"] or "",
        " (compat package)" if p["resolves_to"] == "pydub2" else ""))
    print("audioop:", "present" if r["audioop"] else "not present, pydub2 does not need it")
    for f in r["fixes"]:
        print("missing, %s: %s" % (f["for"], f["run"]))
    print("ok" if r["ok"] else "not ok")


def clean(text):
    return text if text.isprintable() else "".join(c if c.isprintable() else "?" for c in text)


class NotAFile(Exception):
    pass


def open_regular(path, follow=True):
    # look before opening, opening a fifo blocks and opening a device can do things
    mode = (os.stat if follow else os.lstat)(path).st_mode
    if stat.S_ISREG(mode):
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags | (0 if follow else getattr(os, "O_NOFOLLOW", 0)))
        # it can have been swapped since the stat
        mode = os.fstat(fd).st_mode
        if stat.S_ISREG(mode):
            return os.fdopen(fd, "rb")
        os.close(fd)
    raise NotAFile(next((name for test, name in KINDS if test(mode)), "an unknown kind of file"))


def behind_id3(f, head):
    # an id3 tag can sit in front of any format
    at = 0
    for _ in range(16):
        if head[:3] != b"ID3" or len(head) < 10:
            break
        at += 10 + (head[6] << 21 | head[7] << 14 | head[8] << 7 | head[9]) + (10 if head[5] & 0x10 else 0)
        f.seek(at)
        head = f.read(4096)
    return at, head


KBPS = [[0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
        [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
        [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
        [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
        [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]]


def frame(h):
    # the length of the mpeg audio or adts frame that h starts with, 0 when it starts with none
    if len(h) < 7 or h[0] != 0xff or h[1] & 0xe0 != 0xe0:
        return 0
    version, layer = h[1] >> 3 & 3, 4 - (h[1] >> 1 & 3)
    if layer == 4:
        return ((h[3] & 3) << 11 | h[4] << 3 | h[5] >> 5) if h[1] & 0xf0 == 0xf0 and (h[2] >> 2 & 15) < 13 else 0
    if version == 1 or h[2] >> 4 == 15 or h[2] >> 2 & 3 == 3:
        return 0
    rate = [44100, 48000, 32000][h[2] >> 2 & 3] >> [2, 0, 1, 0][version]
    kbps = KBPS[layer - 1 if version == 3 else 3 if layer == 1 else 4][h[2] >> 4]
    pad = h[2] >> 1 & 1
    if layer == 1:
        return (12000 * kbps // rate + pad) * 4
    return (144000 if version == 3 or layer == 2 else 72000) * kbps // rate + pad


def what(f, head):
    # the format by its first bytes, None when not known
    h = behind_id3(f, head)[1]
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "avi"
    fmt = utils.sniff(h) or next((name for magic, name in MAGIC if h.startswith(magic)), None)
    if fmt in ("mp3", "aac") and h[:1] == b"\xff":
        # two bytes are too little to go by, a second frame must follow the first
        n = frame(h)
        if n < 7 or len(h) < n + 2 or h[n] != 0xff or (h[n + 1] ^ h[1]) & 0xfe:
            return None
        return "aac" if fmt == "aac" else {2: "mp3", 4: "mp2", 6: "mp1"}[h[1] & 6]
    if fmt == "mp4" and h[8:12] == b"qt  ":
        return "mov"
    if fmt == "mp4" and h[8:10] == b"3g":
        return "3gp"
    if fmt == "ac3" and len(h) > 5 and h[5] >> 3 > 10:
        return "eac3"
    if fmt == "webm" and b"webm" not in h[:64]:
        return "matroska"
    return fmt


def native_format(head):
    # the choice detect() in src/decode.cpp makes, all else goes to the mp3 decoder
    if head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    packet = head[27 + head[26]:] if head[:4] == b"OggS" and len(head) > 27 else b""
    return "opus" if packet.startswith(b"OpusHead") else "ogg" if packet.startswith(b"\x01vorbis") else "mp3"


def is_text(head):
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head[:4096])
    except UnicodeDecodeError:
        return False
    return b"\0" not in head[:4096]


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
        # ffmpeg joins the streams of a chained file, the built in decoder does not always
        return not ogg_walk(f, os.fstat(f.fileno()).st_size)[1]
    if fmt != "wav":
        return True
    tag = next((data[:2] if data[:2] != b"\xfe\xff" else data[24:26] for _, cid, n, data in chunks(f) if cid == b"fmt "), b"")
    # no fmt chunk, or pcm or float which pydub2 reads itself
    return len(tag) < 2 or struct.unpack("<H", tag)[0] in (1, 3)


def mp3_missing(f, size, got, rate):
    # the xing or info frame that lame and ffmpeg write holds the length in bytes and frames
    f.seek(0)
    at, h = behind_id3(f, f.read(4096))
    m = re.search(rb"\xff[\xe0-\xff]", h)
    if m is None:
        return None
    s = m.start()
    x = max(h.find(b"Xing", s, s + 64), h.find(b"Info", s, s + 64))
    if x < 0 or len(h) < x + 144:
        return None
    flags, frames, length = struct.unpack(">III", h[x + 4:x + 16])
    if flags & 3 != 3 or at + s + length <= size:
        return None
    total = frames * (1152 if h[s + 1] & 8 else 576)
    if flags & 12 == 12 and h[x + 120:x + 124].isalnum():
        # the lame tag, the decoder drops this delay and padding
        g = h[x + 141:x + 144]
        total -= (g[0] << 4 | g[1] >> 4) + ((g[1] & 15) << 8 | g[2])
    return max(total - got, 0) / rate


def ogg_walk(f, size):
    # cut and chained. every page must be whole and the last one must end the stream,
    # and a stream that starts after the first one has pages makes the file chained
    at = flags = 0
    chained = False
    while at < size:
        f.seek(at)
        h = f.read(27)
        if len(h) < 27:
            return b"OggS".startswith(h[:4]), chained
        if h[:4] != b"OggS":
            return False, chained
        lace = f.read(h[26])
        chained = chained or bool(at and h[5] & 2 and not flags & 2)
        at += 27 + h[26] + sum(lace)
        flags = h[5]
        if len(lace) < h[26]:
            return True, chained
    return at > size or not flags & 4, chained


def missing(f, path, size, fmt, seg):
    # seconds missing at the end, 0 when the length is not known, None when nothing is missing or there is no telling
    got = int(seg.frame_count())
    if fmt == "wav":
        rate, align = None, 1
        for at, cid, n, data in chunks(f):
            if cid == b"fmt " and len(data) >= 14:
                rate, align = struct.unpack("<IH", data[8:14])
            # shortcut: rf64 keeps the size in ds64, its truncation is not seen, read ds64 if it matters
            # a size that is less than a frame too large is a sloppy writer, no sound is missing
            if cid == b"data" and n not in (0, 0xffffffff) and at + 8 + n - size >= max(align, 1):
                return (at + 8 + n - size) / rate if rate else 0
    elif fmt == "flac":
        try:
            d = _pydub2.Decoder(path)
        except RuntimeError:
            return None
        return (d.frames - got) / d.rate if d.frames > got else None
    elif fmt == "mp3":
        return mp3_missing(f, size, got, seg.frame_rate)
    elif fmt in ("ogg", "opus") and ogg_walk(f, size)[0]:
        return 0
    return None


def result(path, **more):
    return dict({"file": path, "format": None, "loads": False, "how": None, "reason": None, "hint": None, "note": None}, **more)


def check_file(path):
    r = result(path)
    try:
        f = open_regular(path)
    except NotAFile as e:
        r.update(reason="not_a_file", note="is %s, not read" % e)
        return r
    except (OSError, ValueError) as e:
        # a name with a nul in it is a ValueError
        r.update(reason="cant_open", note="cant open: " + (getattr(e, "strerror", None) or str(e) or "error"))
        return r
    with f:
        head = f.read(65536)
        size = os.fstat(f.fileno()).st_size
        r["format"] = fmt = what(f, head)
        # ask whether from_file ran ffmpeg, the list is seen by this thread only and nothing is patched
        used = []
        token = _ffmpeg._seen.set(used)
        try:
            seg = AudioSegment.from_file(path)
        except Exception as e:
            failed(r, f, head, e)
            return r
        finally:
            _ffmpeg._seen.reset(token)
        native = not used
        if native:
            r["format"] = fmt = native_format(head)
        r.update(loads=True, how="natively" if native else "through ffmpeg", duration=seg.duration_seconds,
                 rate=seg.frame_rate, channels=seg.channels, sample_width=seg.sample_width)
        notes = []
        if native and fmt == "mp3" and utils.sniff(head) is None:
            m = re.search(rb"ID3|\xff[\xe0-\xff]", head)
            notes.append("mp3 starts with about %s of junk: ok" % ("%d bytes" % m.start() if m else "64 kB or more"))
        gone = missing(f, path, size, fmt, seg)
        if gone is not None:
            r["reason"] = "truncated"
            notes.append("truncated, last %.2f s missing" % gone if gone else "truncated, the end is missing")
    r["note"] = ", ".join(notes) or None
    return r


def failed(r, f, head, e):
    fmt = r["format"]
    reason = "playlist_refused" if fmt == "playlist" else getattr(e, "reason", None) or "cant_decode"
    hint = getattr(e, "hint", None)
    conv = os.fspath(AudioSegment.converter)
    if reason == "playlist_refused":
        note = "file is a playlist (hls, dash or concat): refused"
    elif reason == "too_long":
        note = "longer than max_duration: refused"
    elif not head:
        reason, hint, note = "empty", None, "file is empty"
    elif is_text(head):
        reason, hint, note = "not_audio", None, "file is text, not audio"
    elif fmt == "midi":
        reason, hint, note = "not_audio", None, "midi holds notes and no sound, ffmpeg does not read it either"
    elif _ffmpeg.find(conv) is None and reason in ("needs_ffmpeg", "cant_decode"):
        if not pydub2.strict and damaged(f, head, fmt):
            reason, hint, note = "cant_decode", None, "cant decode %s, the file is damaged" % fmt
        else:
            reason, hint = "needs_ffmpeg", hint or (FFMPEG_FIX if conv == "ffmpeg" else "put ffmpeg at " + conv)
            note = "%s needs ffmpeg, none found: %s" % (fmt, hint) if fmt else \
                "format not known, pydub2 does not read it by itself and there is no ffmpeg to try it with: " + hint
    else:
        lines = str(e).splitlines() or [type(e).__name__]
        note = ("cant decode %s: " % fmt if fmt else "not audio that anything here reads: ") + \
            (lines[-1] if lines[-1].startswith("ffmpeg said") else lines[0])
    r.update(reason=reason, hint=hint, note=note)


def print_file(r):
    if r["loads"]:
        line = "%s: %s, loads %s, %.2f s, %d Hz, %d channel%s, %d bit" % (
            r["file"], r["format"] or "unknown", r["how"], r["duration"], r["rate"], r["channels"], "s"[:r["channels"] > 1],
            r["sample_width"] * 8)
        print(clean(line + (", " + r["note"] if r["note"] else "")))
    else:
        print(clean("%s: %s" % (r["file"], r["note"])))


def dotted(x):
    # the name an expression spells, like pydub.utils.mediainfo
    if isinstance(x, ast.Name):
        return x.id
    if isinstance(x, ast.Attribute):
        left = dotted(x.value)
        return left + "." + x.attr if left else None


def arg(call, pos, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return call.args[pos] if pos is not None and len(call.args) > pos else None


def text(node):
    return node.value.lower() if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def look(tree):
    imports, found = [], []

    def add(node, kind, msg):
        found.append({"line": node.lineno, "kind": kind, "text": msg})

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            for n in names:
                if n == "pydub" or n.startswith("pydub."):
                    imports.append(node)
                    if n.endswith((".pyaudioop", ".audioop")) or (n == "pydub" and any(
                            a.name in ("pyaudioop", "audioop") for a in getattr(node, "names", []))):
                        add(node, "change", "pydub2 has no pydub.audioop, it does not use audioop")
                    else:
                        add(node, "import", "imports pydub, change it to pydub2 or install pydub2-compat")
                    if n == "pydub.playback":
                        add(node, "note", "playback is there as in pydub, it needs simpleaudio, PyAudio or ffplay")
                if n.split(".")[0] in ("audioop", "pyaudioop", "audioop_lts"):
                    add(node, "note", "pydub2 does not need audioop, this import still does (Python 3.12 or older, or audioop-lts)")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if not isinstance(t, ast.Attribute):
                    continue
                base = dotted(t.value) or ""
                if t.attr == "converter" and base.endswith("AudioSegment"):
                    add(node, "note", "converter works as before, it is only used for ffmpeg")
                elif t.attr in ("ffmpeg", "ffprobe") and base.endswith("AudioSegment"):
                    add(node, "change", "AudioSegment.%s does nothing, in pydub too, set AudioSegment.converter" % t.attr)
                elif t.attr in ("get_prober_name", "get_encoder_name", "get_player_name") and base.endswith("utils"):
                    add(node, "note", "pydub2 asks %s only for mediainfo and in strict mode, the patch works on pydub2.utils" % t.attr)
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            for t in ast.walk(node.type):
                if dotted(t) in ("audioop.error", "wave.Error"):
                    add(node, "change", "pydub2 raises ValueError where pydub raised " + dotted(t))
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in ("from_file", "from_mp3", "from_ogg", "from_wav", "from_flv", "export"):
                call(node, name, add)
            elif name in ("mediainfo", "mediainfo_json"):
                add(node, "note", "without ffprobe this gives 15 of the 37 keys, and only for wav, flac, ogg, opus and mp3")
            elif name in ("patch", "setattr") and node.args and (text(node.args[0]) or "").startswith("pydub."):
                add(node, "change", "patches " + text(node.args[0]) + ", patch the pydub2 name or install pydub2-compat")
    return imports, found


def call(node, name, add):
    out = name == "export"
    first = text(node.args[0]) if node.args else None
    fmt = text(arg(node, 1, "format")) if name in ("from_file", "export") else name[5:]
    if fmt is None and first and "." in first and "://" not in first:
        fmt = first.rsplit(".", 1)[1]
    if first and first.startswith(("http://", "https://")) and not out:
        add(node, "change", "pydub2 does not fetch a URL, it looks for a file with that name")
    if fmt in NEEDS_FFMPEG:
        add(node, "ffmpeg", "%s needs ffmpeg (pydub does too), pip install \"pydub2[ffmpeg]\" if none is installed" % fmt)
        if out and fmt in ("m4a", "aac"):
            add(node, "strict", "export to %s works here, pydub raises CouldntEncodeError, strict mode does as pydub" % fmt)
    elif fmt in SAMPLES_DIFFER and not out:
        add(node, "strict", "%s samples can differ a little from pydub, strict mode gives pydub's" % fmt)
    elif fmt == "mp3" and out:
        add(node, "note", "mp3 export needs ffmpeg or pip install \"pydub2[mp3]\"")
    for key in ("parameters", "codec"):
        if arg(node, None, key) is not None:
            goes = "goes to ffmpeg" + ("" if fmt in ("wav", "flac") else ", so ffmpeg must be installed")
            if key == "parameters":
                add(node, "strict", "parameters " + goes + ", here before the output file where they take effect, pydub puts them after it and strict mode does too")
            else:
                add(node, "ffmpeg", "codec " + goes)


def skip(root, d):
    return d in SKIP_DIRS or d.startswith(".") or os.path.exists(os.path.join(root, d, "pyvenv.cfg"))


def scan(top):
    files, using, findings, unread = 0, 0, [], []

    def cant(path, why):
        unread.append({"file": path, "why": why})

    if os.path.isdir(top):
        walk = os.walk(top, onerror=lambda e: cant(e.filename, "cant list: " + (e.strerror or "error")))
    elif os.path.isfile(top):
        walk = [(os.path.dirname(top), [], [os.path.basename(top)])]
    else:
        raise FileNotFoundError("no directory or file " + top)
    for root, dirs, names in walk:
        dirs[:] = sorted(d for d in dirs if not skip(root, d))
        for n in sorted(names):
            path = os.path.join(root, n)
            if not n.endswith(".py") or os.path.islink(path):
                continue
            files += 1
            try:
                if os.lstat(path).st_size > 2 << 20:
                    cant(path, "larger than 2 MB")
                    continue
                with open_regular(path, follow=False) as f:
                    tree = ast.parse(f.read())
            except SyntaxError as e:
                cant(path, "cant parse%s: %s" % (" line %d" % e.lineno if e.lineno else "", e.msg))
                continue
            except NotAFile as e:
                cant(path, "is %s, not read" % e)
                continue
            except OSError as e:
                cant(path, "cant open: " + (e.strerror or "error"))
                continue
            except (ValueError, RecursionError, MemoryError) as e:
                cant(path, "cant parse: " + (str(e) or type(e).__name__))
                continue
            imports, found = look(tree)
            if imports:
                using += 1
                findings += [dict(f, file=path) for f in sorted(found, key=lambda f: f["line"])]
    kinds = [f["kind"] for f in findings]
    return {"files": files, "files_using_pydub": using, "findings": findings, "unreadable": unread,
            "need_change": kinds.count("import") + kinds.count("change"), "imports": kinds.count("import"),
            "strict_covers": kinds.count("strict"), "notes": kinds.count("note") + kinds.count("ffmpeg")}


def print_scan(r):
    for f in r["findings"]:
        print(clean("%s:%d: %s" % (f["file"], f["line"], f["text"])))
    for u in r["unreadable"]:
        print(clean("%s: %s" % (u["file"], u["why"])))
    print("%d of %d python files use pydub" % (r["files_using_pydub"], r["files"]) +
          (", %d could not be read and are not counted" % len(r["unreadable"]) if r["unreadable"] else ""))
    print("need a change: %d, of which %d are imports that pydub2-compat covers" % (r["need_change"], r["imports"]))
    print("strict mode covers %d more, %d are notes or need ffmpeg as before" % (r["strict_covers"], r["notes"]))


def check_safely(path):
    try:
        return check_file(path)
    except Exception as e:
        return result(path, reason="doctor_failed", note="doctor failed on this file: %s: %s" % (type(e).__name__, e))


def worker(conn, state):
    pydub2.strict, pydub2.max_duration, pydub2.ffmpeg_timeout, AudioSegment.converter = state
    if hasattr(os, "setsid"):
        # its own group, so an ffmpeg it started dies with it
        os.setsid()
    try:
        for path in iter(conn.recv, None):
            conn.send(check_safely(path))
    except (EOFError, OSError, KeyboardInterrupt):
        pass


def stop(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        # shortcut: on windows an ffmpeg the worker started lives on until it is done, kill the tree if that matters
        p.kill()
    p.join()


def check_files(paths, timeout=60):
    # in another process, so a file that hangs or crashes the decoder costs its own line and not the run
    # under python -m this module is called __main__, the new process finds the worker by its real name
    from pydub2 import __main__ as here
    ctx = multiprocessing.get_context("spawn")
    state = (pydub2.strict, pydub2.max_duration, pydub2.ffmpeg_timeout, AudioSegment.converter)
    p, rs = None, []
    try:
        for path in paths:
            if p is None:
                conn, there = ctx.Pipe()
                p = ctx.Process(target=here.worker, args=(there, state), daemon=True)
                p.start()
                there.close()
            try:
                conn.send(path)
                r = conn.recv() if conn.poll(timeout) else result(
                    path, reason="timeout", note="not done after %g s, stopped. --timeout sets the limit" % timeout)
            except (EOFError, OSError):
                r = result(path, reason="doctor_failed", note="the check of this file crashed")
            if r["reason"] in ("timeout", "doctor_failed"):
                stop(p)
                p = None
            rs.append(r)
    finally:
        if p is not None:
            stop(p)
    return rs


CODES = """exit codes:
  0  the install works, or every file loads. --scan gives 0 whatever it finds
  1  the install does not work, or a file does not load
  2  doctor could not do its work, or was called wrongly
"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m pydub2")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="check this install, some files, or a project", epilog=CODES,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    d.add_argument("files", nargs="*", help="audio files to check, each gets its own line")
    d.add_argument("--scan", metavar="DIR", help="list what in the python files under DIR behaves differently in pydub2")
    d.add_argument("--json", action="store_true", help="print JSON")
    d.add_argument("--timeout", metavar="SECONDS", type=float, default=60, help="give up on a file after this long, 60 by default")
    a = p.parse_args(argv)
    if a.scan is not None and a.files:
        p.error("use --scan or files, not both")
    if not a.timeout > 0:
        p.error("--timeout must be more than 0")
    # a file name the terminal cant show must not end in a traceback
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = doctor(a)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, e)
        code, out = 2, io.StringIO()
        if a.json:
            print(json.dumps({"error": error}), file=out)
        else:
            print(clean("doctor: " + error), file=sys.stderr)
    # all at once and flushed here, so a reader that left or a full disk is met here and not at exit
    try:
        if sys.stdout is not None:
            sys.stdout.write(out.getvalue())
            sys.stdout.flush()
    except BrokenPipeError:
        # head or a pager took what it wanted, python must not complain when it closes stdout
        with contextlib.suppress(OSError, ValueError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError) as e:
        with contextlib.suppress(Exception):
            print(clean("doctor: cant write the result: %s" % e), file=sys.stderr)
        return 2
    return code


def doctor(a):
    if a.scan is not None:
        r = scan(a.scan)
        if a.json:
            print(json.dumps(r, indent=1))
        else:
            print_scan(r)
        return 0
    if a.files:
        rs = check_files(a.files, a.timeout)
        if a.json:
            print(json.dumps(rs, indent=1))
        for r in rs:
            if not a.json:
                print_file(r)
        return 0 if all(r["loads"] for r in rs) else 1
    r = report()
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print_report(r)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
