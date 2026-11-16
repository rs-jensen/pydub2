# pydub2-mp3

The LAME mp3 encoder for [pydub2](https://pypi.org/project/pydub2/). With it installed, `export(format="mp3")` works without ffmpeg.

    pip install pydub2-mp3

`pip install "pydub2[mp3]"` does the same.

The wheel holds only `libmp3lame` (LAME 3.100, encoder only) and has no Python extension, so one wheel per platform covers every Python version. pydub2 loads it with ctypes. LAME is LGPL, which is why it lives in its own package and pydub2 itself stays MIT. See [LICENSE](https://github.com/rs-jensen/pydub2/blob/main/mp3/LICENSE). The project is at [github.com/rs-jensen/pydub2](https://github.com/rs-jensen/pydub2).

`pydub2_mp3.path()` gives the path to the library.
