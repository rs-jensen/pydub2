# Third party code

These are built into the pydub2 extension. The license text of each one is in the `licenses`
folder of the source tree, and in `*.dist-info/licenses/licenses` of an installed wheel.

| Library | Version | License | Text |
|---|---|---|---|
| dr_wav | 0.14.6 | public domain or MIT-0 | licenses/dr_libs.txt |
| dr_flac | 0.13.4 | public domain or MIT-0 | licenses/dr_libs.txt |
| dr_mp3 | 0.7.4 | public domain or MIT-0 | licenses/dr_libs.txt |
| stb_vorbis | 1.24 (commit 9f755b6) | public domain or MIT | licenses/stb.txt |
| libebur128 | 1.2.6 | MIT | licenses/libebur128.txt |
| sys/queue.h, shipped with libebur128 | | BSD-3-Clause | licenses/queue.txt |
| libogg | 1.3.6 | BSD-3-Clause | licenses/libogg.txt |
| libvorbis | 1.3.7 | BSD-3-Clause | licenses/libvorbis.txt |
| libFLAC | 1.5.0 | BSD-3-Clause | licenses/libFLAC.txt |
| libopus | 1.6.1 | BSD-3-Clause | licenses/libopus.txt |
| opusfile | 0.12 | BSD-3-Clause | licenses/opusfile.txt |
| libopusenc | 0.3 | BSD-3-Clause | licenses/libopusenc.txt |
| Speex resampler, inside libopusenc | | BSD-3-Clause | licenses/speex-resampler.txt |
| libsamplerate | 0.2.2 | BSD-2-Clause | licenses/libsamplerate.txt |
| nanobind | 3.1.0 | BSD-3-Clause | licenses/nanobind.txt |
| robin-map, inside nanobind | | MIT | licenses/robin-map.txt |
| Silero VAD | v6.2, weights from silero_vad.onnx | MIT | licenses/silero-vad.txt |
| pydub | 0.25.1, the Python layer is a port of it | MIT | licenses/pydub.txt |

Only the libFLAC library is built from the FLAC source. The flac and metaflac programs and the
replaygain code, which are GPL or LGPL, are not built and are not in the wheel. The MD5 code inside
libFLAC is public domain.

The Silero weights in `src/silero_weights.h` come from `src/silero_vad/data/silero_vad.onnx` of
github.com/snakers4/silero-vad at tag v6.2 (first shipped in the PyPI wheel silero-vad 6.2.0),
sha256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`. Running
`tools/silero_weights.py` on that file gives the header in the repo byte for byte. The license at
that tag is MIT, Copyright (c) 2020-present Silero Team. The comment in the header says v5, which is old.

Two vendored files have a small change each, so that opening many files from several threads does not write
shared tables at the same time (ThreadSanitizer flagged both). `src/third_party/stb_vorbis.c`: `crc32_init` is
not `static` and is no longer called from `start_decoder`, `src/decode.cpp` calls it once. `src/third_party/ebur128.c`:
the block in `ebur128_init` that writes the static constants runs only while `relative_gate_factor` is still 0,
and `src/loudness.cpp` makes the first `ebur128_init` call once before any other.

The mp3 encoder is not part of this package. It is in the separate package pydub2-mp3 (LAME 3.100,
LGPL-2.0-or-later) and is loaded at runtime. It is its own shared library, built from the unmodified
release tarball, and its LICENSE has the full LGPL text, the source and how to replace it.

The test audio in `tests/pydub_suite` is made when the tests run. No audio from pydub's repo is kept.
`tests/pydub_suite/test_pydub.py` is pydub's test code (MIT, licenses/pydub.txt).

How each of these was checked is in [docs/licensing.md](docs/licensing.md).
