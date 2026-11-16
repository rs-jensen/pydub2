import shutil

import pytest

have_ffmpeg = shutil.which("ffmpeg") is not None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    e = call.excinfo
    msg = str(e.value) if e else ""
    missing = not have_ffmpeg and e and e.errisinstance(FileNotFoundError) and "ffmpeg" in msg
    if missing or "Encoder not found" in msg:
        rep.outcome = "skipped"
        rep.longrepr = (str(item.path), item.location[1] or 0, "Skipped: no ffmpeg or no encoder")
