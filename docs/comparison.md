# pydub2 compared with other audio libraries

I wrote pydub2 to replace pydub. This page puts it next to the libraries people use for the same jobs, and says where each of them is the better pick.

I checked everything on 2026-11-16. Versions and dates come from PyPI that day. For the format and Python rows I installed the wheels from PyPI on macOS arm64 with Python 3.14, took ffmpeg off `PATH`, and tried to read and write a three second file in each format. I did not install torchcodec, librosa or audioread, so their cells come from their READMEs, changelogs and PyPI metadata. Links are at the end.

## The facts

| | pydub2 | pydub 0.25.1 | pydub-lts | pydub-ng | soundfile | PyAV | pyminiaudio | pedalboard | torchcodec | librosa | audioread |
|---|---|---|---|---|---|---|---|---|---|---|---|
| License | MIT | MIT | MIT | MIT | BSD-3 (libsndfile inside is LGPL) | BSD-3 (FFmpeg inside has its own license) | MIT | GPLv3 | BSD-3 | ISC | MIT |
| Latest release | 0.0.1 | 0.25.1, 2021-03-10 | 0.26.0.post5, 2026-11-15 | 0.2.0, 2025-03-13 | 0.14.0, 2026-06-06 | 19.0.2, 2026-10-21 | 1.73, 2026-11-10 | 0.9.26, 2026-10-07 | 0.17.0, 2026-09-30 | 1.0.1, 2026-11-12 | 3.1.0, 2025-10-26 |
| Wheel size | 3 MB | 32 KB | 43 KB | 37 KB | 1.1 to 1.3 MB | 19 to 46 MB | 0.3 to 0.7 MB | 2.5 to 6.1 MB | 4 to 10 MB, without FFmpeg and without torch | 0.3 MB, but it pulls in numba, scipy, scikit-learn and more | 23 KB |
| Needs ffmpeg installed | for m4a/aac, wma, aiff and video, and for mp3 writing unless you add `pydub2-mp3` | for everything except wav, plus ffprobe | same as pydub | same as pydub | no | no, FFmpeg is in the wheel | no | no | yes, the shared FFmpeg libraries, for all decoding and encoding | no | one of several backends, ffmpeg is one of them |
| AudioSegment API | yes, pydub's own test suite runs against it | it is the original | yes, a fork | yes, a fork | no | no | no | no | no | no | no |
| Read with nothing else installed | wav, flac, ogg, opus, mp3 | wav | wav | wav | wav, flac, ogg, opus, mp3, aiff and more | wav, flac, ogg, opus, mp3, m4a, aiff, mp4 | wav, flac, vorbis, mp3 | wav, flac, ogg, opus, mp3, aiff, m4a (m4a through the system codecs on macOS) | none, needs FFmpeg | what soundfile reads | wave, aifc and sunau from the standard library, Core Audio on macOS |
| Write with nothing else installed | wav, flac, ogg, opus; mp3 after `pip install "pydub2[mp3]"` | wav | wav | wav | wav, flac, ogg, opus (8, 12, 16, 24 and 48 kHz only), mp3 | wav, flac, mp3, aac, opus; vorbis only with the experimental encoder | wav | wav, flac, ogg, mp3 | none, needs FFmpeg | nothing, it only reads | nothing, it only reads |
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

## Speed and size

I timed the libraries on an Apple M2 Pro (macOS 15.7.9, Python 3.13.1, ffmpeg 7.1.1) with pydub 0.25.1, pydub2 0.0.1, soundfile 0.14.0, PyAV 19.0.2 and miniaudio 1.73. pydub-lts is within a few percent of pydub in every row, so it is left out. The inputs are generated 44.1 kHz stereo files, speech-like and music-like. Each number is the median of at least 5 runs, each in a fresh process. The machine was not idle, so read differences under 15 percent as a tie. The method, the full table with CPU time for every case and the exact versions are in [bench/README.md](../bench/README.md).

Seconds of wall time on a 10 minute speech file, lower is better. The music file gives the same picture for loading.

| | pydub | pydub2 | soundfile | PyAV | miniaudio |
|---|---|---|---|---|---|
| import, milliseconds | 4.9 | 7.5 | 36 | 81 | 49 |
| load wav | 0.061 | 0.051 | 0.054 | 0.22 | 0.081 |
| load flac | 0.32 | 0.32 | 0.52 | 0.65 | 0.39 |
| load mp3 | 0.76 | 0.40 | 0.53 | 0.71 | 0.31 |
| load ogg | 0.86 | 0.54 | 0.79 | 0.84 | 0.52 |
| load opus | 1.4 | 0.96 | 3.3 | 1.2 | not supported |
| 10 s out of an mp3 | 0.40 | 0.045 | 0.055 | 0.13 | 0.17 |
| 10 s out of an ogg | 0.39 | 0.50 | 0.049 | 0.12 | 0.063 |
| export flac | 1.1 | 0.67 | 0.75 | 0.87 | not supported |
| export ogg | 5.8 | 4.7 | crashed | not supported | not supported |
| export opus | 4.9 | 6.2 | not supported | 20 | not supported |
| export mp3 | 6.5 | 5.8 with `pydub2-mp3`, 6.5 without | 5.2 | 5.6 | not supported |
| peak memory, load flac, MiB | 393 | 133 | 131 | 250 | 301 |
| peak memory, load ogg, MiB | 677 | 237 | 131 | 262 | 301 |
| peak memory, load opus, MiB | 731 | 253 | 140 | 283 | not supported |

pydub exports mp3 through ffmpeg, and so does pydub2 when `pydub2-mp3` is not installed. soundfile cannot write opus at 44.1 kHz and the PyAV wheel has no vorbis encoder. soundfile crashed the process with a segmentation fault every time I wrote 10 minutes of stereo to ogg. It wrote 60 seconds fine.

The editing functions only exist in the pydub family. Seconds on the 10 minute file unless it says otherwise:

| | pydub | pydub2 |
|---|---|---|
| `set_frame_rate(16000)` | 0.39 (PyAV 0.32, miniaudio 0.20) | 0.21 |
| `overlay` | 0.056 | 0.054 |
| `normalize` | 0.099 | 0.090 |
| `compress_dynamic_range` | 69 | 0.56 |
| `low_pass_filter` | 7.3 | 0.17 |
| `detect_silence` | 70 | 0.14 |
| `split_on_silence` | 70 | 0.15 |
| `seg += chunk`, 1000 times, 60 s file | 1.05, peak 1175 MiB | 0.005, peak 30 MiB |
| mp3 file to numpy, 60 s | 0.16 | 0.039 with `np.array(get_array_of_samples())`, 0.065 with `to_numpy()` |
| numpy to wav file | 0.025 | 0.026 |
| numpy to mp3 file | 6.5 | 5.8 with `pydub2-mp3`, 6.5 without |
| `detect_speech` | does not exist | 1.0, peak 106 MiB |

Where pydub2 is slower or heavier than something else in these runs:

- `overlay` and `normalize` take as long as pydub, which uses the C `audioop`, or a little less. Both are under 0.1 seconds for 10 minutes.
- Importing it takes 7.5 ms against 4.9 for pydub. soundfile, PyAV and miniaudio take longer than pydub2.
- Taking 10 seconds out of an ogg file takes 0.50 seconds for a 10 minute file and 0.26 for a 1 minute file, because pydub2 decodes from the start. soundfile needs 0.05, miniaudio 0.06 and PyAV 0.12. pydub needs 0.39, so this is the one row where pydub2 reads slower than pydub. For mp3 and flac the 10 second read takes 0.04 to 0.05 seconds.
- Opus export takes 6.2 seconds for 10 minutes, a little more than ffmpeg through pydub (4.9). It is faster than PyAV (20). This is measured on Apple silicon, where the resampler inside the opus encoder has a neon version.
- mp3 export with `pydub2-mp3` is about 10 percent behind soundfile (5.8 against 5.2) and 3 percent behind PyAV.
- Reading mp3 and ogg is a little slower than miniaudio (0.40 against 0.31 for mp3, 0.55 against 0.52 for ogg).
- Memory: decoding 10 minutes of ogg or opus peaks at 237 to 253 MiB for pydub2 against 131 to 140 for soundfile and 262 to 283 for PyAV. Flac is 133 against 131. Exporting 10 minutes of ogg or opus peaks at about 360 MiB against 164 for pydub.
- `to_numpy()` is as fast as `np.array(seg.get_array_of_samples())` or faster (0.037 against 0.037 seconds for a 60 second mp3, 0.003 against 0.004 for wav). An earlier run looked slower because it timed the import of numpy.

Size on macOS arm64 with Python 3.13, without numpy:

| | wheel | installed | needs ffmpeg installed |
|---|---|---|---|
| pydub | 0.06 MB | 0.4 MB, with `audioop-lts` | for everything except wav |
| pydub2 | 3.0 MB | 4.5 MB | for m4a, aac, wma, aiff and video, and for mp3 writing unless `pydub2-mp3` (0.13 MB more) is installed |
| soundfile | 1.1 MB | 5.2 MB, 38 MB with the numpy it requires | no |
| PyAV | 18.6 MB | 47 MB | no |
| miniaudio | 0.39 MB | 2.8 MB | no |

pydub2 is 3 MB because the codecs are in the wheel. PyAV is 18.6 MB because FFmpeg is.

## When another library is the better choice

Use soundfile when you want arrays in and out of wav, flac, ogg or mp3 files. It gives you float arrays, reads blocks of a file, and has been around much longer than pydub2. pydub2 gives you integer samples and an editing API on top, which you do not need if all you do is load and save.

Use PyAV when you need video, or every codec and container ffmpeg has, or ffmpeg filters, or control over packets and frames. Its wheel carries FFmpeg, so you install nothing else. It is a low level API. Cutting a file in half is more code than in pydub2.

Use pedalboard for effects, reverb, compressors, plugins (VST3 and Audio Units) and pitch shifting, if GPLv3 is fine for your project. The README lists a Rubber Band based `PitchShift` and `time_stretch`. pydub2 has none of that.

Use torchcodec when the audio ends up in a PyTorch pipeline. It returns tensors, can decode on a GPU, and can decode a range of samples without reading the rest. You have to install FFmpeg yourself, with its shared libraries, and torch.

Use librosa when you want analysis, such as spectrograms, beats and pitch. Version 1.0 loads files only through soundfile. You can read with pydub2 and pass `to_numpy()` to librosa if you like.

Use pyminiaudio when you want to play or record sound. It is a small wheel with MIT code.

Use pydub 0.25.1, or one of its forks, if your code works today, you only touch wav, and you stay on Python 3.12 or older. Nothing forces you to move. On 3.13 and newer you need `audioop-lts`, and pydub has had no release since 2021.

## What pydub2 does not do yet

I develop it on macOS arm64. The tests run on Linux for every push, and on Windows and macOS when I build the wheels. I have not tried it on a real Windows machine.

m4a and aac, wma, aiff and video files still go through ffmpeg. pydub2 can export m4a and aac when ffmpeg is there. That fails in pydub 0.25.1.

Writing mp3 needs the `pydub2-mp3` package, or ffmpeg.

There is no pitch shift and no tempo change that keeps the pitch. `speedup` is pydub's algorithm. `set_frame_rate` changes both speed and pitch.

`play()` is pydub's. It waits until the audio is done and needs simpleaudio, PyAudio or ffplay. You cannot stop it from another thread.

For formats pydub2 does not read itself, `mediainfo` needs ffprobe to give the full stream info. With only ffmpeg you get the codec and sample size. Tags of wav, flac, ogg, opus and mp3 files work without ffprobe.

## Links

- PyPI: [pydub](https://pypi.org/project/pydub/), [pydub-lts](https://pypi.org/project/pydub-lts/), [pydub-ng](https://pypi.org/project/pydub-ng/), [soundfile](https://pypi.org/project/soundfile/), [av](https://pypi.org/project/av/), [miniaudio](https://pypi.org/project/miniaudio/), [pedalboard](https://pypi.org/project/pedalboard/), [torchcodec](https://pypi.org/project/torchcodec/), [librosa](https://pypi.org/project/librosa/), [audioread](https://pypi.org/project/audioread/)
- READMEs: [soundfile](https://github.com/bastibe/python-soundfile), [PyAV](https://github.com/PyAV-Org/PyAV), [pyminiaudio](https://github.com/irmen/pyminiaudio), [pedalboard](https://github.com/spotify/pedalboard), [torchcodec](https://github.com/meta-pytorch/torchcodec), [audioread](https://github.com/beetbox/audioread)
- librosa dropped its audioread backend in 1.0: [changelog](https://github.com/librosa/librosa/blob/main/docs/changelog.rst)
- What differs between pydub2 and pydub: [docs/differences.md](differences.md)
