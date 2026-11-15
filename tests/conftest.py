import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    e = call.excinfo
    if e and e.errisinstance(FileNotFoundError) and "ffmpeg" in str(e.value):
        rep.outcome = "skipped"
        rep.longrepr = (str(item.path), item.location[1] or 0, "Skipped: no ffmpeg")
