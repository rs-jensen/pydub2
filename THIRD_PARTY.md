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
| Silero VAD | v5 weights from silero_vad.onnx | MIT | licenses/silero-vad.txt |
| pydub | 0.25.1, the Python layer is a port of it | MIT | licenses/pydub.txt |

Only the libFLAC library is built from the FLAC source. The flac and metaflac programs and the
replaygain code, which are GPL or LGPL, are not built and are not in the wheel. The MD5 code inside
libFLAC is public domain.

The mp3 encoder is not part of this package. It is in the separate package pydub2-mp3 (LAME, LGPL)
and is loaded at runtime.
