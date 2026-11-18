import os
import pathlib
import re
import subprocess
import sys

import pytest

import _pydub2
import pydub2

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_decode_garbage_raises():
    with pytest.raises(RuntimeError):
        _pydub2.decode(b"not audio")


@pytest.mark.skipif(sys.platform == "win32", reason="the console check uses ctypes there")
def test_import_does_not_need_ctypes():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    code = "import sys, pydub2\nassert 'ctypes' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], env=env, check=True)


def test_docs_say_the_lame_build_is_patched():
    root = pathlib.Path(__file__).resolve().parents[1]
    if not (root / "mp3" / "lame_patch.cmake").exists():
        pytest.skip("not run from a source checkout")
    for name in ("docs/licensing.md", "THIRD_PARTY.md", "mp3/LICENSE", "mp3/README.md"):
        assert "unmodified" not in (root / name).read_text(encoding="utf-8"), name


def test_versions_agree():
    if not (ROOT / "pyproject.toml").exists():
        pytest.skip("not run from a source checkout")
    found = {}
    for name in ("pyproject.toml", "mp3/pyproject.toml", "compat/pyproject.toml"):
        path = ROOT / name
        if path.exists():
            found[name] = re.search(r'^version = "([^"]+)"', path.read_text(encoding="utf-8"), re.M).group(1)
    found["pydub2.__version__"] = pydub2.__version__
    assert len(set(found.values())) == 1, found


def test_license_files_named_in_the_docs_exist():
    if not (ROOT / "licenses").is_dir():
        pytest.skip("not run from a source checkout")
    named = set()
    for name in ("THIRD_PARTY.md", "docs/licensing.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        named.update(re.findall(r"\blicenses/[\w.-]+\.txt", text))
    assert named
    for name in sorted(named):
        assert (ROOT / name).is_file(), name
    third_party = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    for path in sorted((ROOT / "licenses").iterdir()):
        assert "licenses/" + path.name in third_party, path.name
