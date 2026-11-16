import functools
import shutil
import subprocess

import pytest

from pydub2.exceptions import CouldntDecodeError


@functools.lru_cache(None)
def encoders(ffmpeg):
    out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return {line.split()[1] for line in out.splitlines() if line.startswith(" A")}


def why(e):
    msg = str(e.value)
    cmd = getattr(e.value, "cmd", None)
    if e.errisinstance(FileNotFoundError) and ("ffmpeg" in msg or "ffprobe" in msg):
        return "no ffmpeg"
    # pydub2 raises this when it cant decode natively and has no ffmpeg to fall back on
    if e.errisinstance(CouldntDecodeError) and "cant read this file" in msg and shutil.which("ffmpeg") is None:
        return "no ffmpeg"
    if "Encoder not found" in msg or "Unknown encoder" in msg:
        return "ffmpeg lacks an encoder"
    if e.errisinstance(subprocess.CalledProcessError) and isinstance(cmd, list) and "-c:a" in cmd[:-1]:
        name = cmd[cmd.index("-c:a") + 1]
        if name not in encoders(cmd[0]):
            return "ffmpeg lacks " + name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    reason = call.excinfo and why(call.excinfo)
    if reason:
        rep.outcome = "skipped"
        rep.longrepr = (str(item.path), item.location[1] or 0, "Skipped: " + reason)
