import importlib.util
import os
import subprocess
import sys

import pytest

if importlib.util.find_spec("pydub2_compat") is None:
    pytest.skip("pydub2-compat is not installed", allow_module_level=True)

names = ["utils", "silence", "effects", "playback", "generators", "exceptions", "audio_segment", "logging_utils"]
if importlib.util.find_spec("scipy"):
    names.append("scipy_effects")


def run(code, tmp_path, files=None, **env):
    path = []
    for name, text in (files or {}).items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        path = [str(tmp_path)]
    e = dict(os.environ, PYTHONPATH=os.pathsep.join(path), **env)
    return subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, cwd=tmp_path)


same = """
import importlib, sys
import pydub, pydub2
from pydub import AudioSegment
from pydub.silence import split_on_silence
assert pydub is pydub2 and AudioSegment is pydub2.AudioSegment
import pydub2.silence
assert split_on_silence is pydub2.silence.split_on_silence
for n in %r:
    m = importlib.import_module("pydub." + n)
    assert m is sys.modules["pydub2." + n] and m.__spec__.name == "pydub2." + n, n
    assert getattr(pydub, n) is m
importlib.reload(pydub)
assert pydub.__name__ == "pydub2" and pydub.__spec__.name == "pydub2"
assert isinstance(AudioSegment.silent(10), pydub.audio_segment.AudioSegment)
for n in ("pydub.pyaudioop", "pydub.audioop", "pydub.nothing"):
    try:
        importlib.import_module(n)
    except ImportError:
        pass
    else:
        raise SystemExit(n)
""" % names

fake = {"pydub/__init__.py": "raise RuntimeError('real pydub')"}


def test_aliases(tmp_path):
    r = run(same, tmp_path)
    assert r.returncode == 0, r.stderr


def test_aliases_with_real_pydub_installed(tmp_path):
    r = run(same, tmp_path, fake)
    assert r.returncode == 0, r.stderr


def test_isolated_mode(tmp_path):
    r = subprocess.run([sys.executable, "-I", "-c", "import pydub, pydub2; assert pydub is pydub2"], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr


def test_off_switch(tmp_path):
    r = run("import pydub", tmp_path, fake, PYDUB2_COMPAT="0")
    assert "real pydub" in r.stderr


def test_pydub2_import_fails(tmp_path):
    files = {"pydub2/__init__.py": "raise ImportError('boom')", "pydub/__init__.py": "x = 'real'"}
    r = run("import pydub; print(pydub.x)", tmp_path, files)
    assert r.stdout.strip() == "real"
    assert r.stderr.count("pydub2-compat: cant import pydub2 (boom)") == 1


def test_inside_pytest():
    import pydub2
    from pydub import AudioSegment

    assert AudioSegment is pydub2.AudioSegment
