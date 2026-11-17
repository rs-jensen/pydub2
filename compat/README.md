# pydub2-compat

Optional add-on for [pydub2](https://pypi.org/project/pydub2/). Once it is installed, `import pydub` gives you pydub2.
Most packages write `import pydub` or `from pydub import AudioSegment`. This makes those lines
load pydub2 without touching the code of those packages. `pydub.AudioSegment is pydub2.AudioSegment`,
and the same for `pydub.utils`, `silence`, `effects`, `generators`, `playback`, `exceptions`,
`audio_segment`, `scipy_effects` and `logging_utils`.

It is opt in. pydub2 alone never takes over the name `pydub`. Install it with `pip install pydub2-compat`.
The real pydub can stay installed, gradio and others depend on it and pip will keep installing it.
No files go into a `pydub/` folder. The package is one `.pth` file and one small import hook.
pydub2 is only imported when something imports `pydub`.

To turn it off set `PYDUB2_COMPAT=0` in the environment, or `pip uninstall pydub2-compat`.
If pydub2 fails to import you get a one line warning and the real pydub is used.

Things to know:

- It needs the `.pth` file to be read, so it does not work with `python -S`. With `pip install --user`
  it does not work under `python -I`. Normal installs and venvs are fine.
- `pip list` and `importlib.metadata.version("pydub")` still report the real pydub if it is installed.
- `pydub.pyaudioop` and `pydub.audioop` do not exist and raise ImportError, because pydub2 does not need audioop.
- `python -m pydub.utils` and the like stop with "No code object available".
- Do not run tests that compare against pydub with this installed, they would test pydub2 against itself.

MIT, see [LICENSE](https://github.com/rs-jensen/pydub2/blob/main/compat/LICENSE). The project is at [github.com/rs-jensen/pydub2](https://github.com/rs-jensen/pydub2).
