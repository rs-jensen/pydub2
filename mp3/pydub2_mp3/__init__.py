import os
import sys

__version__ = "3.100.0"


def path():
    ext = {"win32": ".dll", "darwin": ".dylib"}.get(sys.platform, ".so")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "libmp3lame" + ext)
