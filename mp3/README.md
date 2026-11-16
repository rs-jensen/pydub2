# pydub2-mp3

The LAME mp3 encoder for pydub2. With it installed, `export(format="mp3")` works without ffmpeg.

    pip install pydub2-mp3

The wheel holds only `libmp3lame` (LAME 3.100, encoder only) and has no Python extension, so one wheel per platform covers every Python version. pydub2 loads it with ctypes. LAME is LGPL, which is why it lives in its own package and pydub2 itself stays MIT. See LICENSE.

`pydub2_mp3.path()` gives the path to the library.
