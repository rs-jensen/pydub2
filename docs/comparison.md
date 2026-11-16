# pydub2 compared with other audio libraries

I wrote pydub2 to replace pydub. This page puts it next to the libraries people use for the same jobs, and says where each of them is the better pick.

I checked everything on 2026-11-16. Versions and dates come from PyPI that day. For the format and Python rows I installed the wheels from PyPI on macOS arm64 with Python 3.14, took ffmpeg off `PATH`, and tried to read and write a three second file in each format. I did not install torchcodec, librosa or audioread, so their cells come from their READMEs, changelogs and PyPI metadata. Links are at the end.

## The facts

| | pydub2 | pydub 0.25.1 | pydub-lts | pydub-ng | soundfile | PyAV | pyminiaudio | pedalboard | torchcodec | librosa | audioread |
|---|---|---|---|---|---|---|---|---|---|---|---|
| License | MIT | MIT | MIT | MIT | BSD-3 (libsndfile inside is LGPL) | BSD-3 (FFmpeg inside has its own license) | MIT | GPLv3 | BSD-3 | ISC | MIT |
| Latest release | not on PyPI yet, 0.1.0 in the repo | 0.25.1, 2021-03-10 | 0.26.0.post5, 2026-11-15 | 0.2.0, 2025-03-13 | 0.14.0, 2026-06-06 | 19.0.2, 2026-10-21 | 1.73, 2026-11-10 | 0.9.26, 2026-10-07 | 0.17.0, 2026-09-30 | 1.0.1, 2026-11-12 | 3.1.0, 2025-10-26 |
| Wheel size | 3 MB | 32 KB | 43 KB | 37 KB | 1.1 to 1.3 MB | 19 to 46 MB | 0.3 to 0.7 MB | 2.5 to 6.1 MB | 4 to 10 MB, without FFmpeg and without torch | 0.3 MB, but it pulls in numba, scipy, scikit-learn and more | 23 KB |
| Needs ffmpeg installed | for m4a/aac, wma, aiff and video, and for mp3 writing unless you add `pydub2-mp3` | for everything except wav, plus ffprobe | same as pydub | same as pydub | no | no, FFmpeg is in the wheel | no | no | yes, the shared FFmpeg libraries, for all decoding and encoding | no | one of several backends, ffmpeg is one of them |
| AudioSegment API | yes, pydub's own test suite runs against it | it is the original | yes, a fork | yes, a fork | no | no | no | no | no | no | no |
| Read with nothing else installed | wav, flac, ogg, opus, mp3 | wav | wav | wav | wav, flac, ogg, opus, mp3, aiff and more | wav, flac, ogg, opus, mp3, m4a, aiff, mp4 | wav, flac, vorbis, mp3 | wav, flac, ogg, opus, mp3, aiff, m4a (m4a through the system codecs on macOS) | none, needs FFmpeg | what soundfile reads | wave, aifc and sunau from the standard library, Core Audio on macOS |
| Write with nothing else installed | wav, flac, ogg, opus; mp3 after `pip install pydub2[mp3]` | wav | wav | wav | wav, flac, ogg, opus (8, 12, 16, 24 and 48 kHz only), mp3 | wav, flac, mp3, aac, opus; vorbis only with the experimental encoder | wav | wav, flac, ogg, mp3 | none, needs FFmpeg | nothing, it only reads | nothing, it only reads |
| Long files in pieces | `AudioSegment.stream` for wav, flac, mp3, ogg, opus | no, the whole file is decoded | no | no | `blocks()` | frame by frame | `stream_file` | read in chunks | decode a sample range | `librosa.stream` | yes, it yields buffers |
| Python 3.13 and 3.14 | yes, run on 3.14 and 3.15 | import fails on 3.13 and newer unless `audioop-lts` is installed | pulls in `audioop-lts`, run on 3.14 | pulls in `audioop-lts`, run on 3.14 | yes, run on 3.14 | yes, run on 3.14, needs 3.12 or newer | yes, 3.11 or newer | yes, run on 3.14 | 3.10 to 3.14 in the README | 3.12 or newer, 3.13 and 3.14 listed | 3.9 or newer, adds `standard-aifc` on 3.13 |
| Free threaded builds | yes, 8 threads decoding on 3.15t in my run | pure Python, never tried | not tried | not tried | pure Python plus cffi, the README has a section on threads | wheels for 3.14t | wheels for 3.14t | wheels for 3.14t on some platforms | wheels for 3.14t | not declared | not declared |
| numpy in and out | `to_numpy()`, `from_numpy()`, numpy is not required to install | `array.array`, numpy by hand | same | same | numpy arrays, numpy is required | frame `to_ndarray()` | `array.array` | float32 arrays | PyTorch tensors | float32 arrays | raw bytes |
| Loudness (LUFS) | `loudness()` and `normalize(target_lufs=)` | dBFS and rms only | same | same | no | only by building an ffmpeg `ebur128` filter graph | no | no | no | no | no |
| Speech detection | `detect_speech`, `split_on_speech` | silence only | silence only | silence only | no | no | no | no | no | `effects.split`, which is silence based | no |

A few notes on cells that need one.

The pydub2 wheel is 3.0 MB on macOS arm64 and 3.2 MB on Windows. The mp3 writer is a separate wheel of about 130 KB because LAME is LGPL.

pydub2 reads aiff only through ffmpeg. That is the same as pydub.

For free threaded builds I could only run pydub2 itself. I built it for a free threaded 3.15 and ran eight threads that each decoded an mp3 and faded a flac. The GIL stayed off and every thread got the same answer. The cells for other libraries only say what their wheels and READMEs offer.

Speech detection in pydub2 uses the Silero VAD model, which is built into the extension. You do not need onnxruntime or torch.

## When another library is the better choice

Use soundfile when you want arrays in and out of wav, flac, ogg or mp3 files. It gives you float arrays, reads blocks of a file, and has been around much longer than pydub2. pydub2 gives you integer samples and an editing API on top, which you do not need if all you do is load and save.

Use PyAV when you need video, or every codec and container ffmpeg has, or ffmpeg filters, or control over packets and frames. Its wheel carries FFmpeg, so you install nothing else. It is a low level API. Cutting a file in half is more code than in pydub2.

Use pedalboard for effects, reverb, compressors, plugins (VST3 and Audio Units) and pitch shifting, if GPLv3 is fine for your project. The README lists a Rubber Band based `PitchShift` and `time_stretch`. pydub2 has none of that.

Use torchcodec when the audio ends up in a PyTorch pipeline. It returns tensors, can decode on a GPU, and can decode a range of samples without reading the rest. You have to install FFmpeg yourself, with its shared libraries, and torch.

Use librosa when you want analysis, such as spectrograms, beats and pitch. Version 1.0 loads files only through soundfile. You can read with pydub2 and pass `to_numpy()` to librosa if you like.

Use pyminiaudio when you want to play or record sound. It is a small wheel with MIT code.

Use pydub 0.25.1, or one of its forks, if your code works today, you only touch wav, and you stay on Python 3.12 or older. Nothing forces you to move. On 3.13 and newer you need `audioop-lts`, and pydub has had no release since 2021.

## What pydub2 does not do yet

It is not on PyPI yet. Until the first release you install it from GitHub. I develop it on macOS arm64. The tests run on Linux for every push, and on Windows and macOS when I build the wheels. I have not tried it on a real Windows machine.

m4a and aac, wma, aiff and video files still go through ffmpeg. pydub2 can export m4a and aac when ffmpeg is there. That fails in pydub 0.25.1.

Writing mp3 needs the `pydub2-mp3` package, or ffmpeg.

There is no pitch shift and no tempo change that keeps the pitch. `speedup` is pydub's algorithm. `set_frame_rate` changes both speed and pitch.

`play()` is pydub's. It waits until the audio is done and needs simpleaudio, PyAudio or ffplay. You cannot stop it from another thread.

Tags of mp3 files and the stream info of formats pydub2 does not read itself need ffprobe. Tags of wav, flac, ogg and opus files work without it.

## Links

- PyPI: [pydub](https://pypi.org/project/pydub/), [pydub-lts](https://pypi.org/project/pydub-lts/), [pydub-ng](https://pypi.org/project/pydub-ng/), [soundfile](https://pypi.org/project/soundfile/), [av](https://pypi.org/project/av/), [miniaudio](https://pypi.org/project/miniaudio/), [pedalboard](https://pypi.org/project/pedalboard/), [torchcodec](https://pypi.org/project/torchcodec/), [librosa](https://pypi.org/project/librosa/), [audioread](https://pypi.org/project/audioread/)
- READMEs: [soundfile](https://github.com/bastibe/python-soundfile), [PyAV](https://github.com/PyAV-Org/PyAV), [pyminiaudio](https://github.com/irmen/pyminiaudio), [pedalboard](https://github.com/spotify/pedalboard), [torchcodec](https://github.com/meta-pytorch/torchcodec), [audioread](https://github.com/beetbox/audioread)
- librosa dropped its audioread backend in 1.0: [changelog](https://github.com/librosa/librosa/blob/main/docs/changelog.rst)
- What differs between pydub2 and pydub: [docs/differences.md](differences.md)
