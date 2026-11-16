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
| nanobind | 3.1.0, 2.15.0 for Python 3.9 | BSD-3-Clause | licenses/nanobind.txt |
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
that tag is MIT, Copyright (c) 2020-present Silero Team.

Two vendored files have a small change so that opening files from several threads does not write shared tables at the same time: `src/third_party/stb_vorbis.c` (`crc32_init` is called once from `src/decode.cpp`) and `src/third_party/ebur128.c` (the static constants are written only by the first `ebur128_init`, which `src/loudness.cpp` makes once before any other).

opusfile 0.12 is patched at build time by `cmake/opusfile_patch.cmake`: the fix for CVE-2022-47021 (xiph/opusfile 0a4cd796) and the fix from xiph/opusfile master for the tags a failed open leaked.

On arm64 (not MSVC) libopusenc 0.3 is built with `USE_NEON`, so `resample.c` includes `cmake/resample_neon.h`. It is `resample_sse.h` of the same release (BSD-3-Clause, licenses/speex-resampler.txt) with neon intrinsics for `inner_product_single` and `interpolate_product_single`, which upstream only has for SSE. Without it the resampler runs as a scalar loop on arm64.

`src/third_party/dr_mp3.h` has small changes so that it decodes like ffmpeg. Each one is marked with a `pydub2:` comment:

- a short granule ignores its own scfsi bits but no longer clears the ones of the next granule
- the private bits in front of the scfsi bits are skipped, minimp3 read them as scfsi of granule 0
- MPEG 2 intensity stereo: the highest scalefactor value is a position like any other, only 16 and up are illegal
- a frame whose granules claim more bits than it has is decoded, not dropped. The huffman decoder stops at the end of
  the data before each pair and each quad, reads zeros after it, and drops a last quad that ran past its granule
- after such a frame the bit reservoir is the end of its data
- scfsi copies the scalefactors of the last granule 0 that was decoded, which `src/decode.cpp` can mark as zeroed
  (the new fields `zeroed` and `scf0` in `drmp3dec`)
- a frame cut short at the end of the file is decoded up to the cut (the new fields `missing` and `have`), a granule
  that starts in the bit reservoir reads zeros past it like ffmpeg

The mp3 encoder is not part of this package. It is in the separate package pydub2-mp3 (LAME 3.100,
LGPL-2.0-or-later) and is loaded at runtime. It is its own shared library, built from the unmodified
release tarball, and its LICENSE has the full LGPL text, the source and how to replace it.

The test audio in `tests/pydub_suite` is made when the tests run. No audio from pydub's repo is kept.
`tests/pydub_suite/test_pydub.py` is pydub's test code (MIT, licenses/pydub.txt).

More in [docs/licensing.md](docs/licensing.md).
