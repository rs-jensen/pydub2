import os

strict = os.environ.get("PYDUB2_STRICT", "0") not in ("", "0")
max_duration = None
ffmpeg_timeout = None

from .audio_segment import AudioSegment

__version__ = "0.0.1"
