import os
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
