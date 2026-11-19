import os
import pathlib
import re
import shutil
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


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads the extension with nm")
def test_uses_only_python_symbols_the_macos_link_allows():
    # the macos link lets only the symbols in nanobind's list stay undefined, a private one breaks that build
    nanobind = pytest.importorskip("nanobind")
    allowed = pathlib.Path(nanobind.cmake_dir()) / "darwin-ld-cpython.sym"
    try:
        out = subprocess.run(["nm", "-D", "--undefined-only", _pydub2.__file__], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no nm")
    names = {line.split()[-1].split("@")[0] for line in out.splitlines() if line.strip()}
    listed = {line.split()[-1][1:] for line in allowed.read_text().splitlines() if line.startswith("-U _")}
    assert sorted(n for n in names if n.lstrip("_").startswith("Py") and n not in listed) == []


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


@pytest.mark.parametrize("name, files", [("vorbis", ["lib/psy.c", "lib/floor1.c"]), ("opusfile", ["src/opusfile.c"])])
def test_patch_scripts_run_again_and_refuse_other_sources(tmp_path, name, files):
    # cmake runs a patch step again on the patched sources when the command line changes, a new cmake path is enough
    cmake = shutil.which("cmake")
    found = sorted(ROOT.glob("build/*/_deps/%s-src" % name))
    if not cmake or not found:
        pytest.skip("needs cmake and a build tree")
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(found[0] / f, tmp_path / f)
    script = str(ROOT / "cmake" / (name + "_patch.cmake"))
    subprocess.run([cmake, "-P", script], cwd=tmp_path, check=True, capture_output=True)
    once = [(tmp_path / f).read_bytes() for f in files]
    subprocess.run([cmake, "-P", script], cwd=tmp_path, check=True, capture_output=True)
    assert [(tmp_path / f).read_bytes() for f in files] == once
    (tmp_path / files[0]).write_bytes(once[0] + b"\n")
    r = subprocess.run([cmake, "-P", script], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode and "not what the patch expects" in r.stderr
    assert (tmp_path / files[0]).read_bytes() == once[0] + b"\n"
