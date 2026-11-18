# Benchmarks

Numbers for pydub2 next to pydub, pydub-lts, soundfile, PyAV and pyminiaudio. `bench.py` makes the test audio, runs every case in a fresh process and prints the table below.

## What was measured

Run on 2026-11-16 on an Apple M2 Pro (10 cores), macOS 15.7.9, Python 3.13.1 (python.org build), system ffmpeg 7.1.1 from Homebrew. Each library lives in its own venv with numpy 2.5.5, which only the harness uses.

| name in the table | installed |
|---|---|
| pydub | pydub 0.25.1 and audioop-lts 0.2.2 |
| pydublts | pydub-lts 0.26.0.post5 |
| pydub2 | pydub2 0.0.1 built with `pip wheel` from commit 580d4c4 |
| pydub2mp3 | the same and pydub2-mp3 0.0.1, only used where mp3 is written |
| sf | soundfile 0.14.0 |
| av | PyAV 19.0.2 |
| mini | miniaudio 1.73 |

I left out librosa and silero-vad. They need torch or numba and scipy, which is a lot of disk for a speed comparison.

The inputs are generated, so nobody has to download anything. `speech` is made of voiced syllables with pauses and a little noise. `music` is chords, a bass drum and short noise hits. Both are 44.1 kHz stereo, 60 seconds and 10 minutes, as wav and encoded with ffmpeg to flac, mp3 (128k), ogg (vorbis q4) and opus (96k). The 16 kHz mono wav used for `detect_speech` is made from the speech file.

Every number is the median of 5 runs, each in a new Python process. If the runs of a case differ by more than 15 percent ((max - min) / median) the case gets 5 more runs, twice at most, and the median is taken over all of them. A cell that is still over 15 percent after that has a `*`. Cases whose first run takes more than 30 seconds get 3 runs and a `*`. All the 3 run cells here are pydub's `detect_silence`, `split_on_silence` and `compress_dynamic_range` on 10 minutes, and they did not move by more than 4 percent.

Each cell is `wall seconds / cpu seconds, peak MiB`. Peak is the highest resident size of the process and its children, so it includes the interpreter and numpy, about 20 to 40 MiB. The last column is the median 1 minute load average at the start of that row's runs. The machine was not idle: a browser was open, and the load average over all runs was 2.3 at the lowest, 3.4 in the median and 4.3 at the highest. Every run has its own load value in the raw output. 32 of the 313 cells carry a `*`, nearly all of them under 0.2 seconds (import, exporting wav, small copies). Differences of a few milliseconds in those rows mean nothing. The first import after installing a package was slow (up to 8 seconds for PyAV, macOS checking new binaries) and is in the repeats, which the median ignores.

The pydub2 cells of `dsp overlay` and `dsp normalize`, of the 10 minute `load` rows for flac, ogg and opus, of `gradio_in` and `gradio_in_fast` for 10 minute flac and of `export opus` were measured again on 2026-11-17 with the same method, after the changes that made them faster or smaller. The load average was 6 to 8 then, higher than for the rest of the table, so those times are if anything too slow. The other `gradio_in_fast` cells are from the first run, which timed the import of numpy, about 0.03 seconds. A later run on the 60 second files gave 0.037 seconds for mp3 and 0.003 for wav, against 0.037 and 0.004 with `np.array`.

When two libraries ran the same case, the harness compared sample counts, sample rate and mean level of the result. They agree within 3 percent everywhere, so the cases do the same work.

The timed part of each case is only the call named in the row. Reading the input and building the `AudioSegment` is not counted for the effects and the silence functions. `gradio_in` is `from_file`, then numpy samples (`np.array(seg.get_array_of_samples())` for the pydubs, shown in a second row `gradio_in_fast` with `to_numpy()` for pydub2). `gradio_out` is numpy bytes to an `AudioSegment` and `export`. The mp3 export of pydub and pydub2 goes through ffmpeg, as it does for every user without `pydub2-mp3`. `n/a` means the library cannot do it. `fail` is a crash: soundfile segfaults on every try to write 10 minutes of stereo to ogg here.

## Rerun it

    pip wheel --no-deps -w wheels .
    pip wheel --no-deps -w wheels mp3
    cd bench
    for v in pydub pydublts pydub2 pydub2mp3 sf av mini; do python3.13 -m venv venvs/$v; venvs/$v/bin/pip install numpy; done
    venvs/pydub/bin/pip install pydub==0.25.1 audioop-lts
    venvs/pydublts/bin/pip install pydub-lts
    venvs/pydub2/bin/pip install ../wheels/pydub2-*.whl
    venvs/pydub2mp3/bin/pip install ../wheels/pydub2-*.whl ../wheels/pydub2_mp3-*.whl
    venvs/sf/bin/pip install soundfile
    venvs/av/bin/pip install av
    venvs/mini/bin/pip install miniaudio
    venvs/sf/bin/python bench.py gen data
    venvs/sf/bin/python bench.py run --venvs venvs --data data --out results.jsonl --quick --runs 5 --slow 30
    venvs/sf/bin/python bench.py report results.jsonl
    venvs/sf/bin/python bench.py footprint venvs ../wheels
    venvs/sf/bin/python bench.py noffmpeg venvs data

The first two lines run in the repo root and build the wheels. `bench.py` needs ffmpeg on `PATH` for `gen`. Without `--quick` it also runs 5 second files, `speedup`, `fade`, `dBFS` and a high quality resample, and wants librosa, audioread and silero-vad venvs. The run above took about 45 minutes. `gen` writes about 0.4 GB, the venvs about 0.4 GB. Run only one case with `--only "load speech_10m"`, only one library with `--libs pydub2`. Results are appended to the `--out` file and a rerun skips what is already in it.

## Size and ffmpeg

Sizes are without numpy. Installed size is every file the package and its dependencies put in site-packages. Wheel size is for macOS arm64 and Python 3.13.

| | wheel | installed | pulls in |
|---|---|---|---|
| pydub | 0.06 MB | 0.4 MB | audioop-lts |
| pydub-lts | 0.04 MB | 0.4 MB | audioop-lts |
| pydub2 | 3.04 MB | 4.5 MB | nothing |
| pydub2 and pydub2-mp3 | 3.18 MB | 4.8 MB | nothing |
| soundfile | 1.10 MB | 5.2 MB (37.8 with numpy) | numpy, cffi |
| PyAV | 18.56 MB | 47.0 MB | nothing |
| miniaudio | 0.39 MB | 2.8 MB | cffi |

What works with ffmpeg removed from `PATH` (tried on a 5 second file, `yes` means it ran and wrote a file):

| lib | read wav | read flac | read mp3 | read ogg | read opus | write wav | write flac | write mp3 | write ogg | write opus |
|---|---|---|---|---|---|---|---|---|---|---|
| pydub | yes | no | no | no | no | yes | no | no | no | no |
| pydublts | yes | no | no | no | no | yes | no | no | no | no |
| pydub2 | yes | yes | yes | yes | yes | yes | yes | no | yes | yes |
| pydub2mp3 | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| sf | yes | yes | yes | yes | yes | yes | yes | yes | yes | n/a |
| av | yes | yes | yes | yes | yes | yes | yes | yes | n/a | yes |
| mini | yes | yes | yes | yes | n/a | yes | n/a | n/a | n/a | n/a |

For pydub2 every effect, resample, silence and speech function runs without ffmpeg. It is needed for m4a, aac, wma, aiff and video files, and to write mp3 unless `pydub2-mp3` is installed. pydub needs ffmpeg for everything except wav. soundfile cannot write opus at 44.1 kHz and PyAV's wheel has no vorbis encoder, so those two are `n/a`.

## Full table

Rows are named `case input`. `load` reads the whole file, `load10` reads 10 seconds from the middle, `export` writes the speech wav to the format named. 60s and 10m are the length of the input.

| case | pydub | pydublts | pydub2 | pydub2mp3 | sf | av | mini | load |
|---|---|---|---|---|---|---|---|---|
| import | 0.00493/0.00494s 19M* | 0.005/0.005s 20M | 0.00751/0.00751s 20M | 0.00739/0.0074s 20M* | 0.0361/0.0361s 30M* | 0.0805/0.0805s 36M* | 0.0492/0.0492s 35M* | 3 |
| dsp overlay speech_60s_s44.wav | 0.00636/0.00634s 80M | 0.00649/0.0065s 80M | 0.00738/0.00728s 83M |  |  |  |  | 3 |
| dsp normalize speech_60s_s44.wav | 0.0104/0.0104s 40M | 0.00961/0.00961s 39M* | 0.00878/0.00867s 43M |  |  |  |  | 3 |
| dsp compress speech_60s_s44.wav | 6.91/6.91s 391M | 6.95/6.95s 392M | 0.0574/0.0574s 41M |  |  |  |  | 3 |
| dsp lowpass speech_60s_s44.wav | 0.71/0.71s 60M | 0.731/0.729s 60M | 0.017/0.017s 40M |  |  |  |  | 3 |
| dsp append speech_60s_s44.wav | 1.05/1.05s 1175M | 1.06/1.05s 1164M | 0.00469/0.00469s 30M |  |  |  |  | 3 |
| dsp overlay speech_10m_s44.wav | 0.0562/0.0562s 423M* | 0.0545/0.0545s 423M | 0.0539/0.0529s 426M |  |  |  |  | 3 |
| dsp normalize speech_10m_s44.wav | 0.0994/0.0988s 221M | 0.101/0.101s 221M | 0.0903/0.0895s 224M |  |  |  |  | 3 |
| dsp compress speech_10m_s44.wav | 69.3/69.3s 3667M* | 69.5/69.4s 3679M* | 0.557/0.555s 223M |  |  |  |  | 3 |
| dsp lowpass speech_10m_s44.wav | 7.28/7.27s 423M | 7.25/7.25s 423M | 0.165/0.165s 222M |  |  |  |  | 3 |
| silence detect speech_10m_s44.wav | 70.1/69.9s 126M* | 69.7/69.6s 126M* | 0.139/0.139s 121M |  |  |  |  | 3 |
| silence split speech_10m_s44.wav | 69.7/69.6s 202M* | 69.4/69.3s 203M* | 0.148/0.148s 199M |  |  |  |  | 3 |
| vad speech_10m_m16.wav |  |  | 1.04/1.04s 106M |  |  |  |  | 3 |
| gradio_in speech_60s_s44.wav | 0.00371/0.00371s 60M | 0.00385/0.00386s 60M* | 0.00349/0.00349s 61M |  |  |  |  | 3 |
| gradio_in speech_60s_s44.mp3 | 0.162/0.235s 94M* | 0.163/0.231s 94M | 0.0387/0.0387s 61M |  |  |  |  | 3 |
| gradio_in speech_60s_s44.flac | 0.116/0.2s 98M | 0.115/0.198s 98M | 0.0299/0.0299s 61M |  |  |  |  | 3 |
| gradio_in speech_10m_s44.wav | 0.0453/0.0453s 333M* | 0.0436/0.0436s 333M | 0.0357/0.0357s 333M* |  |  |  |  | 3 |
| gradio_in speech_10m_s44.mp3 | 0.742/1.48s 415M | 0.737/1.49s 422M | 0.373/0.373s 334M |  |  |  |  | 3 |
| gradio_in speech_10m_s44.flac | 0.305/1.15s 493M | 0.308/1.14s 494M | 0.309/0.307s 335M |  |  |  |  | 3 |
| gradio_in_fast speech_60s_s44.wav |  |  | 0.0313/0.0313s 50M |  |  |  |  | 4 |
| gradio_in_fast speech_60s_s44.mp3 |  |  | 0.0654/0.0654s 51M |  |  |  |  | 4 |
| gradio_in_fast speech_60s_s44.flac |  |  | 0.0619/0.0619s 51M |  |  |  |  | 4 |
| gradio_in_fast speech_10m_s44.wav |  |  | 0.0522/0.0522s 232M |  |  |  |  | 4 |
| gradio_in_fast speech_10m_s44.mp3 |  |  | 0.396/0.396s 234M |  |  |  |  | 4 |
| gradio_in_fast speech_10m_s44.flac |  |  | 0.291/0.288s 235M |  |  |  |  | 4 |
| gradio_out wav speech_60s_s44.wav | 0.00254/0.00224s 51M* | 0.00243/0.00232s 51M* | 0.00239/0.00223s 51M |  |  |  |  | 2 |
| gradio_out mp3 speech_60s_s44.wav | 0.709/0.736s 76M | 0.711/0.746s 76M | 0.718/0.801s 86M | 0.578/0.577s 79M* |  |  |  | 2 |
| gradio_out wav speech_10m_s44.wav | 0.0245/0.0238s 232M | 0.0251/0.0237s 232M | 0.026/0.0247s 233M |  |  |  |  | 3 |
| gradio_out mp3 speech_10m_s44.wav | 6.48/6.78s 266M | 6.46/6.79s 267M | 6.45/7.27s 368M | 5.77/5.77s 475M |  |  |  | 3 |
| resample default speech_60s_s44.wav | 0.0394/0.0394s 58M | 0.0391/0.0391s 57M | 0.0209/0.0209s 64M |  |  | 0.0331/0.0331s 75M | 0.0199/0.0199s 49M* | 3 |
| resample default speech_10m_s44.wav | 0.389/0.389s 305M | 0.391/0.39s 305M | 0.207/0.207s 353M |  |  | 0.319/0.319s 326M | 0.201/0.201s 173M | 3 |
| export wav speech_60s_s44.wav | 0.00161/0.00148s 41M* | 0.00167/0.00151s 41M | 0.00152/0.00148s 41M |  | 0.00271/0.00157s 41M* | 0.0841/0.0837s 56M* | 0.00271/0.00262s 66M* | 3 |
| export flac speech_60s_s44.wav | 0.185/0.2s 76M | 0.186/0.2s 75M | 0.0679/0.0676s 57M |  | 0.0752/0.0743s 41M | 0.161/0.16s 64M | n/a | 3 |
| export mp3 speech_60s_s44.wav | 0.717/0.747s 66M | 0.719/0.749s 66M | 0.709/0.785s 76M | 0.576/0.576s 68M | 0.526/0.526s 41M | 0.636/0.635s 59M | n/a | 3 |
| export ogg speech_60s_s44.wav | 0.66/0.748s 65M | 0.644/0.722s 66M | 0.473/0.473s 64M |  | 1.18/1.17s 72M | n/a | n/a | 3 |
| export opus speech_60s_s44.wav | 0.562/0.631s 66M | 0.562/0.631s 66M | 0.614/0.611s 66M |  | n/a | 2.12/2.11s 61M | n/a | 3 |
| export wav speech_10m_s44.wav | 0.0135/0.0131s 132M | 0.0139/0.0131s 131M | 0.0136/0.0134s 132M |  | 0.0149/0.0134s 132M | 0.114/0.107s 148M | 0.0248/0.0242s 339M | 3 |
| export flac speech_10m_s44.wav | 1.08/1.23s 208M | 1.08/1.24s 208M | 0.674/0.672s 277M |  | 0.753/0.75s 132M | 0.871/0.87s 156M | n/a | 3 |
| export mp3 speech_10m_s44.wav | 6.46/6.77s 165M | 6.46/6.8s 165M | 6.46/7.26s 267M | 5.79/5.79s 374M | 5.24/5.22s 132M | 5.63/5.62s 150M | n/a | 3 |
| export ogg speech_10m_s44.wav | 5.82/6.59s 164M | 5.78/6.59s 164M | 4.72/4.71s 356M |  | fail | n/a | n/a | 4 |
| export opus speech_10m_s44.wav | 4.85/5.5s 164M | 4.8/5.48s 164M | 6.17/6.15s 363M |  | n/a | 20.4/20.4s 153M | n/a | 3 |
| load10 speech_60s_s44.wav | 0.0387/0.0387s 52M | 0.0394/0.0394s 52M | 0.0375/0.0373s 32M |  | 0.0372/0.0369s 32M | 0.137/0.134s 50M | 0.0495/0.049s 39M | 3 |
| load10 speech_60s_s44.flac | 0.146/0.187s 59M* | 0.138/0.156s 59M | 0.0413/0.0412s 32M |  | 0.0448/0.0448s 33M | 0.119/0.119s 49M | 0.0555/0.0555s 39M | 3 |
| load10 speech_60s_s44.mp3 | 0.17/0.192s 58M | 0.15/0.163s 58M | 0.042/0.042s 32M |  | 0.0454/0.0454s 32M | 0.123/0.123s 50M | 0.0639/0.0639s 39M | 3 |
| load10 speech_60s_s44.ogg | 0.167/0.201s 63M | 0.149/0.172s 64M | 0.264/0.299s 67M |  | 0.0474/0.0474s 32M | 0.119/0.119s 50M | 0.063/0.0629s 39M | 3 |
| load10 speech_60s_s44.opus | 0.205/0.233s 64M | 0.205/0.23s 64M | 0.0553/0.0553s 34M |  | 0.0939/0.0939s 32M | 0.128/0.128s 50M* | n/a | 3 |
| load10 speech_10m_s44.wav | 0.0631/0.063s 234M | 0.0627/0.0626s 234M | 0.0366/0.0366s 32M |  | 0.0364/0.0364s 32M | 0.134/0.134s 49M | 0.0504/0.0503s 39M* | 3 |
| load10 speech_10m_s44.flac | 0.201/0.526s 60M | 0.142/0.159s 59M | 0.0431/0.043s 32M |  | 0.0437/0.0437s 33M | 0.122/0.122s 50M | 0.0555/0.0555s 39M | 3 |
| load10 speech_10m_s44.mp3 | 0.403/0.515s 58M | 0.151/0.16s 58M | 0.0453/0.0453s 33M |  | 0.0553/0.0553s 32M | 0.126/0.126s 50M | 0.172/0.172s 38M | 3 |
| load10 speech_10m_s44.ogg | 0.388/0.581s 64M | 0.146/0.162s 63M | 0.496/0.694s 68M |  | 0.0489/0.0489s 32M | 0.121/0.121s 51M | 0.0627/0.0627s 39M | 3 |
| load10 speech_10m_s44.opus | 0.705/0.867s 64M | 0.715/0.89s 64M | 0.0564/0.0562s 35M |  | 0.0909/0.0909s 32M | 0.127/0.127s 50M | n/a | 3 |
| load speech_60s_s44.wav | 0.0388/0.0388s 50M | 0.038/0.038s 50M | 0.039/0.039s 41M* |  | 0.0369/0.0368s 41M | 0.142/0.142s 66M | 0.0514/0.0514s 63M | 3 |
| load speech_60s_s44.flac | 0.155/0.242s 88M | 0.155/0.24s 88M | 0.0654/0.0652s 41M |  | 0.0846/0.0846s 40M | 0.166/0.166s 66M | 0.087/0.0869s 64M | 4 |
| load speech_60s_s44.mp3 | 0.194/0.269s 84M | 0.193/0.268s 84M | 0.0704/0.0704s 40M |  | 0.0843/0.0843s 41M | 0.173/0.173s 68M | 0.0781/0.078s 64M | 3 |
| load speech_60s_s44.ogg | 0.205/0.341s 115M | 0.208/0.345s 115M | 0.0851/0.0851s 51M |  | 0.111/0.111s 40M* | 0.182/0.182s 68M | 0.1/0.0994s 64M | 4 |
| load speech_60s_s44.opus | 0.254/0.347s 120M | 0.263/0.351s 121M | 0.131/0.131s 54M |  | 0.364/0.364s 42M | 0.222/0.221s 70M | n/a | 4 |
| load speech_10m_s44.wav | 0.0605/0.0605s 231M | 0.0617/0.0617s 232M | 0.051/0.051s 132M |  | 0.054/0.0531s 132M | 0.216/0.216s 251M | 0.0808/0.0808s 302M | 4 |
| load speech_10m_s44.flac | 0.322/1.16s 393M | 0.318/1.16s 392M | 0.319/0.318s 133M |  | 0.523/0.523s 131M | 0.65/0.65s 250M | 0.388/0.388s 301M | 4 |
| load speech_10m_s44.mp3 | 0.761/1.48s 369M | 0.744/1.5s 369M | 0.397/0.397s 133M |  | 0.526/0.525s 132M | 0.709/0.709s 256M | 0.311/0.311s 301M | 3 |
| load speech_10m_s44.ogg | 0.858/2.28s 677M | 0.864/2.28s 677M | 0.536/0.534s 237M |  | 0.792/0.791s 131M | 0.844/0.844s 262M | 0.524/0.524s 301M | 4 |
| load speech_10m_s44.opus | 1.37/2.29s 731M | 1.38/2.31s 731M | 0.958/0.955s 253M |  | 3.33/3.33s 140M | 1.23/1.22s 283M | n/a | 4 |
| load music_60s_s44.wav | 0.0394/0.0394s 50M | 0.0392/0.0392s 51M* | 0.0403/0.0403s 41M |  | 0.0376/0.0376s 40M | 0.142/0.142s 67M | 0.0545/0.0531s 64M | 4 |
| load music_60s_s44.flac | 0.157/0.245s 88M | 0.158/0.25s 88M | 0.0669/0.0669s 40M |  | 0.0868/0.0868s 41M | 0.165/0.165s 66M | 0.0862/0.0861s 64M | 4 |
| load music_60s_s44.mp3 | 0.193/0.275s 84M | 0.196/0.278s 84M | 0.0748/0.0745s 41M |  | 0.0806/0.0805s 41M | 0.168/0.168s 68M | 0.0763/0.0763s 63M | 4 |
| load music_60s_s44.ogg | 0.207/0.35s 115M | 0.207/0.348s 115M | 0.085/0.0849s 52M |  | 0.103/0.103s 41M | 0.187/0.187s 70M | 0.0973/0.0973s 64M | 4 |
| load music_60s_s44.opus | 0.264/0.357s 120M | 0.262/0.355s 120M | 0.144/0.144s 53M |  | 0.404/0.404s 42M* | 0.229/0.229s 70M | n/a | 4 |
| load music_10m_s44.wav | 0.0616/0.0616s 232M | 0.0616/0.0615s 232M | 0.0522/0.0522s 132M |  | 0.0502/0.0502s 131M | 0.211/0.211s 250M | 0.08/0.0797s 301M | 4 |
| load music_10m_s44.flac | 0.319/1.17s 393M | 0.322/1.17s 393M | 0.336/0.336s 133M |  | 0.554/0.554s 131M | 0.661/0.661s 250M | 0.411/0.41s 302M | 4 |
| load music_10m_s44.mp3 | 0.726/1.46s 369M | 0.724/1.52s 369M | 0.376/0.376s 132M |  | 0.471/0.471s 131M | 0.692/0.692s 256M | 0.3/0.3s 269M | 4 |
| load music_10m_s44.ogg | 0.854/2.28s 676M | 0.86/2.29s 677M | 0.53/0.53s 236M |  | 0.719/0.719s 131M | 0.868/0.867s 274M | 0.516/0.516s 302M | 4 |
| load music_10m_s44.opus | 1.42/2.33s 731M | 1.42/2.32s 731M | 1.1/1.1s 252M |  | 3.66/3.65s 141M | 1.31/1.3s 283M | n/a | 4 |

