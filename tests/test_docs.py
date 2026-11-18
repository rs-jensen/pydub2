import ast
import builtins
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from pydub2 import _mp3

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

# what the snippets in the README take for granted, they are pieces of one session
PRELUDE = """\
from pydub2 import AudioSegment
seg = AudioSegment.from_file("song.wav")
upload = "song.wav"
"""
GIVEN = {"AudioSegment", "seg", "upload"}


def blocks():
    if not README.exists():
        return []
    out = []
    text = README.read_text(encoding="utf-8")
    for m in re.finditer(r"^```python\n(.*?)^```", text, re.S | re.M):
        out.append((text.count("\n", 0, m.start()) + 2, m.group(1)))
    return out


def free_names(code):
    tree = ast.parse(code)
    bound, used = set(dir(builtins)), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (used if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return used - bound


def has_mp3_writer():
    return _mp3.available() or shutil.which("ffmpeg") is not None


CASES = blocks()


def test_readme_has_python_blocks():
    if not README.exists():
        pytest.skip("not run from a source checkout")
    assert len(CASES) >= 5


@pytest.mark.parametrize("line,code", CASES, ids=["README.md:%d" % c[0] for c in CASES])
def test_readme_block_runs(line, code, tmp_path):
    if free_names(code) - GIVEN:
        pytest.skip("not self-contained")
    if "play_async" in code:
        pytest.skip("needs a sound device or ffplay")
    if "numpy" in code or "to_numpy" in code or "from_numpy" in code:
        pytest.importorskip("numpy")
    if '"mp3"' in code and not has_mp3_writer():
        pytest.skip("needs pydub2-mp3 or ffmpeg to write mp3")

    # real files for the names the snippets open, made by pydub2 itself, so no ffmpeg is needed
    from pydub2.generators import Sine
    tone = Sine(440).to_audio_segment(duration=2000)
    tone.export(str(tmp_path / "song.wav"), format="wav").close()
    tone.export(str(tmp_path / "long.flac"), format="flac").close()
    (tmp_path / "block.py").write_text(PRELUDE + code, encoding="utf-8")

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    env.pop("PYDUB2_STRICT", None)
    done = subprocess.run([sys.executable, "block.py"], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, "README.md:%d\n%s" % (line, done.stderr[-2000:])
