import json
import shutil
import subprocess

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
    "renamed": {"tracknumber": "1", "discnumber": "2", "albumartist": "x", "album artist": "y", "description": "d",
                "comment": "c", "ALBUM_ARTIST": "g", "DISC": "5"},
    "not text": {"track": 3, "x": None, "y": 1.5},
    "nothing": {},
    "no key": {"": "v", "a": "b"},
    "bytes": {"title": "a\udc80b"},
}
formats = ["flac", "ogg", "opus"]


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
    monkeypatch.setattr(utils2, "which", lambda p: None if p in ("ffprobe", "avprobe") else which(p))


@need_ffmpeg
@pytest.mark.parametrize("name", sets)
@pytest.mark.parametrize("format", formats)
def test_tags_like_pydub(tmp_path, monkeypatch, name, format):
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
