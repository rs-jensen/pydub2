import base64
import io
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import time
import zlib

import pytest

import _pydub2
from pydub2 import AudioSegment, _ffmpeg, utils as utils2

pydub = pytest.importorskip("pydub")
need_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                 reason="needs ffmpeg and ffprobe for the oracle")

sets = {
    "common": {"title": "T", "artist": "A", "album": "Al", "album_artist": "AA", "date": "2020", "year": "2021",
               "track": "3", "genre": "G", "comment": "C", "composer": "Co", "disc": "1"},
    "mixed case": {"Title": "T", "ARTIST": "A", "aLbUm_ArTiSt": "AA", "Date": "2020"},
    "unknown": {"foo": "bar", "x_y": "z", "encoder": "mine", "ENCODER": "m2", "vendor": "v"},
    "non ascii": {"title": "Ål æø 日本語 \U0001F600", "artist": "é"},
    "empty": {"title": "", "artist": "A"},
    "long": {"title": "x" * 100000, "comment": "y" * 300},
    "equals": {"a=b": "c", "t": "v=w", "=": "v"},
    "newlines": {"a\nb": "c", "title": "l1\nl2\r\nl3"},
    "spaces": {"my key": "v", " lead": " x ", "tab\tk": "v", "k~": "v", "k}": "v"},
    "twice": {"title": "1", "TITLE": "2", "Album": "x", "album": ""},
    "renamed": {"tracknumber": "1", "discnumber": "2", "albumartist": "x", "album artist": "y", "description": "d"},
    "renamed twice": {"Track": "1", "DISC": "2", "album_artist": "x", "COMMENT": "c"},
    "not text": {"track": 3, "x": None, "y": 1.5},
    "nothing": {},
    "no key": {"": "v", "a": "b"},
    "bytes": {"title": "a\udc80b"},
}
formats = ["flac", "ogg", "opus"]


def windows_cant(name):
    return sys.platform == "win32" and name in ("long", "bytes")


CANT = "a windows command line holds 32k and no lone surrogate, so the oracle cant do this"


def raw():
    return bytes(range(256)) * 400


def ours():
    return AudioSegment(raw(), sample_width=2, frame_rate=22050, channels=1)


def theirs():
    return pydub.AudioSegment(raw(), sample_width=2, frame_rate=22050, channels=1)


def probe(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True)
    return where(json.loads(p.stdout))


def where(info):
    return [list(info["format"].get("tags", {}).items()), list(info["streams"][0].get("tags", {}).items())]


def masked(tags):
    return [[(k, "x" if k == "encoder" else v) for k, v in part] for part in tags]


def no_ffprobe(monkeypatch):
    which = utils2.which
    monkeypatch.setattr(utils2, "which", lambda p: None if os.path.basename(p) in ("ffprobe", "avprobe") else which(p))


@need_ffmpeg
@pytest.mark.parametrize("name", sets)
@pytest.mark.parametrize("format", formats)
def test_tags_like_pydub(tmp_path, monkeypatch, name, format):
    if windows_cant(name):
        pytest.skip(CANT)
    tags = sets[name]
    a, b, plain = (str(tmp_path / (n + "." + format)) for n in "abc")
    theirs().export(a, format=format, tags=tags)
    ours().export(plain, format=format)
    monkeypatch.setattr(_ffmpeg, "from_wav", None)
    ours().export(b, format=format, tags=tags)
    assert masked(probe(b)) == masked(probe(a))
    assert AudioSegment.from_file(b).raw_data == AudioSegment.from_file(plain).raw_data


@need_ffmpeg
@pytest.mark.parametrize("format", formats)
def test_tag_errors_like_pydub(tmp_path, format):
    for tags in (["x"], {"a": "b\0c"}, {"a\0": "b"}):
        with pytest.raises(Exception) as want:
            theirs().export(str(tmp_path / "a"), format=format, tags=tags)
        with pytest.raises(Exception) as got:
            ours().export(str(tmp_path / "b"), format=format, tags=tags)
        assert got.type.__name__ == want.type.__name__


def test_wav_keeps_dropping_tags():
    s = ours()
    assert s.export(format="wav", tags={"title": "T"}).read() == s.export(format="wav").read()


@need_ffmpeg
@pytest.mark.parametrize("name", sets)
@pytest.mark.parametrize("format", formats + ["wav"])
def test_mediainfo_tags_without_ffprobe(tmp_path, monkeypatch, name, format):
    if windows_cant(name):
        pytest.skip(CANT)
    # wav only gets tags from ffmpeg, with a codec
    kw = {"codec": "pcm_s16le"} if format == "wav" else {}
    paths = [str(tmp_path / (n + "." + format)) for n in "ab"]
    theirs().export(paths[0], format=format, tags=sets[name], **kw).close()
    ours().export(paths[1], format=format, tags=sets[name], **kw).close()
    want = [probe(p) for p in paths]
    no_ffprobe(monkeypatch)
    for p, w in zip(paths, want):
        assert where(utils2.mediainfo_json(p)) == w
        assert utils2.mediainfo(p).get("TAG", {}) == dict(w[0] + w[1])


others = [
    [(b"Title", b"1"), (b"TITLE", b"2")],
    [(b"title", b"1"), (b"x", b"y"), (b"title", b"2"), (b"Title", b"3"), (b"z", b"w")],
    [(b"a", b"1"), (b"b", b"2"), (b"c", b"3"), (b"a", b"4"), (b"d", b"5")],
    [(b"ALBUMARTIST", b"1"), (b"album_artist", b"2")],
    [(b"ALBUMARTIST", b"1"), (b"ALBUMARTIST", b"2"), (b"x", b"y")],
    [(b"nokey", b""), (b"ARTIST", b"a"), (b"TrackNumber", b"7"), (b"description", b"d")],
    [(b"x", b"\xff\xfe"), (b"y", b"\xc3")],
    [(b"title", b"t"), (b"Metadata_Block_Picture", b"AAAA"), (b"artist", b"a")],
]


@need_ffmpeg
@pytest.mark.parametrize("tags", others)
@pytest.mark.parametrize("format", formats)
def test_tags_of_other_writers(tmp_path, monkeypatch, format, tags):
    p = tmp_path / ("a." + format)
    p.write_bytes(_pydub2.encode(raw(), 22050, 1, 2, format, 0, tags))
    want = probe(p)
    no_ffprobe(monkeypatch)
    assert where(utils2.mediainfo_json(str(p))) == want


@need_ffmpeg
@pytest.mark.parametrize("format", formats)
def test_mediainfo_tag_dict(tmp_path, monkeypatch, format):
    p = str(tmp_path / ("a." + format))
    ours().export(p, format=format, tags=sets["common"]).close()
    want = pydub.utils.mediainfo(p)["TAG"]
    no_ffprobe(monkeypatch)
    assert utils2.mediainfo(p)["TAG"] == want


def wav_with(entries, audio=b"\0\0" * 100):
    info = b"INFO" + b"".join(k + struct.pack("<I", 2) + b"v\0" for k in entries)
    body = b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16) + b"LIST" + struct.pack("<I", len(info)) \
        + info + b"data" + struct.pack("<I", len(audio)) + audio
    return b"RIFF" + struct.pack("<I", len(body)) + body


def flac_with(tags):
    return _pydub2.encode(b"\0" * 400, 8000, 1, 2, "flac", 0, tags)


def ogg_with(tags):
    return _pydub2.encode(b"\0" * 400, 8000, 1, 2, "ogg", 0, tags)


HOSTILE = {
    "distinct keys in flac": (lambda n: flac_with([(b"k%d" % i, b"v") for i in range(n)]), 1000),
    "distinct ids in wav": (lambda n: wav_with([("%04x" % i).encode() for i in range(n)]), 1000),
    "repeated key in flac": (lambda n: flac_with([(b"k", b"v" * 100)] * n), 1000),
    "long packet in ogg": (lambda n: ogg_with([(b"k", b"v" * n)]), 1 << 20),
}


def seconds(tmp_path, data, ogg):
    p = tmp_path / "a"
    p.write_bytes(data)
    t = time.perf_counter()
    if ogg:
        # the decoder is slower than linear on a packet this long, so the tag reader is timed alone
        f = io.BytesIO(data)
        utils2._ogg_tags(lambda i, n: (f.seek(i), f.read(n))[1], 7)
    else:
        utils2.mediainfo_json(str(p))
    return time.perf_counter() - t


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_tags_take_linear_time(tmp_path, monkeypatch, name):
    make, n = HOSTILE[name]
    no_ffprobe(monkeypatch)
    ogg = "ogg" in name
    small, big = seconds(tmp_path, make(n), ogg), seconds(tmp_path, make(8 * n), ogg)
    # 8 times the tags may take 8 times as long, quadratic is 64
    assert big < 25 * small


class Counting(io.BufferedReader):
    got = 0

    def read(self, n=-1):
        b = super().read(n)
        Counting.got += len(b)
        return b


@pytest.mark.parametrize("name", ["wav", "flac", "ogg"])
def test_mediainfo_reads_only_the_tags(tmp_path, monkeypatch, name):
    audio = os.urandom(20_000_000)
    data = {"wav": lambda: wav_with([b"INAM"], audio), "flac": lambda: _pydub2.encode(audio, 8000, 1, 2, "flac", 0, [(b"title", b"t")]),
            "ogg": lambda: _pydub2.encode(audio[:10_000_000], 8000, 1, 2, "ogg", 0, [(b"title", b"t")])}[name]()
    p = tmp_path / ("a." + name)
    p.write_bytes(data)
    no_ffprobe(monkeypatch)
    monkeypatch.setattr(utils2, "open", lambda path, mode: Counting(io.FileIO(path)), raising=False)
    Counting.got = 0
    info = utils2.mediainfo_json(str(p))
    assert Counting.got < 100_000 and "title" in str(info)
    assert len(data) > 1_000_000


def test_tags_are_utf8_whatever_the_filesystem_says(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "fsencode", lambda x: x.encode("latin-1", "surrogateescape"))
    p = str(tmp_path / "a.flac")
    ours().export(p, format="flac", tags={"title": "\u00c5l \u65e5\u672c"}).close()
    no_ffprobe(monkeypatch)
    assert utils2.mediainfo_json(p)["format"]["tags"]["title"] == "\u00c5l \u65e5\u672c"


def syncsafe(n):
    return bytes([n >> 21 & 127, n >> 14 & 127, n >> 7 & 127, n & 127])


def id3_frame(name, data, ver=3, flags=0):
    if ver == 2:
        return name + len(data).to_bytes(3, "big") + data
    size = syncsafe(len(data)) if ver == 4 else struct.pack(">I", len(data))
    return name + size + struct.pack(">H", flags) + data


def id3_tag(frames, ver=3, flags=0, pad=0):
    body = b"".join(frames) + b"\0" * pad
    return b"ID3" + bytes([ver, 0, flags]) + syncsafe(len(body)) + body


def text(name, s, enc=0, ver=3):
    return id3_frame(name, bytes([enc]) + s, ver)


def id3v1(title=b"Title", artist=b"Artist", track=0, genre=13):
    b = b"TAG" + title.ljust(30, b"\0") + artist.ljust(30, b" ") + b"Album".ljust(30, b"\0") + b"1999"
    return b + (b"Comment".ljust(28, b"\0") + bytes([0, track]) if track else b"Comment".ljust(30, b"\0")) + bytes([genre])


def utf16(s, bom=True):
    return s.encode("utf-16" if bom else "utf-16-be")


def id3_cases():
    t = text
    return {
        "common": [id3_tag([t(b"TIT2", b"Hello"), t(b"TPE1", b"Art"), t(b"TALB", b"Alb"), t(b"TRCK", b"3/10"),
                            t(b"TCON", b"(13)"), t(b"TYER", b"2001"), t(b"TPE2", b"AA"), t(b"TPOS", b"1/2")]), b""],
        "genres": [id3_tag([t(b"TCON", b"13")]), b""],
        "genre with text": [id3_tag([t(b"TCON", b"(13)Pop")]), b""],
        "genre too big": [id3_tag([t(b"TCON", b"(200)")]), b""],
        "latin 1": [id3_tag([t(b"TIT2", b"\xc5l \xe6\xf8")]), b""],
        "utf 16 with bom": [id3_tag([t(b"TIT2", utf16("hej 日本"), 1)]), b""],
        "utf 16 little endian": [id3_tag([t(b"TIT2", b"\xff\xfe" + "hej 日".encode("utf-16-le"), 1)]), b""],
        "utf 16 big endian": [id3_tag([t(b"TIT2", utf16("hej \U0001f600", False), 2)]), b""],
        "utf 8": [id3_tag([t(b"TIT2", "hej \U0001f600".encode(), 3, 4)], 4), b""],
        "unknown encoding": [id3_tag([t(b"TIT2", b"abc", 9), t(b"TPE1", b"x")]), b""],
        "bad bom": [id3_tag([t(b"TIT2", b"\0\0abc", 1), t(b"TPE1", b"x")]), b""],
        "lone surrogate": [id3_tag([t(b"TIT2", b"\xff\xfea\0\0\xd8b\0", 1), t(b"TPE1", b"x")]), b""],
        "user frames": [id3_tag([id3_frame(b"TXXX", b"\0my key\0my val"), id3_frame(b"TXXX", b"\0MY KEY\0other"),
                                 id3_frame(b"TXXX", b"\0title\0t"), id3_frame(b"TXXX", b"\0empty\0"),
                                 id3_frame(b"TXXX", b"\0\0no key")]), b""],
        "user frame utf 16": [id3_tag([id3_frame(b"TXXX", b"\1" + utf16("k") + b"\0\0" + utf16("v") + b"\0\0")]), b""],
        "comments": [id3_tag([id3_frame(b"COMM", b"\0engdesc\0text"), id3_frame(b"COMM", b"\0eng\0plain"),
                              id3_frame(b"COMM", b"\0deu\0second"), id3_frame(b"COMM", b"\0eng\0")]), b""],
        "lyrics": [id3_tag([id3_frame(b"USLT", b"\0engdesc\0words"), id3_frame(b"USLT", b"\0eng\0two")]), b""],
        "date from three": [id3_tag([t(b"TYER", b"1999"), t(b"TDAT", b"1503"), t(b"TIME", b"1230")]), b""],
        "date from year": [id3_tag([t(b"TYER", b"1999"), t(b"TDRC", b"2020")]), b""],
        "date not numbers": [id3_tag([t(b"TYER", b"99"), t(b"TDAT", b"1503")]), b""],
        "recording time": [id3_tag([t(b"TDRC", b"2020-01-02", 3, 4)], 4), b""],
        "v2.2": [id3_tag([id3_frame(b"TT2", b"\0Title", 2), id3_frame(b"TP1", b"\0Art", 2), id3_frame(b"TCO", b"\0(17)", 2),
                          id3_frame(b"TYE", b"\0" b"2002", 2), id3_frame(b"TDA", b"\0" b"0102", 2),
                          id3_frame(b"COM", b"\0engd\0c", 2), id3_frame(b"TRK", b"\0" b"5", 2),
                          id3_frame(b"TXX", b"\0k\0v", 2), id3_frame(b"TBP", b"\0" b"120", 2)], 2), b""],
        "v2.4": [id3_tag([id3_frame(b"TIT2", b"\3Title", 4), id3_frame(b"TSSE", b"\3Lavf", 4)], 4), b""],
        "v2.4 footer": [id3_tag([id3_frame(b"TIT2", b"\3Title", 4)], 4, 0x10) + b"3DI" + bytes([4, 0, 0x10]) + syncsafe(20), b""],
        "v2.4 tag at the end": [b"", id3_tag([t(b"TIT2", b"x", 3, 4)], 4)],
        "v2.4 sizes not syncsafe": [id3_tag([id3_frame(b"TXXX", b"\0k\0" + b"v" * 300, 3), t(b"TIT2", b"t")], 4), b""],
        "extended header v2.3": [id3_tag([b"\0\0\0\6\0\0\0\0\0\0", t(b"TIT2", b"ext")], 3, 0x40), b""],
        "extended header v2.4": [id3_tag([b"\0\0\0\6\1\0", t(b"TIT2", b"ext", 3, 4)], 4, 0x40), b""],
        "unsynchronised tag": [id3_tag([id3_frame(b"TIT2", b"\0a\xff\0\xe0b")], 3, 0x80), b""],
        "unsynchronised frame": [id3_tag([id3_frame(b"TIT2", b"\0a\xff\0\xe0b", 3, 2)]), b""],
        "encrypted frame": [id3_tag([id3_frame(b"TIT2", b"\0abc", 3, 4), t(b"TPE1", b"x")]), b""],
        "data length": [id3_tag([id3_frame(b"TIT2", b"\0\0\0\4\0abc", 3, 1), t(b"TPE1", b"x")]), b""],
        "compressed": [id3_tag([id3_frame(b"TIT2", b"\0\0\0\5" + zlib.compress(b"\0abcd"), 3, 9), t(b"TPE1", b"x")]), b""],
        "compressed lying about its size": [id3_tag([id3_frame(b"TIT2", b"\0\0\0\2" + zlib.compress(b"\0abcd"), 3, 9)]), b""],
        "empty frames": [id3_tag([id3_frame(b"TIT2", b""), id3_frame(b"TPE1", b"\0"), id3_frame(b"TALB", b"\0\0"),
                                  t(b"TCOM", b"c")]), b""],
        "padding": [id3_tag([t(b"TIT2", b"p")], 3, 0, 50), b""],
        "duplicates": [id3_tag([t(b"TIT2", b"1"), t(b"TIT2", b"2"), t(b"tit2", b"3")]), b""],
        "private frames": [id3_tag([id3_frame(b"PRIV", b"own\0\1a\\b\tx\ny\xff"), id3_frame(b"PRIV", b"own\0two"),
                                    id3_frame(b"PRIV", b"empty\0"), t(b"TIT2", b"t")]), b""],
        "pictures are not tags": [id3_tag([id3_frame(b"APIC", b"\0image/jpeg\0\3\0" + b"x" * 20), t(b"TIT2", b"t")]), b""],
        "two tags": [id3_tag([t(b"TIT2", b"a")]) + id3_tag([t(b"TIT2", b"b"), t(b"TPE1", b"c")]), b""],
        "version 5 is skipped": [id3_tag([t(b"TIT2", b"a")], 5) + id3_tag([t(b"TPE1", b"c")]), b""],
        "v1 only": [b"", id3v1(track=7)],
        "v1 only latin 1": [b"", id3v1(title=b"T\xe5l", genre=200)],
        "v1 and v2": [id3_tag([t(b"TIT2", b"v2 title")]), id3v1()],
        "v1 and an empty v2": [id3_tag([t(b"TIT2", b"")]), id3v1()],
        "v1 and only private v2": [id3_tag([id3_frame(b"PRIV", b"own\0z")]), id3v1()],
    }


@pytest.fixture(scope="module")
def mp3_audio(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    p = tmp_path_factory.mktemp("mp3") / "a.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", "-ac", "1", "-ar", "22050",
                    "-write_id3v2", "0", "-write_xing", "0", str(p)], check=True)
    return p.read_bytes()


def same_as_ffprobe(path, monkeypatch):
    want = probe(path)
    no_ffprobe(monkeypatch)
    assert where(utils2.mediainfo_json(path)) == want
    assert utils2.mediainfo(path).get("TAG", {}) == dict(want[1] + want[0])


@need_ffmpeg
@pytest.mark.parametrize("name", id3_cases())
def test_id3_like_ffprobe(tmp_path, monkeypatch, mp3_audio, name):
    head, tail = id3_cases()[name]
    p = tmp_path / "a.mp3"
    p.write_bytes(head + mp3_audio + tail)
    same_as_ffprobe(str(p), monkeypatch)


@need_ffmpeg
@pytest.mark.parametrize("version", ["3", "4"])
@pytest.mark.parametrize("v1", ["0", "1"])
@pytest.mark.parametrize("name", ["common", "non ascii", "unknown", "twice", "empty", "long", "equals", "renamed"])
def test_id3_of_ffmpeg(tmp_path, monkeypatch, name, version, v1):
    if windows_cant(name):
        pytest.skip(CANT)
    args = [x for k, v in sets[name].items() for x in ("-metadata", "%s=%s" % (k, v))]
    p = str(tmp_path / "a.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", *args, "-id3v2_version", version,
                    "-write_id3v1", v1, p], check=True)
    same_as_ffprobe(p, monkeypatch)


@need_ffmpeg
@pytest.mark.skipif(shutil.which("lame") is None, reason="needs lame")
@pytest.mark.parametrize("args", [[], ["--tt", "T", "--ta", "A", "--tl", "Al", "--ty", "2020", "--tn", "4", "--tg", "13", "--add-id3v2"],
                                  ["--id3v1-only", "--tt", "T", "--ta", "A"], ["--id3v2-only", "--tt", "T", "--tc", "C"]])
def test_id3_of_lame(tmp_path, monkeypatch, args):
    wav, p = str(tmp_path / "a.wav"), str(tmp_path / "a.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", wav], check=True)
    subprocess.run(["lame", "--quiet", *args, wav, p], check=True)
    same_as_ffprobe(p, monkeypatch)


def id3_of(data):
    return utils2._mp3_tags(io.BytesIO(data), len(data))[0]


def test_id3_values():
    t = text
    assert id3_of(id3_tag([t(b"TIT2", b"T"), t(b"TCON", b"(13)"), t(b"TYER", b"1999"), t(b"TDAT", b"1503")]))\
        == {"title": "T", "genre": "Pop", "date": "1999-03-15"}
    assert id3_of(id3_tag([id3_frame(b"TT2", b"\1" + utf16("å"), 2)], 2)) == {"title": "å"}
    assert id3_of(id3_tag([id3_frame(b"COMM", b"\0eng\0hi")])) == {"comment": "hi"}
    assert id3_of(id3_tag([t(b"TIT2", b"a"), t(b"TIT2", b"b")])) == {"title": "a"}
    assert id3_of(b"x" * 200 + id3v1(track=3)) == {
        "title": "Title", "artist": "Artist", "album": "Album", "date": "1999", "comment": "Comment", "track": "3", "genre": "Pop"}
    assert id3_of(id3_tag([t(b"TIT2", b"v2")]) + b"x" * 200 + id3v1()) == {"title": "v2"}
    assert id3_of(b"x" * 300) == {}


def rich():
    return id3_tag([text(b"TIT2", b"a" * 20), text(b"TIT2", utf16("b" * 9), 1), id3_frame(b"TXXX", b"\0k\0v"),
                    id3_frame(b"COMM", b"\1eng" + utf16("d") + b"\0\0" + utf16("t")), id3_frame(b"USLT", b"\0engd\0t"),
                    id3_frame(b"PRIV", b"o\0\1\2"), text(b"TYER", b"1999"), text(b"TDAT", b"1503"),
                    id3_frame(b"TIT2", b"\0\0\0\5" + zlib.compress(b"\0abcd"), 3, 9), id3_frame(b"TALB", b"\0a\xff\0b", 3, 2)])


def test_id3_prefixes_and_flips_never_raise():
    data = rich()
    for i in range(len(data) + 1):
        id3_of(data[:i])
    rng = random.Random(1)
    for _ in range(3000):
        b = bytearray(data)
        for _ in range(rng.randint(1, 4)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        id3_of(bytes(b) + b"\0" * rng.randint(0, 200))


def test_id3_bad_encodings_and_sizes_never_raise():
    for enc in range(256):
        for body in (b"", b"a", b"abc\0def", b"\xff\xfea\0", b"\xfe\xff\0a\0\0\0b", b"\0\0\xd8\0"):
            for name in (b"TIT2", b"TXXX", b"COMM", b"USLT", b"TCON"):
                id3_of(id3_tag([id3_frame(name, bytes([enc]) + body + body)]))
    for size in (0, 1, 3, 9, 10, 11, 0x7f, 0x80, 0x3fff, 0x4000, 0x7fffffff, 0xffffffff):
        for ver in (2, 3, 4):
            for flags in (0, 1, 2, 4, 8, 15, 0xff):
                f = b"TIT2" + struct.pack(">I", size) + struct.pack(">H", flags) + b"\0abc" * 3
                id3_of(id3_tag([f], ver, flags & 0xf0))
                id3_of(id3_tag([f], ver, flags) + b"x" * 50)
    id3_of(b"ID3\3\0\0\x7f\x7f\x7f\x7f" + b"x" * 100)
    id3_of(b"ID3\3\0\0\x80\0\0\0" + b"x" * 100)
    id3_of(b"ID3\xff\xff\0\0\0\0\0")


def id3_speed(make, n):
    data = make(n)
    t = time.perf_counter()
    id3_of(data)
    return time.perf_counter() - t


ID3_HOSTILE = {
    "frames of size 0": (lambda n: id3_tag([id3_frame(b"TIT2", b"")] * n), 20000),
    "frames that are all different": (lambda n: id3_tag([id3_frame(b"TXXX", b"\0k%d\0v" % i) for i in range(n)]), 5000),
    "the same frame again and again": (lambda n: id3_tag([text(b"TIT2", b"x" * 100)] * n), 5000),
    "tags one after the other": (lambda n: b"ID3\3\0\0\0\0\0\0" * n, 20000),
    "tags with a lying size": (lambda n: b"ID3\3\0\0" + syncsafe(10 ** 7) + b"x" * n, 20000),
    "frames that say they are huge": (lambda n: id3_tag([b"TIT2\x0f\xff\xff\xff\0\0" + b"x" * 20] * n), 20000),
    "private frame with control bytes": (lambda n: id3_tag([id3_frame(b"PRIV", b"o\0" + b"\1" * n)]), 1 << 18),
    "compressed frame that grows": (lambda n: id3_tag([id3_frame(b"TIT2", n.to_bytes(4, "big") + zlib.compress(b"\0" * n), 3, 9)]), 1 << 18),
    "compressed frames again and again": (lambda n: id3_tag([id3_frame(b"TIT2", (1 << 20).to_bytes(4, "big") + zlib.compress(b"\0" * (1 << 20)), 3, 9)] * n), 100),
    "compressed frames that fail to inflate": (lambda n: id3_tag([id3_frame(b"TIT2", (1 << 20).to_bytes(4, "big") + zlib.compress(b"\0" * (1 << 20))[:-1], 3, 9)] * n), 100),
    "compressed frames one byte short": (lambda n: id3_tag([id3_frame(b"TIT2", ((1 << 20) - 1).to_bytes(4, "big") + zlib.compress(b"\0" * (1 << 20)), 3, 9)] * n), 100),
    "utf 16 with its zeros in the wrong place": (lambda n: id3_tag([id3_frame(b"TXXX", b"\1\xff\xfe" + b"a\0\0" * n)]), 1 << 17),
    "unsynchronised": (lambda n: id3_tag([id3_frame(b"TIT2", b"\0" + b"\xff\0" * n, 3, 2)]), 1 << 17),
}


@pytest.mark.parametrize("name", ID3_HOSTILE)
def test_id3_hostile_takes_linear_time(name):
    make, n = ID3_HOSTILE[name]
    small, big = id3_speed(make, n), id3_speed(make, 8 * n)
    # 8 times the bytes may take 8 times as long, quadratic is 64
    assert big < 25 * small + 0.05
    assert big < 10


def test_id3_reads_only_the_tags(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(id3_tag([text(b"TIT2", b"t")]) + os.urandom(20_000_000) + id3v1())
    f = Counting(io.FileIO(p))
    Counting.got = 0
    assert utils2._mp3_tags(f, p.stat().st_size)[0] == {"title": "t"}
    assert Counting.got < 1000


def cover(n):
    img = b"\xff\xd8\xff\xe0" + os.urandom(n)
    pic = struct.pack(">II", 3, 10) + b"image/jpeg" + struct.pack(">I", 0) + struct.pack(">IIIII", 1, 1, 24, 0, len(img)) + img
    return base64.b64encode(pic)


@pytest.mark.parametrize("format", ["ogg", "opus"])
def test_a_cover_over_the_cap_leaves_the_other_tags(format):
    tags = [(b"title", b"T"), (b"METADATA_BLOCK_PICTURE", cover(13 << 20)), (b"artist", b"A")]
    data = _pydub2.encode(raw(), 22050, 1, 2, format, 0, tags)
    assert len(data) > 17 << 20
    f = io.BytesIO(data)
    got = utils2._convert(utils2._ogg_tags(lambda i, n: (f.seek(i), f.read(n))[1], 7 if format == "ogg" else 8), {})
    assert got == {"title": "T", "artist": "A"}


@need_ffmpeg
def test_cover_and_many_tags_like_ffprobe(tmp_path, monkeypatch):
    many = [(b"k%d" % i, b"v") for i in range(5000)] + [(b"title", b"last")]
    paths = {"a.flac": _pydub2.encode(raw(), 22050, 1, 2, "flac", 0, many),
             "b.opus": _pydub2.encode(raw(), 22050, 1, 2, "opus", 0, [(b"title", b"T"), (b"METADATA_BLOCK_PICTURE", cover(5 << 20))])}
    want = {}
    for name, data in paths.items():
        (tmp_path / name).write_bytes(data)
        want[name] = probe(tmp_path / name)
    assert len(dict(want["a.flac"][0])) > 4096
    no_ffprobe(monkeypatch)
    for name in paths:
        assert where(utils2.mediainfo_json(str(tmp_path / name))) == want[name]


@pytest.mark.parametrize("format", ["ogg", "opus"])
def test_a_cut_comment_page_gives_what_was_read(format):
    data = _pydub2.encode(raw(), 22050, 1, 2, format, 0, [(b"title", b"T" * 150000), (b"artist", b"A")])
    for cut in range(200, 170000, 997):
        f = io.BytesIO(data[:cut])
        utils2._ogg_tags(lambda i, n: (f.seek(i), f.read(n))[1], 7 if format == "ogg" else 8)


def test_inflating_that_fails_is_paid_for():
    frame = id3_frame(b"TIT2", ((1 << 20) - 1).to_bytes(4, "big") + zlib.compress(b"\0" * (1 << 20)), 3, 9)
    good = id3_frame(b"TIT2", (1 << 20).to_bytes(4, "big") + zlib.compress(b"\0" * (1 << 20)), 3, 9)
    short = id3_tag([frame] * 12)
    # the baseline inflates the same bytes without a failure, 12 MiB of tag of these must not cost more than 20 times that
    t = time.perf_counter()
    id3_of(id3_tag([good] * 12))
    base = time.perf_counter() - t
    t = time.perf_counter()
    id3_of(id3_tag([frame] * 4000)[:12 << 20])
    assert time.perf_counter() - t < 20 * base + 0.5
    assert id3_of(short) == {}
