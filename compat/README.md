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
- Without the real pydub, `importlib.metadata.version("pydub")` raises `PackageNotFoundError` and `pip show pydub` finds nothing.
- pip does not know that this stands in for pydub. A package that requires pydub makes pip install the real pydub, and `pip check` complains about it while only pydub2-compat is installed.
- When the real pydub is installed too, `import pydub` still gives pydub2, because the hook runs before the normal lookup. The real pydub is used when pydub2 cannot be imported or `PYDUB2_COMPAT=0` is set.
- `importlib.util.find_spec("pydub")` imports pydub2 to see that it works.
- A pickle written by pydub loads as a pydub2 `AudioSegment`. A pickle written by pydub2 names `pydub2` and the real pydub cannot read it.
- `pydub.pyaudioop` and `pydub.audioop` do not exist and raise ImportError, because pydub2 does not need audioop.
- `python -m pydub.utils` and the like stop with "No code object available".
- Do not run tests that compare against pydub with this installed, they would test pydub2 against itself.

MIT, see [LICENSE](https://github.com/rs-jensen/pydub2/blob/main/compat/LICENSE). The project is at [github.com/rs-jensen/pydub2](https://github.com/rs-jensen/pydub2).
