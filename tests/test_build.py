import os
import pathlib
import subprocess
import sys

import pytest

import _pydub2


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
