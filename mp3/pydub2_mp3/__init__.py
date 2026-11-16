import os
import sys
from importlib.metadata import PackageNotFoundError, version

lame_version = "3.100"
try:
    __version__ = version("pydub2-mp3")
except PackageNotFoundError:
    __version__ = "0+unknown"


def path():
    ext = {"win32": ".dll", "darwin": ".dylib"}.get(sys.platform, ".so")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "libmp3lame" + ext)
