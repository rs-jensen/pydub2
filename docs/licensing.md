# Licensing

pydub2 is MIT. The Python layer is a port of pydub (MIT, licenses/pydub.txt). The tests in `tests/pydub_suite/test_pydub.py` are pydub's own tests.

## What is bundled

The C++ extension has the libraries listed in [THIRD_PARTY.md](../THIRD_PARTY.md) built in. All of them are MIT, BSD, public domain or MIT-0. The text of each license is in `licenses/` in the source tree and in `*.dist-info/licenses/licenses` of an installed wheel.

Only the libFLAC library is built from the FLAC source. The flac and metaflac programs and the replaygain code, which are GPL or LGPL, are not built and not in the wheel.

## Silero VAD weights

`src/silero_weights.h` is made by `tools/silero_weights.py` from `silero_vad.onnx` of silero-vad v6.2 (MIT, Copyright (c) 2020-present Silero Team). The file has sha256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`. Running the script on it gives the header in the repo byte for byte.

## Test audio

No audio from pydub's repository is kept. `tests/pydub_suite/conftest.py` makes the test files when the suite first runs, and `tests/pydub_suite/data/.gitignore` keeps them out of git.

## mp3 and aac

mp3 reading is built in (minimp3 through dr_mp3). mp3 writing uses LAME 3.100 (LGPL-2.0-or-later), which is not in this package. It is in the separate package `pydub2-mp3`, installed with `pip install pydub2[mp3]`. LAME is built from the unmodified release tarball (sha256 `ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e`) with `mp3/CMakeLists.txt` as its own shared library, which pydub2 loads with ctypes. Anyone can replace it with their own build. The `LICENSE` of that package has the full LGPL text, the source and how to replace the library.

aac, m4a, wma and video files are not bundled. They go to ffmpeg, which you install yourself.

## The name

pydub2 is a name for compatibility, like pydub-ng. The project is not affiliated with pydub. I found no registered trademark for "pydub" in the US trademark database. I could not search the EU and WIPO databases.
