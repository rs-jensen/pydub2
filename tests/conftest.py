import functools
import re
import shutil
import subprocess

import pytest

from pydub2.exceptions import CouldntDecodeError


OPTIONAL = {"libmp3lame", "libvorbis", "libopus", "libfdk_aac", "libshine", "aac"}
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


@functools.lru_cache(None)
def encoders(ffmpeg):
    out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return {line.split()[1] for line in out.splitlines() if line.startswith(" A")}


def why(e):
    msg = str(e.value)
    cmd = getattr(e.value, "cmd", None)
    if not HAVE_FFMPEG and e.errisinstance(FileNotFoundError) and ("ffmpeg" in msg or "ffprobe" in msg):
        return "no ffmpeg"
    # pydub2 raises this when it cant decode natively and has no ffmpeg to fall back on
    if e.errisinstance(CouldntDecodeError) and "cant read this file" in msg and not HAVE_FFMPEG:
        return "no ffmpeg"
    m = re.search(r"Unknown encoder '([^']+)'", msg)
    if m and HAVE_FFMPEG and m.group(1) in OPTIONAL and m.group(1) not in encoders(shutil.which("ffmpeg")):
        return "ffmpeg lacks " + m.group(1)
    if e.errisinstance(subprocess.CalledProcessError) and isinstance(cmd, list):
        for flag in ("-acodec", "-c:a"):
            if flag in cmd[:-1]:
                name = cmd[cmd.index(flag) + 1]
                if name in OPTIONAL and name not in encoders(cmd[0]):
                    return "ffmpeg lacks " + name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    reason = call.excinfo and why(call.excinfo)
    if reason:
        rep.outcome = "skipped"
        rep.longrepr = (str(item.path), item.location[1] or 0, "Skipped: " + reason)
