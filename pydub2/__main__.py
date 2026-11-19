import argparse
import ast
import codecs
import importlib.metadata
import importlib.util
import io
import json
import os
import platform
import re
import stat
import struct
import sys
import tokenize

import _pydub2
import pydub2
from . import _ffmpeg, _mp3, utils
from .utils import behind_id3, chunks, damaged, native_format, what
from .audio_segment import AudioSegment

MIN_FFMPEG = (3, 3)
FFMPEG_FIX = 'pip install "pydub2[ffmpeg]"'
SKIP_DIRS = {"venv", "node_modules", "build", "dist", "site-packages", "__pycache__"}
# formats pydub2 cant read or write by itself
NEEDS_FFMPEG = {"m4a", "mp4", "aac", "wma", "aiff", "aif", "flv", "3gp", "webm", "mkv", "mov", "avi", "amr", "ac3", "wmv"}
SAMPLES_DIFFER = {"mp3", "ogg", "opus"}
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
    conv = _ffmpeg.name_of(AudioSegment.converter)
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
    if r["pydub2_mp3"]:
        mp3 = "installed"
    elif any(f["for"].startswith("pydub2-mp3") for f in r["fixes"]):
        mp3 = "broken"
    else:
        mp3 = "not installed"
    print("writes without ffmpeg:", " ".join(r["write_natively"]), "(pydub2-mp3 %s)" % mp3)
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
    return "".join(c if c.isprintable() else "?" for c in text)


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


def is_text(head):
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head[:4096])
    except UnicodeDecodeError:
        return False
    return b"\0" not in head[:4096]


def mp3_missing(f, size, got, rate):
    # the xing or info frame that lame and ffmpeg write holds the length in bytes and frames
    f.seek(0)
    at, h = behind_id3(f, f.read(4096))
    m = re.search(rb"\xff[\xe0-\xff]", h)
    if m is None:
        return None
    s = m.start()
    tag = max(h.find(b"Xing", s, s + 64), h.find(b"Info", s, s + 64))
    if tag < 0 or len(h) < tag + 144:
        return None
    flags, frames, length = struct.unpack(">III", h[tag + 4:tag + 16])
    if flags & 3 != 3 or at + s + length <= size:
        return None
    total = frames * (1152 if h[s + 1] & 8 else 576)
    if flags & 12 == 12 and h[tag + 120:tag + 124].isalnum():
        # the lame tag, the decoder drops this delay and padding
        pad = h[tag + 141:tag + 144]
        total -= (pad[0] << 4 | pad[1] >> 4) + ((pad[1] & 15) << 8 | pad[2])
    return max(total - got, 0) / rate


def ogg_cut(f, size):
    # every page must be whole and the last one must end the stream
    at = flags = 0
    while at < size:
        f.seek(at)
        h = f.read(27)
        if len(h) < 27:
            return b"OggS".startswith(h[:4])
        if h[:4] != b"OggS":
            return False
        lace = f.read(h[26])
        at += 27 + h[26] + sum(lace)
        flags = h[5]
        if len(lace) < h[26]:
            return True
    return at > size or not flags & 4


def missing(f, path, fmt, seg):
    # seconds missing at the end, 0 when the length is not known, None when nothing is missing or there is no telling
    size = os.fstat(f.fileno()).st_size
    got = int(seg.frame_count())
    if fmt == "wav":
        rate = None
        for at, cid, n, data in chunks(f):
            if cid == b"fmt " and len(data) >= 12:
                rate = struct.unpack("<I", data[8:12])[0]
            # a writer that does not know the length leaves 0, 0xffffffff, 0x7ffff000 and up, or a size far past the end
            # rf64 keeps the size in ds64, which is not read here, so a cut rf64 is not seen
            if cid == b"data" and n and n < 0x7ffff000 and n <= 100 * size and at + 8 + n > size:
                return (at + 8 + n - size) / rate if rate else 0
    elif fmt == "flac":
        try:
            d = _pydub2.Decoder(path)
        except RuntimeError:
            return None
        return (d.frames - got) / d.rate if d.frames > got else None
    elif fmt == "mp3":
        return mp3_missing(f, size, got, seg.frame_rate)
    elif fmt in ("ogg", "opus") and ogg_cut(f, size):
        return 0
    return None


def check_file(path):
    r = {"file": path, "format": None, "loads": False, "how": None, "reason": None, "hint": None, "note": None}
    try:
        f = open_regular(path)
    except NotAFile as e:
        r.update(reason="not_a_file", note="is %s, not read" % e)
        return r
    except OSError as e:
        r.update(reason="cant_open", note="cant open: " + (e.strerror or "error"))
        return r
    with f:
        head = f.read(65536)
        r["format"] = fmt = what(f, head)
        if os.path.splitext(path)[1].lower() in (".raw", ".pcm"):
            r.update(reason="cant_decode", note="raw audio has no header, loading it needs sample_width, frame_rate and channels")
            return r
        # from_file puts the ffmpeg commands it runs in this list, which only this thread sees
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
        gone = missing(f, path, fmt, seg)
        if gone is not None:
            r["reason"] = "truncated"
            notes.append("truncated, last %.2f s missing" % gone if gone else "truncated, the end is missing")
    r["note"] = ", ".join(notes) or None
    return r


def failed(r, f, head, e):
    fmt = r["format"]
    reason = "playlist_refused" if fmt == "playlist" else getattr(e, "reason", None) or "cant_decode"
    hint = getattr(e, "hint", None)
    conv = _ffmpeg.name_of(AudioSegment.converter)
    if reason == "playlist_refused":
        note = "file is a playlist (hls, dash or concat): refused"
    elif reason == "too_long":
        note = "longer than max_duration: refused"
    elif reason == "too_big":
        note = "larger than max_bytes or max_file_size: refused"
    elif not head:
        reason, hint, note = "empty", None, "file is empty"
    elif is_text(head):
        reason, hint, note = "not_audio", None, "file is text, not audio"
    elif _ffmpeg.find(conv) is None and reason in ("needs_ffmpeg", "cant_decode"):
        if not pydub2.strict and damaged(f, head, fmt):
            reason, hint, note = "cant_decode", None, "cant decode %s, the file is damaged" % fmt
        else:
            reason, hint = "needs_ffmpeg", hint or (FFMPEG_FIX if conv == "ffmpeg" else "put ffmpeg at " + conv)
            note = "%s needs ffmpeg, none found: %s" % (fmt or "this file", hint)
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
    parts = []
    while isinstance(x, ast.Attribute):
        parts.append(x.attr)
        x = x.value
    if isinstance(x, ast.Name):
        return ".".join([x.id] + parts[::-1])


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
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and dotted(t) in ("audioop.error", "wave.Error"):
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


# 17 attributes in a row, also over line breaks, comments without a dot, and names that are not ascii
GAP = rb"(?:\s|\\|#[^\r\n.]*[\r\n])*"
LONG_CHAIN = re.compile(rb"(?:\." + GAP + rb"[\w\x80-\xff]+" + GAP + rb"){17}")


def parse(source):
    # deep nesting runs into a limit that depends on the platform: 1000 levels on Windows on arm with
    # Python 3.13, the size of the C stack on 3.14, and Python 3.9 and 3.10 crash on Windows where the
    # stack runs out. A long chain like a.b.c... is the usual cause, so those are cut short first
    short = short_chains(source) if LONG_CHAIN.search(source) else None
    if short is not None:
        return ast.parse(short)
    try:
        return ast.parse(source)
    except RecursionError:
        # a chain the search did not see
        short = short_chains(source)
        if short is None:
            raise
    return ast.parse(short)


def short_chains(source, keep=8):
    # cut each chain of more than 2 * keep attributes to its first name and last keep attributes,
    # a.b1.b2 ... .b99.AudioSegment.converter keeps a and the end, and every line keeps its number
    try:
        lines = io.StringIO(importlib.util.decode_source(source)).readlines()
        cuts, run, dot = [], [], None
        for tok in tokenize.generate_tokens(iter(lines + [""]).__next__):
            if tok.type in (tokenize.NL, tokenize.COMMENT):
                continue
            if dot is not None and tok.type == tokenize.NAME:
                run.append((dot, tok.end))
                dot = None
                continue
            dot = None
            if tok.type == tokenize.OP and tok.string == ".":
                dot = tok.start
                continue
            if len(run) > 2 * keep:
                cuts.append((run[0][0], run[-keep - 1][1]))
            run = []
    except (SyntaxError, tokenize.TokenError, ValueError):
        return None
    if not cuts:
        return None
    for (r1, c1), (r2, c2) in reversed(cuts):
        # a backslash for each line break that was cut keeps the lines after it where they were
        lines[r1 - 1:r2] = [lines[r1 - 1][:c1] + " \\\n" * (r2 - r1) + lines[r2 - 1][c2:]]
    return "".join(lines)


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
                    tree = parse(f.read())
            except SyntaxError as e:
                cant(path, "cant parse line %s: %s" % (e.lineno, e.msg))
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m pydub2")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="check this install, some files, or a project")
    d.add_argument("files", nargs="*")
    d.add_argument("--scan", metavar="DIR", help="list what in the python files under DIR behaves differently in pydub2")
    d.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.scan and a.files:
        p.error("use --scan or files, not both")
    # a file name the terminal cant show must not end in a traceback
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        return doctor(a)
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, e)
        if a.json:
            print(json.dumps({"error": error}))
        else:
            print(clean("doctor: " + error), file=sys.stderr)
        return 2


def doctor(a):
    if a.scan:
        r = scan(a.scan)
        if a.json:
            print(json.dumps(r, indent=1))
        else:
            print_scan(r)
        return 0
    if a.files:
        rs = [check_file(f) for f in a.files]
        if a.json:
            print(json.dumps(rs, indent=1))
        else:
            for r in rs:
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
