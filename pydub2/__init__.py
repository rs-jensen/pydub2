import os
import warnings

_env = os.environ.get("PYDUB2_STRICT", "").strip().lower()
strict = _env in ("1", "true", "yes", "y", "on", "t")
if _env not in ("", "0", "false", "no", "n", "off", "f") and not strict:
    warnings.warn("PYDUB2_STRICT=%r is not a yes or a no, strict mode stays off. Use 1 or 0." % _env, stacklevel=2)
max_duration = None
max_bytes = None
ffmpeg_timeout = None

from .audio_segment import AudioSegment

__version__ = "0.0.2"
