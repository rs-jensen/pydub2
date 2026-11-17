import argparse
import ast
import importlib.metadata
import importlib.util
import io
import json
import os
import platform
import re
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
    mp3 = _mp3.available()
    fixes = []
    if ffmpeg is None:
        fixes.append({"for": "m4a, aac, wma and video files" + (", and strict mode" if pydub2.strict else ""),
                      "run": FFMPEG_FIX if conv == "ffmpeg" else "put ffmpeg at " + conv + " or fix AudioSegment.converter"})
    elif not ffmpeg["ok"]:
        fixes.append({"for": "ffmpeg is older than %d.%d" % MIN_FFMPEG, "run": "install a newer ffmpeg"})
    if pydub2.strict and ffprobe is None:
        fixes.append({"for": "strict mode needs ffprobe", "run": "install ffmpeg with ffprobe"})
    if not mp3:
        fixes.append({"for": "mp3 export without ffmpeg", "run": 'pip install "pydub2[mp3]"'})
    return {
        "pydub2": pydub2.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "read_natively": ["wav", "flac", "ogg", "opus", "mp3"],
        "write_natively": ["wav", "flac", "ogg", "opus"] + (["mp3"] if mp3 else []),
        "pydub2_mp3": mp3,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "strict": bool(pydub2.strict),
        "pydub": pydub,
        "audioop": importlib.util.find_spec("audioop") is not None,
        "fixes": fixes,
        "ok": selftest() and not (ffmpeg and not ffmpeg["ok"]) and not (pydub2.strict and (ffmpeg is None or ffprobe is None)),
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
          ("installed" if r["pydub2_mp3"] else "not installed"))
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


def check_file(path):
    r = {"file": path, "format": None, "loads": False, "how": None, "reason": None, "hint": None, "note": None}
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
    except OSError as e:
        r.update(reason="cant_open", note="cant open: " + (e.strerror or "error"))
        return r
    r["format"] = utils.sniff(head)
    native = False
    try:
        try:
            d = _pydub2.Decoder(path)
            try:
                got = len(utils.read_capped(d, -1)) // (d.channels * d.width)
            finally:
                d.close()
            native = not pydub2.strict and not (d.chained and _ffmpeg.find(AudioSegment.converter))
        except RuntimeError:
            pass
        seg = AudioSegment.from_file(path)
    except Exception as e:
        reason = "playlist_refused" if r["format"] == "playlist" else getattr(e, "reason", None) or "cant_decode"
        r["reason"], r["hint"] = reason, getattr(e, "hint", None)
        what = r["format"] or "this file"
        if reason == "needs_ffmpeg" and r["hint"]:
            r["note"] = "%s needs ffmpeg, none found: %s" % (what, r["hint"])
        elif reason == "playlist_refused":
            r["note"] = "file is a playlist (hls, dash or concat): refused"
        elif reason == "too_long":
            r["note"] = "longer than max_duration: refused"
        else:
            lines = str(e).splitlines() or [type(e).__name__]
            r["note"] = ("cant decode %s: " % what if r["format"] else "not audio that anything here reads: ") + \
                (lines[-1] if lines[-1].startswith("ffmpeg said") else lines[0])
        return r
    r.update(loads=True, how="natively" if native else "through ffmpeg", duration=seg.duration_seconds,
             rate=seg.frame_rate, channels=seg.channels, sample_width=seg.sample_width)
    notes = []
    if native and r["format"] is None:
        m = re.search(rb"ID3|\xff[\xe0-\xff]", head)
        notes.append("mp3 starts with about %s of junk: ok" % ("%d bytes" % m.start() if m else "64 kB or more"))
    if native and d.frames and got < d.frames:
        r["reason"] = "truncated"
        notes.append("truncated, last %.2f s missing" % ((d.frames - got) / d.rate))
    r["note"] = ", ".join(notes) or None
    return r


def print_file(r):
    if r["loads"]:
        line = "%s: %s, loads %s, %.2f s, %d Hz, %d channel%s, %d bit" % (
            r["file"], r["format"] or "mp3", r["how"], r["duration"], r["rate"], r["channels"], "s"[:r["channels"] > 1],
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
    files, using, findings = 0, 0, []
    for root, dirs, names in os.walk(top):
        dirs[:] = [d for d in dirs if not skip(root, d)]
        for n in sorted(names):
            path = os.path.join(root, n)
            if not n.endswith(".py") or os.path.islink(path):
                continue
            files += 1
            try:
                if os.path.getsize(path) > 2 << 20:
                    continue
                with open(path, "rb") as f:
                    tree = ast.parse(f.read())
            except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
                continue
            imports, found = look(tree)
            if imports:
                using += 1
                findings += [dict(f, file=path) for f in sorted(found, key=lambda f: f["line"])]
    kinds = [f["kind"] for f in findings]
    return {"files": files, "files_using_pydub": using, "findings": findings,
            "need_change": kinds.count("import") + kinds.count("change"), "imports": kinds.count("import"),
            "strict_covers": kinds.count("strict"), "notes": kinds.count("note") + kinds.count("ffmpeg")}


def print_scan(r):
    for f in r["findings"]:
        print(clean("%s:%d: %s" % (f["file"], f["line"], f["text"])))
    print("%d of %d python files use pydub" % (r["files_using_pydub"], r["files"]))
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
